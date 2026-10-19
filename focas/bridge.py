"""FOCAS bridge: a small JSON API on 127.0.0.1 that cnc-admin calls.

Runs in its own container because libfwlib32 only exists for armv7/x86/x64:
on a Pi (64-bit ARM) that is a 32-bit ARM container.

    GET  /status?ip=&port=&timeout=                    status, model, alarm text, message
    GET  /programs?ip=&port=&timeout=[&folder=]        folder listing, or O-number directory
    GET  /upload?ip=&port=&timeout=&number=|name=      -> program text
    POST /download?ip=&port=&timeout=&replace=0|1[&folder=]   body: program
    POST /delete?ip=&port=&timeout=&number=|name=

"name" and "folder" are full paths in the CNC's own memory ("//CNC_MEM/USER/PATH1/O1234",
"//CNC_MEM/USER/PATH1/"), for controls with program folders.
"""
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import focas

log = logging.getLogger("focas-bridge")
BIND = os.environ.get("FOCAS_BRIDGE_BIND", "127.0.0.1")
PORT = int(os.environ.get("FOCAS_BRIDGE_PORT", "8095"))
MAX_PROGRAM = 4 * 1024 * 1024
TOKEN_FILE = os.environ.get("FOCAS_TOKEN_FILE", "/run/secrets/focas-token")


def load_token():
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    if len(token) < 32:
        raise SystemExit(f"{TOKEN_FILE} is missing or too short")
    return token


TOKEN = None

_machine_locks = {}
_locks_lock = threading.Lock()


def program_number(q):
    """O-number from the query, 1-9999 only: cnc_delete takes a C short, and an
    out-of-range value would silently wrap to a different program."""
    n = int(q["number"])
    if not 1 <= n <= 9999:
        raise ValueError("program number must be 1-9999")
    return n


# Program folders: only the CNC's own memory (not a data server or memory card), plain
# names, no "." or ".." parts. FANUC allows 211 characters for a path.
_PART = r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,31}"
FOLDER_RE = re.compile(rf"//CNC_MEM/(?:{_PART}/)*")
PROGRAM_RE = re.compile(rf"//CNC_MEM/(?:{_PART}/)*{_PART}")


def folder_path(raw):
    if len(raw) > 211 or not FOLDER_RE.fullmatch(raw):
        raise ValueError("folder must be a path in CNC memory like //CNC_MEM/USER/PATH1/")
    return raw


def program_path(raw):
    if len(raw) > 211 or not PROGRAM_RE.fullmatch(raw):
        raise ValueError("program must be a path in CNC memory like //CNC_MEM/USER/PATH1/O1234")
    return raw


def machine_lock(ip):
    """One FOCAS operation per CNC at a time."""
    with _locks_lock:
        return _machine_locks.setdefault(ip, threading.Lock())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _reply(self, code, body, ctype="application/json"):
        data = json.dumps(body).encode() if ctype == "application/json" else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        # Everything on the Pi shares the host network: only cnc-admin knows the token.
        if not hmac.compare_digest(self.headers.get("X-Focas-Token", ""), TOKEN):
            return self._reply(403, {"error": "missing or wrong token"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            ip = str(ipaddress.IPv4Address(q.get("ip", "")))
            port = int(q.get("port", 8193))
            timeout = max(1, min(60, int(q.get("timeout", 10))))
        except ValueError:
            return self._reply(400, {"error": "ip, port and timeout must be valid"})

        route = (method, url.path)
        if route not in {("GET", "/status"), ("GET", "/programs"), ("GET", "/upload"),
                         ("POST", "/download"), ("POST", "/delete")}:
            return self._reply(404, {"error": "unknown endpoint"})

        body = b""
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_PROGRAM:
                return self._reply(413, {"error": "program missing or too large"})
            body = self.rfile.read(length)

        try:
            with machine_lock(ip), focas.connect(ip, port, timeout) as h:
                if route == ("GET", "/status"):
                    return self._reply(200, focas.status(h, details=True))
                if route == ("GET", "/programs"):
                    return self._reply(200, self._programs(h, q))
                if route == ("GET", "/upload"):
                    data = focas.upload_name(h, program_path(q["name"])) if "name" in q \
                        else focas.upload(h, program_number(q))
                    return self._reply(200, data, "application/octet-stream")
                if route == ("POST", "/delete"):
                    if "name" in q:
                        path = program_path(q["name"])
                        self._refuse_if_running(h, path.rsplit("/", 1)[1])
                        focas.delete_name(h, path)
                        return self._reply(200, {"deleted": path})
                    number = program_number(q)
                    self._refuse_if_running(h, number)
                    focas.delete(h, number)
                    return self._reply(200, {"deleted": number})
                return self._reply(200, self._store(h, q, body))
        except (ValueError, KeyError) as e:  # includes O-numbers out of range and bad paths
            return self._reply(400, {"error": str(e) or "missing parameter"})
        except focas.FocasError as e:
            return self._reply(502, {"error": str(e), "code": e.code})
        except Exception as e:
            log.exception("FOCAS request failed")
            return self._reply(500, {"error": f"{e.__class__.__name__}: {e}"})

    @staticmethod
    def _programs(h, q):
        """The requested (or current) program folder; CNCs without folders: the O-number directory."""
        current = focas.current_folder(h)
        if current is None:
            return {"folders": False, "programs": focas.directory(h)}
        path = folder_path(q["folder"]) if q.get("folder") else current
        return {"folders": True, "folder": path, "current": current, "entries": focas.folder(h, path)}

    def _store(self, h, q, body):
        """Write a program; with replace, keep the old one and put it back if writing fails."""
        if q.get("folder"):
            folder = folder_path(q["folder"])
            data, key = focas.to_cnc_format(body, names=True)
            entry = next((e for e in focas.folder(h, folder)
                          if not e["folder"] and focas.same_program(e["name"], key)), None)
            label = entry["name"] if entry else focas.program_label(key)
            exists = entry is not None
            write = lambda d: focas.download_to(h, folder, d)
            read_old = lambda: focas.upload_name(h, folder + label)
            remove = lambda: focas.delete_name(h, folder + label)
            number = key if isinstance(key, int) else None
            running_key = label
        else:
            data, number = focas.to_cnc_format(body)
            label = f"O{number}"
            exists = any(p["number"] == number for p in focas.directory(h))
            write = lambda d: focas.download(h, d)
            read_old = lambda: focas.upload(h, number)
            remove = lambda: focas.delete(h, number)
            running_key = number
        result = {"number": number, "name": label, "bytes": len(data)}
        if exists and q.get("replace") != "1":
            raise ValueError(f"{label} already exists on the CNC; tick 'replace' to overwrite it")
        if not exists:
            write(data)
            return {**result, "replaced": False}
        # Replace: keep a copy, delete, write; put the old program back if writing fails.
        self._refuse_if_running(h, running_key)
        backup, _ = focas.to_cnc_format(read_old(), names=bool(q.get("folder")))
        remove()
        try:
            write(data)
        except Exception as e:
            try:
                try:
                    remove()  # a half-written new program would block the restore
                except Exception:
                    pass
                write(backup)
                restored = "the previous version was restored"
            except Exception as e2:
                log.error("Restoring %s failed: %s", label, e2)
                restored = f"RESTORING THE PREVIOUS VERSION FAILED ({e2}); it is not on the CNC"
            raise focas.FocasError(f"download ({label}: {restored})", getattr(e, "code", 0)) from e
        return {**result, "replaced": True}

    @staticmethod
    def _refuse_if_running(h, program):
        """program: an O-number, or a program file name in a folder ("O0042", "PART-A")."""
        st = focas.status(h)
        running = st.get("running") or {}
        if st["run"] not in ("START", "HOLD", "STOP"):
            return
        if isinstance(program, int):
            active = running.get("number") == program
            label = f"O{program}"
        else:
            name = running.get("name") or ""
            active = name == program or (running.get("number") and focas.same_program(program, running["number"]))
            label = program
        if active:
            raise ValueError(f"{label} is the active program on the CNC; reset the CNC first")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    global TOKEN
    TOKEN = load_token()
    focas.lib()  # fail fast if the library is missing or won't load
    log.info("FOCAS library loaded from %s; listening on %s:%d", focas.LIB_PATH, BIND, PORT)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
