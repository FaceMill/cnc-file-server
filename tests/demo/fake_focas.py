"""Demo only: stands in for focas-bridge with a simulated FANUC 0i-MF memory."""
import json, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROGS = {1: b"%\nO0001(MAIN SETUP)\nG28G91Z0\nM30\n%", 1000: b"%\nO1000(FACE MILL 50MM)\nG90G54G00X0Y0\nS1500M03\nM30\n%",
         2050: b"%\nO2050(BRACKET OP1)\nG90G54\nM30\n%", 9001: b"%\nO9001(TOOL CHANGE MACRO)\nM99\n%"}
STATE = {"active": 1000}

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
            return self.reply(200, {"mode": "EDIT", "run": "RESET", "emergency": "", "alarm": False, "running": {"name": "O%04d" % STATE["active"], "number": STATE["active"]}})
        if u.path == "/programs":
            return self.reply(200, {"programs": [{"number": n, "length": len(d), "comment": re.search(rb"\((.*?)\)", d).group(1).decode() if b"(" in d else "",
                                                  "modified": "2026-10-19 09:%02d" % (n % 60)} for n, d in sorted(PROGS.items())]})
        if u.path == "/upload":
            return self.reply(200, PROGS[int(q["number"])].replace(b"\n", b"\r\n"), "application/octet-stream")
        if u.path == "/delete":
            n = int(q["number"])
            if n == 9001: return self.reply(502, {"error": "cnc_delete: write protected (memory protect key or parameter) (code 7)"})
            PROGS.pop(n, None); return self.reply(200, {"deleted": n})
        if u.path == "/download":
            text = body.replace(b"\r\n", b"\n").strip().strip(b"%").strip()
            m = re.match(rb"O(\d{1,4})", text)
            if not m: return self.reply(400, {"error": "The program must start with an O-number (O1-O9999), e.g. O1234"})
            n = int(m.group(1)); existed = n in PROGS
            if existed and q.get("replace") != "1":
                return self.reply(502, {"error": "cnc_dwnend3: data error (program exists, bad format, or memory full) (code 5, detail 4/0)"})
            PROGS[n] = b"%\n" + text + b"\n%"; return self.reply(200, {"number": n, "bytes": len(text) + 3, "replaced": existed})
    def do_GET(self): self.go("GET")
    def do_POST(self): self.go("POST")

ThreadingHTTPServer(("127.0.0.1", 8095), H).serve_forever()
