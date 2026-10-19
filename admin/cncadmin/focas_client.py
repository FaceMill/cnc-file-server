"""Client for the focas-bridge container (FANUC FOCAS over Ethernet).

The bridge runs as 32-bit ARM because FANUC's library has no 64-bit ARM build;
it listens on 127.0.0.1 only.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import config

BRIDGE_URL = os.environ.get("FOCAS_BRIDGE_URL", "http://127.0.0.1:8095")


class FocasError(Exception):
    pass


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


def programs(machine):
    return _call(machine, "GET", "/programs")["programs"]


def upload(machine, number):
    return _call(machine, "GET", "/upload", number=number)


def download(machine, data, replace):
    return _call(machine, "POST", "/download", body=data, replace=int(bool(replace)))


def delete(machine, number):
    return _call(machine, "POST", "/delete", number=number)
