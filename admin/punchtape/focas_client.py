"""Client for the focas-bridge container (FANUC FOCAS over Ethernet).

The bridge runs in its own container (32-bit ARM on a Pi: FANUC's library has no
64-bit ARM build); it listens on 127.0.0.1 only.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config

BRIDGE_URL = os.environ.get("FOCAS_BRIDGE_URL", "http://127.0.0.1:8095")


class FocasError(Exception):
    pass


# Same rules as the bridge: paths in the CNC's own memory only, plain names, no "." or "..".
_PART = r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,31}"
FOLDER_RE = re.compile(rf"//CNC_MEM/(?:{_PART}/)*")
PROGRAM_RE = re.compile(rf"//CNC_MEM/(?:{_PART}/)*{_PART}")
# First line of a program: O-number (up to 8 digits with program folders) or <NAME>
HEADER_RE = re.compile(rb"^\s*%?\s*(?:O(\d{1,8})\b|<([A-Za-z0-9_.+-]{1,32})>)")


def folder_path(raw):
    if len(raw) > 211 or not FOLDER_RE.fullmatch(raw):
        raise ValueError("Not a folder in CNC memory (expected e.g. //CNC_MEM/USER/PATH1/).")
    return raw


def program_path(raw):
    if len(raw) > 211 or not PROGRAM_RE.fullmatch(raw):
        raise ValueError("Not a program in CNC memory (expected e.g. //CNC_MEM/USER/PATH1/O1234).")
    return raw


def program_key(data: bytes):
    """O-number (int) or <NAME> (str) from a program's first line, or None."""
    m = HEADER_RE.match(data.replace(b"\r", b"\n").lstrip())
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else m.group(2).decode()


def same_program(name, key):
    """Does a file name in a CNC folder ("O0042", "PART-A") match a program key?"""
    if isinstance(key, int):
        m = re.fullmatch(r"O0*(\d{1,8})", name)
        return bool(m) and int(m.group(1)) == key
    return name == key


def _call(machine, method, path, body=None, **params):
    f = machine["focas"]
    query = urllib.parse.urlencode({"ip": machine["ip"], "port": f["port"], "timeout": f["timeout"], **params})
    req = urllib.request.Request(f"{BRIDGE_URL}{path}?{query}", data=body, method=method,
                                 headers={"X-Focas-Token": config.focas_token()})
    try:
        # A replace can take an upload, a delete, a download and possibly a restore.
        with urllib.request.urlopen(req, timeout=f["timeout"] + 420) as r:
            data = r.read()
            return json.loads(data) if r.headers.get_content_type() == "application/json" else data
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", str(e))
        except ValueError:
            msg = str(e)
        raise FocasError(msg) from None
    except (urllib.error.URLError, OSError) as e:
        raise FocasError(f"FOCAS bridge not reachable ({e}); is the focas-bridge container running?") from None


def status(machine):
    return _call(machine, "GET", "/status")


def programs(machine, folder=None):
    """{"folders": False, "programs": [...]} or {"folders": True, "folder", "current", "entries": [...]}."""
    return _call(machine, "GET", "/programs", **({"folder": folder_path(folder)} if folder else {}))


def upload(machine, number=None, name=None):
    if name:
        return _call(machine, "GET", "/upload", name=program_path(name))
    return _call(machine, "GET", "/upload", number=number)


def download(machine, data, replace, folder=None):
    extra = {"folder": folder_path(folder)} if folder else {}
    return _call(machine, "POST", "/download", body=data, replace=int(bool(replace)), **extra)


def delete(machine, number=None, name=None):
    if name:
        return _call(machine, "POST", "/delete", name=program_path(name))
    return _call(machine, "POST", "/delete", number=number)
