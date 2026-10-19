"""FOCAS bridge: a small JSON API on 127.0.0.1 that cnc-admin calls.

Runs in its own 32-bit ARM container because libfwlib32 only exists for
armv7/x86/x64, while the Pi (and cnc-admin) are 64-bit ARM.

    GET  /status?ip=&port=&timeout=
    GET  /programs?ip=&port=&timeout=
    GET  /upload?ip=&port=&timeout=&number=      -> program text
    POST /download?ip=&port=&timeout=&replace=0|1   body: program
    POST /delete?ip=&port=&timeout=&number=
"""
import hmac
import ipaddress
import json
import logging
import os
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
                    return self._reply(200, focas.status(h))
                if route == ("GET", "/programs"):
                    return self._reply(200, {"programs": focas.directory(h)})
                if route == ("GET", "/upload"):
                    return self._reply(200, focas.upload(h, program_number(q)), "application/octet-stream")
                if route == ("POST", "/delete"):
                    number = program_number(q)
                    self._refuse_if_running(h, number)
                    focas.delete(h, number)
                    return self._reply(200, {"deleted": number})
                data, number = focas.to_cnc_format(body)
                exists = any(p["number"] == number for p in focas.directory(h))
                if exists and q.get("replace") != "1":
                    raise ValueError(f"O{number} already exists on the CNC; tick 'replace' to overwrite it")
                if not exists:
                    focas.download(h, data)
                    return self._reply(200, {"number": number, "bytes": len(data), "replaced": False})
                # Replace: keep a copy, delete, write; put the old program back if writing fails.
                self._refuse_if_running(h, number)
                backup, _ = focas.to_cnc_format(focas.upload(h, number))
                focas.delete(h, number)
                try:
                    focas.download(h, data)
                except Exception as e:
                    try:
                        try:
                            focas.delete(h, number)  # a half-written new program would block the restore
                        except Exception:
                            pass
                        focas.download(h, backup)
                        restored = "the previous version was restored"
                    except Exception as e2:
                        log.error("Restoring O%d failed: %s", number, e2)
                        restored = f"RESTORING THE PREVIOUS VERSION FAILED ({e2}); it is not on the CNC"
                    raise focas.FocasError(f"cnc_download3 (O{number}: {restored})", getattr(e, "code", 0)) from e
                return self._reply(200, {"number": number, "bytes": len(data), "replaced": True})
        except (ValueError, KeyError) as e:  # includes O-numbers out of range
            return self._reply(400, {"error": str(e) or "missing parameter"})
        except focas.FocasError as e:
            return self._reply(502, {"error": str(e), "code": e.code})
        except Exception as e:
            log.exception("FOCAS request failed")
            return self._reply(500, {"error": f"{e.__class__.__name__}: {e}"})

    @staticmethod
    def _refuse_if_running(h, number):
        st = focas.status(h)
        running = st.get("running") or {}
        if running.get("number") == number and st["run"] in ("START", "HOLD", "STOP"):
            raise ValueError(f"O{number} is the active program on the CNC; reset the CNC first")

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
