"""Demo only: stands in for focas-bridge with a simulated FANUC 0i-MF with program folders."""
import json, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOME = "//CNC_MEM/USER/PATH1/"
FILES = {HOME + "O0001": b"%\nO0001(MAIN SETUP)\nG28G91Z0\nM30\n%",
         HOME + "O1000": b"%\nO1000(FACE MILL 50MM)\nG90G54G00X0Y0\nS1500M03\nM30\n%",
         HOME + "O2050": b"%\nO2050(BRACKET OP1)\nG90G54\nM30\n%",
         HOME + "O9001": b"%\nO9001(TOOL CHANGE MACRO)\nM99\n%",
         HOME + "BRACKET-OP2": b"%\n<BRACKET-OP2>(NAMED PROGRAM)\nG90G54\nM30\n%",
         HOME + "LIBRARY/O8000": b"%\nO8000(PROBE CYCLE)\nM99\n%"}
FOLDERS = {"//CNC_MEM/", "//CNC_MEM/USER/", HOME, HOME + "LIBRARY/"}
STATE = {"active": "O1000"}


def entries(folder):
    subs = sorted({f[len(folder):].split("/")[0] for f in FOLDERS if f.startswith(folder) and f != folder})
    out = [{"name": s, "folder": True, "size": 0, "comment": "", "modified": ""} for s in subs]
    for path, data in sorted(FILES.items()):
        if path.rsplit("/", 1)[0] + "/" == folder:
            c = re.search(rb"\((.*?)\)", data)
            out.append({"name": path.rsplit("/", 1)[1], "folder": False, "size": len(data),
                        "comment": c.group(1).decode() if c else "", "modified": "2026-10-19 09:%02d" % (len(data) % 60)})
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, body, ctype="application/json"):
        d = json.dumps(body).encode() if ctype == "application/json" else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
    def go(self, method):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))) if method == "POST" else b""
        time.sleep(0.3)
        if u.path == "/status":
            return self.reply(200, {"mode": "EDIT", "run": "RESET", "emergency": "", "alarm": False,
                                    "running": {"name": STATE["active"], "number": 1000},
                                    "cnc": {"model": "0i-M", "series": "D4F1", "version": "30.0", "axes": "4"},
                                    "alarms": [], "message": "CHECK COOLANT LEVEL BEFORE SHIFT"})
        if u.path == "/programs":
            folder = q.get("folder") or HOME
            if folder not in FOLDERS:
                return self.reply(502, {"error": "cnc_rdpdf_subdirn: invalid data attribute (code 4)"})
            return self.reply(200, {"folders": True, "folder": folder, "current": HOME, "entries": entries(folder)})
        if u.path == "/upload":
            return self.reply(200, FILES[q["name"]].replace(b"\n", b"\r\n"), "application/octet-stream")
        if u.path == "/delete":
            if q["name"].endswith("O9001"):
                return self.reply(502, {"error": "cnc_pdf_del: write protected (memory protect key or parameter) (code 7)"})
            FILES.pop(q["name"], None); return self.reply(200, {"deleted": q["name"]})
        if u.path == "/download":
            text = body.replace(b"\r\n", b"\n").strip().strip(b"%").strip()
            m = re.match(rb"O(\d{1,8})|<([A-Za-z0-9_.+-]{1,32})>", text)
            if not m: return self.reply(400, {"error": "The program must start with an O-number (O1-O99999999) or a <NAME> line"})
            name = "O%04d" % int(m.group(1)) if m.group(1) else m.group(2).decode()
            folder = q.get("folder") or HOME
            path = folder + name; existed = path in FILES
            if existed and q.get("replace") != "1":
                return self.reply(400, {"error": f"{name} already exists on the CNC; tick 'replace' to overwrite it"})
            FILES[path] = b"%\n" + text + b"\n%"
            return self.reply(200, {"number": int(m.group(1)) if m.group(1) else None, "name": name,
                                    "bytes": len(text) + 3, "replaced": existed})
    def do_GET(self): self.go("GET")
    def do_POST(self): self.go("POST")

ThreadingHTTPServer(("127.0.0.1", 8095), H).serve_forever()
