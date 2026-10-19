"""cnc-admin FOCAS workflow against a fake focas-bridge (simulated CNC memory)."""
import glob, json, os, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from werkzeug.security import generate_password_hash

CNC = {"programs": {1: b"%\nO0001(MAIN)\nM30\n%", 42: b"%\nO0042(OLD)\nM30\n%"}, "active": 1, "run": "RESET",
       "calls": [], "pdf": False}
# A control with program folders (30i/0i-F): files by full path
PDF = {"//CNC_MEM/USER/PATH1/O0042": b"%\nO0042(OLD IN FOLDER)\nM30\n%",
       "//CNC_MEM/USER/PATH1/PART-A": b"%\n<PART-A>(NAMED)\nM30\n%",
       "//CNC_MEM/USER/PATH1/SUB/O12345678": b"%\nO12345678(EIGHT DIGITS)\nM30\n%"}


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
        if CNC["pdf"]:
            return self.pdf(u.path, q, body)
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

    def pdf(self, path, q, body):
        if path == "/status":
            return self.reply(200, {"mode": "MEM", "run": "START", "emergency": "", "alarm": True,
                                    "running": {"name": "PART-A", "number": 0},
                                    "cnc": {"model": "31i-M", "series": "G421", "version": "28.0", "axes": "5"},
                                    "alarms": [{"number": 10, "type": 3, "axis": 0, "message": "IMPROPER G-CODE"}],
                                    "message": None})
        if path == "/programs":
            folder = q.get("folder", "//CNC_MEM/USER/PATH1/")
            subs = sorted({k[len(folder):].split("/")[0] for k in PDF if k.startswith(folder) and "/" in k[len(folder):]})
            files = [k[len(folder):] for k in sorted(PDF) if k.startswith(folder) and "/" not in k[len(folder):]]
            return self.reply(200, {"folders": True, "folder": folder, "current": "//CNC_MEM/USER/PATH1/",
                                    "entries": [{"name": n, "folder": True, "size": 0, "comment": "", "modified": ""} for n in subs]
                                    + [{"name": n, "folder": False, "size": len(PDF[folder + n]), "comment": "", "modified": ""} for n in files]})
        if path == "/upload":
            return self.reply(200, PDF[q["name"]], "application/octet-stream")
        if path == "/delete":
            if q["name"].endswith("/PART-A"):
                return self.reply(400, {"error": "PART-A is the active program on the CNC; reset the CNC first"})
            PDF.pop(q["name"]); return self.reply(200, {"deleted": q["name"]})
        if path == "/download":
            m = re.search(rb"O(\d{1,8})|<([A-Za-z0-9_.+-]+)>", body)
            name = "O%04d" % int(m.group(1)) if m.group(1) else m.group(2).decode()
            target = q["folder"] + name; existed = target in PDF
            if existed and q.get("replace") != "1":
                return self.reply(400, {"error": f"{name} already exists on the CNC; tick 'replace' to overwrite it"})
            PDF[target] = body
            return self.reply(200, {"number": int(m.group(1)) if m.group(1) else None, "name": name,
                                    "bytes": len(body), "replaced": existed})

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
kept = glob.glob("/srv/cnc/fanuc5/received/O0042_*_before-replace.nc")
check(len(kept) == 1 and b"OLD" in open(kept[0], "rb").read(), "CNC's previous O42 saved to received/ before replacing")
check(CNC["calls"][-1][2]["replace"] == "1", "replace flag passed to bridge")
r = post("/machines/fanuc5/focas/send", filename="O0077.NC")
check(any("written as O77" in msg for _, msg in flashes(r)) and 77 in CNC["programs"], "new program written")
r = post("/machines/fanuc5/focas/send", filename="../../etc/passwd")
check(any(cat == "error" for cat, _ in flashes(r)), "path traversal refused")

r = post("/machines/fanuc5/focas/read", number="42")
saved = [p for p in glob.glob("/srv/cnc/fanuc5/received/O0042_*.nc") if "before-replace" not in p]
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

# --- a control with program folders (30i/0i-F) ----------------------------------------
CNC["pdf"] = True
st = c.get(BASE + "/machines/fanuc5/focas/status").get_json()
check(st["folders"] and st["folder"] == "//CNC_MEM/USER/PATH1/" and [e["name"] for e in st["entries"]] == ["SUB", "O0042", "PART-A"],
      "folder listing for a control with program folders")
check(st["status"]["cnc"]["model"] == "31i-M" and st["status"]["alarms"][0]["message"] == "IMPROPER G-CODE",
      "control model and alarm text passed through")
st = c.get(BASE + "/machines/fanuc5/focas/status?folder=//CNC_MEM/USER/PATH1/SUB/").get_json()
check([e["name"] for e in st["entries"]] == ["O12345678"], "sub-folder listing")
for bad in ("//CNC_MEM/USER/../", "//DATA_SV/", "//CNC_MEM/USER", "C:/", "//CNC_MEM/./"):
    st = c.get(BASE + "/machines/fanuc5/focas/status?folder=" + bad).get_json()
    check("Not a folder" in st.get("error", ""), f"folder {bad!r} refused before reaching the bridge")
r = post("/machines/fanuc5/focas/read", name="//CNC_MEM/USER/PATH1/SUB/O12345678")
saved = glob.glob("/srv/cnc/fanuc5/received/O12345678_*.nc")
check(len(saved) == 1 and b"EIGHT DIGITS" in open(saved[0], "rb").read(), "read by name (8-digit O-number) saved to received/")
r = post("/machines/fanuc5/focas/read", name="//CNC_MEM/USER/PATH1/PART-A")
check(glob.glob("/srv/cnc/fanuc5/received/PART-A_*.nc"), "read a named program")
for bad in ("//CNC_MEM/USER/PATH1/../../etc", "//CNC_MEM/USER/PATH1/", "//MEMCARD/O1", "O0042"):
    r = post("/machines/fanuc5/focas/read", name=bad)
    check(any("Not a program" in msg for _, msg in flashes(r)), f"program path {bad!r} refused")
open("/srv/cnc/fanuc5/NEW42.NC", "wb").write(b"%\r\nO0042(NEW IN FOLDER)\r\nM30\r\n%\r\n")
r = post("/machines/fanuc5/focas/send", filename="NEW42.NC", folder="//CNC_MEM/USER/PATH1/")
check(any("already exists" in msg for _, msg in flashes(r)) and b"OLD IN FOLDER" in PDF["//CNC_MEM/USER/PATH1/O0042"],
      "send into a folder without replace refused when the program exists")
r = post("/machines/fanuc5/focas/send", filename="NEW42.NC", folder="//CNC_MEM/USER/PATH1/", replace="on")
check(any("replaced as O0042 in //CNC_MEM/USER/PATH1/" in msg for _, msg in flashes(r))
      and b"NEW IN FOLDER" in PDF["//CNC_MEM/USER/PATH1/O0042"], "send into a folder with replace")
kept = [p for p in glob.glob("/srv/cnc/fanuc5/received/O0042_*_before-replace*.nc") if b"IN FOLDER" in open(p, "rb").read()]
check(len(kept) == 1, "old copy from the folder saved to received/ before replacing (even within the same second as another)")
open("/srv/cnc/fanuc5/NAMED.NC", "wb").write(b"%\r\n<PART-B>(NEW NAMED)\r\nM30\r\n%\r\n")
r = post("/machines/fanuc5/focas/send", filename="NAMED.NC", folder="//CNC_MEM/USER/PATH1/SUB/")
check("//CNC_MEM/USER/PATH1/SUB/PART-B" in PDF, "named program written into a sub-folder")
r = post("/machines/fanuc5/focas/send", filename="NAMED.NC", folder="//CNC_MEM/../")
check(any("Not a folder" in msg for _, msg in flashes(r)), "send to a bad folder refused")
r = post("/machines/fanuc5/focas/delete", name="//CNC_MEM/USER/PATH1/PART-A")
check(any("active program" in msg for _, msg in flashes(r)) and "//CNC_MEM/USER/PATH1/PART-A" in PDF, "deleting the running named program refused")
r = post("/machines/fanuc5/focas/delete", name="//CNC_MEM/USER/PATH1/SUB/PART-B")
check(any("PART-B deleted" in msg for _, msg in flashes(r)) and "//CNC_MEM/USER/PATH1/SUB/PART-B" not in PDF, "delete by name")
page = c.get(BASE + "/machines/fanuc5").get_data(as_text=True)
check('class="cnc-folder"' in page, "Send to CNC forms carry the folder")
CNC["pdf"] = False

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
