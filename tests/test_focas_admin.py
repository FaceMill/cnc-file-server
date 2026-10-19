"""cnc-admin FOCAS workflow against a fake focas-bridge (simulated CNC memory)."""
import glob, json, os, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from werkzeug.security import generate_password_hash

CNC = {"programs": {1: b"%\nO0001(MAIN)\nM30\n%", 42: b"%\nO0042(OLD)\nM30\n%"}, "active": 1, "run": "RESET",
       "calls": []}


class FakeBridge(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = json.dumps(body).encode() if ctype == "application/json" else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def handle_any(self, method):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))) if method == "POST" else b""
        CNC["calls"].append((method, u.path, q, body))
        if q["ip"] != "192.168.50.31":
            return self.reply(502, {"error": "connect: cannot connect (code -16)", "code": -16})
        if u.path == "/status":
            return self.reply(200, {"mode": "EDIT", "run": CNC["run"], "emergency": "", "alarm": False,
                                    "running": {"name": "O%04d" % CNC["active"], "number": CNC["active"]}})
        if u.path == "/programs":
            return self.reply(200, {"programs": [{"number": n, "length": len(d), "comment": "", "modified": "2026-10-19 10:00"}
                                                 for n, d in sorted(CNC["programs"].items())]})
        if u.path == "/upload":
            return self.reply(200, CNC["programs"][int(q["number"])], "application/octet-stream")
        if u.path == "/delete":
            n = int(q["number"])
            if n == CNC["active"] and CNC["run"] != "RESET":
                return self.reply(400, {"error": f"O{n} is the active program on the CNC; reset the CNC first"})
            CNC["programs"].pop(n); return self.reply(200, {"deleted": n})
        if u.path == "/download":
            n = int(re.search(rb"O(\d+)", body).group(1))
            replaced = False
            if n in CNC["programs"]:
                if q.get("replace") != "1":
                    return self.reply(502, {"error": "cnc_dwnend3: data error (program exists, bad format, or memory full) (code 5, detail 4/0)", "code": 5})
                replaced = True
            CNC["programs"][n] = body
            return self.reply(200, {"number": n, "bytes": len(body), "replaced": replaced})

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")


srv = ThreadingHTTPServer(("127.0.0.1", 8095), FakeBridge)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from cncadmin import config, create_app, events

config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client(); c.environ_base["REMOTE_ADDR"] = "10.20.5.5"
BASE = "https://localhost"


def csrf():
    c.get(BASE + "/login")
    with c.session_transaction(base_url=BASE) as s:
        return s["csrf"]


def post(path, **data):
    data.setdefault("csrf", csrf())
    return c.post(BASE + path, data=data, follow_redirects=True)


def flashes(r):
    return re.findall(r'<div class="flash (\w+)">(.*?)</div>', r.get_data(as_text=True), re.S)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    assert cond, msg


post("/login", username="admin", password="correct-horse-1")
r = post("/machines/new", name="fanuc5", label="FANUC 0i-MF FOCAS", type="fanuc-focas", ip="192.168.50.31",
         password="", focas_port="8193", focas_timeout="5")
check(any(cat == "ok" for cat, _ in flashes(r)), f"FOCAS machine added: {flashes(r)}")
m = config.find_machine(config.load(), "fanuc5")
check(m["focas"] == {"port": 8193, "timeout": 5, "replace": False} and "serial" not in m, "FOCAS settings stored")
nft = open("/app/generated/nftables.conf").read()
check("192.168.50.31" not in nft, "no inbound firewall opening for a FOCAS machine (Pi connects out)")
for bad, why in [(dict(focas_port="0"), "port 0"), (dict(focas_timeout="99"), "timeout 99"), (dict(ip=""), "missing IP")]:
    r = post("/machines/new", **{**dict(name="bad", label="x", type="fanuc-focas", ip="192.168.50.32",
                                        password="", focas_port="8193", focas_timeout="5"), **bad})
    check(any(cat == "error" for cat, _ in flashes(r)), f"rejects {why}")

page = c.get(BASE + "/machines/fanuc5").get_data(as_text=True)
check("Programs in CNC memory" in page and "FOCAS2" in page and "parameter 3202" in page, "machine page + setup sheet render")

st = c.get(BASE + "/machines/fanuc5/focas/status").get_json()
check(st["status"]["mode"] == "EDIT" and [p["number"] for p in st["programs"]] == [1, 42], "status + CNC directory via bridge")

open("/srv/cnc/fanuc5/PART42.NC", "wb").write(b"%\r\nO0042(NEW)\r\nG00X0\r\nM30\r\n%\r\n")
open("/srv/cnc/fanuc5/O0077.NC", "wb").write(b"%\r\nO0077\r\nM30\r\n%\r\n")
r = post("/machines/fanuc5/focas/send", filename="PART42.NC")
check(any("Send failed" in msg and "program exists" in msg for _, msg in flashes(r)) and b"OLD" in CNC["programs"][42],
      "send without replace refused when O-number exists; CNC program untouched")
r = post("/machines/fanuc5/focas/send", filename="PART42.NC", replace="on")
check(any("replaced as O42" in msg for _, msg in flashes(r)) and b"(NEW)" in CNC["programs"][42], "send with replace overwrites")
check(CNC["calls"][-1][2]["replace"] == "1", "replace flag passed to bridge")
r = post("/machines/fanuc5/focas/send", filename="O0077.NC")
check(any("written as O77" in msg for _, msg in flashes(r)) and 77 in CNC["programs"], "new program written")
r = post("/machines/fanuc5/focas/send", filename="../../etc/passwd")
check(any(cat == "error" for cat, _ in flashes(r)), "path traversal refused")

r = post("/machines/fanuc5/focas/read", number="42")
saved = glob.glob("/srv/cnc/fanuc5/received/O0042_*.nc")
check(len(saved) == 1 and open(saved[0], "rb").read() == CNC["programs"][42], "read saves program to received/")

CNC["run"] = "START"
r = post("/machines/fanuc5/focas/delete", number="1")
check(any("active program" in msg for _, msg in flashes(r)) and 1 in CNC["programs"], "deleting the running program refused")
CNC["run"] = "RESET"
r = post("/machines/fanuc5/focas/delete", number="77")
check(any("O77 deleted" in msg for _, msg in flashes(r)) and 77 not in CNC["programs"], "delete")

log = [e["message"] for e in events.recent_events(20, "fanuc5")]
check(any("replaced as O42" in e for e in log) and any("deleted from CNC" in e for e in log) and any("failed" in e for e in log),
      "activity log records sends, reads, deletes and failures")

# Unreachable CNC and stopped bridge give clear messages
post("/machines/fanuc5/edit", label="FANUC 0i-MF FOCAS", type="fanuc-focas", ip="192.168.50.33", password="",
     focas_port="8193", focas_timeout="5")
st = c.get(BASE + "/machines/fanuc5/focas/status").get_json()
check("cannot connect" in st["error"], f"unreachable CNC reported: {st['error']}")
srv.shutdown(); srv.server_close()
st = c.get(BASE + "/machines/fanuc5/focas/status").get_json()
check("focas-bridge container" in st["error"], "bridge down reported clearly")
dash = c.get(BASE + "/").get_data(as_text=True)
check("FOCAS" in dash, "dashboard lists the FOCAS machine")
print("ALL PASSED")
os._exit(0)
