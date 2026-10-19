"""FOCAS bridge (runs in the 32-bit focas image): the real libfwlib32 loads and reports
connection errors; the HTTP API needs the token and validates input; 'replace'
restores the old program if writing the new one fails (simulated CNC memory)."""
import contextlib, http.client, json, os, socket, subprocess, sys, threading, time

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# --- real library ---------------------------------------------------------
import focas
focas.lib()
check(True, "libfwlib32 loads (cnc_startupprocess OK)")
try:
    with focas.connect("127.0.0.1", 8193, 2):
        pass
    check(False, "connect to nothing fails")
except focas.FocasError as e:
    check(e.code == -16, f"nothing listening -> code {e.code} (EW_SOCKET)")
srv = socket.socket(); srv.bind(("127.0.0.1", 8194)); srv.listen(1)
def junk():
    c, _ = srv.accept(); c.recv(1024); c.sendall(b"HTTP/1.0 400 nope\r\n\r\n"); c.close()
threading.Thread(target=junk, daemon=True).start()
try:
    with focas.connect("127.0.0.1", 8194, 2):
        pass
    check(False, "non-CNC server fails")
except focas.FocasError as e:
    check(e.code == -17, f"non-CNC server -> code {e.code} (EW_PROTOCOL)")
check(focas.to_cnc_format(b"%\r\nO0042(T)\r\nM30\r\n%\r\n") == (b"\nO0042(T)\nM30\n%", 42), "program converted to FOCAS format")
check(all(hasattr(focas.lib(), f) for f in ("cnc_sysinfo", "cnc_rdalmmsg2", "cnc_rdopmsg", "cnc_rdpdf_alldir",
                                            "cnc_upstart4", "cnc_dwnstart4", "cnc_pdf_del")),
      "folder, model and alarm functions exist in this build of the library")
check(focas.to_cnc_format(b"%\nO12345678(T)\nM30\n%", names=True)[1] == 12345678
      and focas.to_cnc_format(b"<PART-A>\nM30\n", names=True)[1] == "PART-A", "folder mode: 8-digit O-numbers and <NAME> headers")
for bad, names in ((b"O12345\nM30", False), (b"<PART-A>\nM30", False), (b"<../X>\nM30", True), (b"G00\nM30", True)):
    try:
        focas.to_cnc_format(bad, names=names); ok = False
    except ValueError:
        ok = True
    check(ok, f"program header {bad[:9]!r} refused (folder mode {names})")
check(focas.same_program("O0042", 42) and not focas.same_program("O0042", 4) and focas.same_program("PART-A", "PART-A"),
      "folder file names matched to O-numbers and names")

# --- parsing the library's answers (a fake library fills the structures) -----------
real_lib = focas._lib
class FakeLib:
    entries = [(0, "SUB")] + [(1, f"O{n:04d}") for n in range(1, 23)]  # 1 folder + 22 programs: three pages of 10
    def cnc_statinfo(self, h, st):
        st._obj.aut, st._obj.run, st._obj.alarm = 1, 3, 1; return 0
    def cnc_exeprgname(self, h, exe):
        exe._obj.name, exe._obj.o_num = b"O0042", 42; return 0
    def cnc_sysinfo(self, h, s):
        o = s._obj; o.addinfo, o.cnc_type, o.mt_type, o.series, o.version, o.axes = 2, b"31", b" M", b"G421", b"28.0", b"05"; return 0
    def cnc_rdalmmsg2(self, h, typ, num, buf):
        msg = b"IMPROPER G-CODE"
        buf[0].alm_no, buf[0].type, buf[0].msg_len, buf[0].alm_msg = 10, 3, len(msg), msg + b"garbage after"
        num._obj.value = 1; return 0
    def cnc_rdopmsg(self, h, typ, length, m):
        m._obj.datano, m._obj.char_num, m._obj.data = 2000, 11, b"LOAD PART B"; return 0
    def cnc_rdpdf_curdir(self, h, typ, buf):
        ctypes.memmove(buf, b"//CNC_MEM/USER/PATH1/\0", 22); return 0
    def cnc_rdpdf_subdirn(self, h, path, n):
        n._obj.dir_num, n._obj.file_num = 1, 22; return 0
    def cnc_rdpdf_alldir(self, h, num, req, buf):
        start, want = req._obj.req_num, num._obj.value
        page = self.entries[start:start + want]
        for i, (kind, name) in enumerate(page):
            buf[i].data_kind, buf[i].d_f, buf[i].size, buf[i].year, buf[i].mon, buf[i].day = kind, name.encode(), 100 + i, 2026, 10, 19
        num._obj.value = len(page); return 0
import ctypes
focas._lib = FakeLib()
st = focas.status(1, details=True)
check(st["cnc"] == {"model": "31i-M", "series": "G421", "version": "28.0", "axes": "5"}, f"control model parsed: {st['cnc']}")
check(st["alarms"] == [{"number": 10, "type": 3, "axis": 0, "message": "IMPROPER G-CODE"}], f"alarm text parsed: {st['alarms']}")
check(st["message"] == "LOAD PART B" and st["run"] == "START" and st["running"]["name"] == "O0042", "operator message parsed")
check(focas.current_folder(1) == "//CNC_MEM/USER/PATH1/", "current folder read")
listing = focas.folder(1, "//CNC_MEM/USER/PATH1/")
check(len(listing) == 23 and listing[0]["folder"] and listing[0]["name"] == "SUB" and listing[-1]["name"] == "O0022"
      and listing[1]["modified"].startswith("2026-10-19"), f"folder listing read in pages ({len(listing)} entries)")
FakeLib.cnc_rdpdf_curdir = lambda self, h, typ, buf: 1  # EW_FUNC: no program folders on this control
check(focas.current_folder(1) is None, "a control without program folders is detected")
FakeLib.cnc_rdopmsg = lambda self, h, typ, length, m: 6
check(focas.status(1, details=True)["message"] is None, "an unsupported extra (operator message) doesn't break status")
focas._lib = real_lib

# --- real bridge process: token and length checks ----------------------------
open("/tmp/tok", "w").write("a" * 64)
proc = subprocess.Popen([sys.executable, "bridge.py"], env={**os.environ, "FOCAS_TOKEN_FILE": "/tmp/tok", "FOCAS_BRIDGE_PORT": "8096"},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)


def call(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.putrequest(method, path)
    headers = dict(headers or {})
    if body is not None:
        headers.setdefault("Content-Length", str(len(body)))
    for k, v in headers.items():
        c.putheader(k, v)
    c.endheaders()
    if body:
        c.send(body)
    r = c.getresponse()
    return r.status, r.read()


tok = {"X-Focas-Token": "a" * 64}
check(call(8096, "GET", "/status?ip=127.0.0.1")[0] == 403, "no token -> 403")
check(call(8096, "GET", "/status?ip=127.0.0.1", {"X-Focas-Token": "b" * 64})[0] == 403, "wrong token -> 403")
st, body = call(8096, "GET", "/status?ip=127.0.0.1&timeout=2", tok)
check(st == 502 and b"code -16" in body, "right token reaches the library (502, cannot connect)")
check(call(8096, "POST", "/download?ip=127.0.0.1", {**tok, "Content-Length": "-1"})[0] == 413, "negative Content-Length refused")
check(call(8096, "GET", "/status?ip=not-an-ip", tok)[0] == 400, "bad IP refused")
check(call(8096, "GET", "/secret?ip=127.0.0.1", tok)[0] == 404, "unknown endpoint refused")
proc.terminate()
r = subprocess.run([sys.executable, "bridge.py"], env={**os.environ, "FOCAS_TOKEN_FILE": "/nope"}, capture_output=True, timeout=10)
check(r.returncode != 0, "bridge refuses to start without a token file")

# --- replace safety with a simulated CNC memory -----------------------------
import bridge
MEM = {42: b"%\nO0042(OLD VERSION)\nM30\n%"}
fail_next = {"on": False}
@contextlib.contextmanager
def fake_connect(ip, port, timeout):
    yield 1
focas.connect = fake_connect
focas.directory = lambda h: [{"number": n} for n in MEM]
RUN = {"run": "RESET", "running": {"number": 1, "name": "O0001"}}
focas.status = lambda h, details=False: dict(RUN)
focas.upload = lambda h, n: MEM[n]
def fake_delete(h, n):
    MEM.pop(n)
def fake_download(h, data):
    if fail_next["on"]:
        fail_next["on"] = False
        raise focas.FocasError("cnc_download3", 8)
    MEM[int(data.split(b"O")[1][:4])] = data
focas.delete, focas.download = fake_delete, fake_download
bridge.TOKEN = "t" * 64
s = bridge.ThreadingHTTPServer(("127.0.0.1", 8097), bridge.Handler)
threading.Thread(target=s.serve_forever, daemon=True).start()
T = {"X-Focas-Token": "t" * 64}
st, body = call(8097, "POST", "/download?ip=1.2.3.4", T, b"%\nO0042(NEW)\nM30\n%")
check(st == 400 and b"already exists" in body and b"OLD" in MEM[42], "existing O-number without replace refused, CNC untouched")
fail_next["on"] = True
st, body = call(8097, "POST", "/download?ip=1.2.3.4&replace=1", T, b"%\nO0042(NEW)\nM30\n%")
check(st == 502 and b"restored" in body and b"OLD VERSION" in MEM.get(42, b""), "failed replace restores the old program")
st, body = call(8097, "POST", "/download?ip=1.2.3.4&replace=1", T, b"%\nO0042(NEW)\nM30\n%")
check(st == 200 and json.loads(body)["replaced"] and b"(NEW)" in MEM[42], "replace succeeds")
for n in ("66770", "0", "10000"):
    st, body = call(8097, "POST", f"/delete?ip=1.2.3.4&number={n}", T)
    check(st == 400 and b"1-9999" in body, f"bridge refuses to delete O{n} (would wrap in a C short)")
st, body = call(8097, "POST", "/download?ip=1.2.3.4", T, b"G00X0\nM30\n")
check(st == 400 and b"O-number" in body, "program without O-number refused")

# --- program folders: by name, replace with restore, path checks -----------------
PDF = {"//CNC_MEM/USER/PATH1/O0042": b"%\nO0042(OLD IN FOLDER)\nM30\n%"}
focas.current_folder = lambda h: "//CNC_MEM/USER/PATH1/"
focas.folder = lambda h, path: [{"name": k[len(path):], "folder": False} for k in PDF if k.rsplit("/", 1)[0] + "/" == path]
focas.upload_name = lambda h, path: PDF[path]
def fake_delete_name(h, path):
    PDF.pop(path)
def fake_download_to(h, path, data):
    if fail_next["on"]:
        fail_next["on"] = False
        raise focas.FocasError("cnc_download4", 8)
    m = focas.HEADER_RE.match(data.lstrip())
    PDF[path + ("O%04d" % int(m.group(1)) if m.group(1) else m.group(2).decode())] = data
focas.delete_name, focas.download_to = fake_delete_name, fake_download_to
st, body = call(8097, "GET", "/programs?ip=1.2.3.4", T)
check(st == 200 and json.loads(body)["folders"] and json.loads(body)["folder"] == "//CNC_MEM/USER/PATH1/", "folder listing (current folder)")
F = "&folder=//CNC_MEM/USER/PATH1/"
st, body = call(8097, "POST", "/download?ip=1.2.3.4" + F, T, b"%\nO42(NEW)\nM30\n%")
check(st == 400 and b"O0042 already exists" in body, "folder: existing program (O42 = O0042) refused without replace")
fail_next["on"] = True
st, body = call(8097, "POST", "/download?ip=1.2.3.4&replace=1" + F, T, b"%\nO42(NEW)\nM30\n%")
check(st == 502 and b"restored" in body and b"OLD IN FOLDER" in PDF["//CNC_MEM/USER/PATH1/O0042"], "folder: failed replace restores the old program")
st, body = call(8097, "POST", "/download?ip=1.2.3.4&replace=1" + F, T, b"%\nO42(NEW)\nM30\n%")
check(st == 200 and json.loads(body)["replaced"] and b"(NEW)" in PDF["//CNC_MEM/USER/PATH1/O0042"], "folder: replace succeeds")
st, body = call(8097, "POST", "/download?ip=1.2.3.4" + F, T, b"%\n<PART-A>\nM30\n%")
check(st == 200 and json.loads(body)["name"] == "PART-A" and "//CNC_MEM/USER/PATH1/PART-A" in PDF, "folder: named program written")
RUN.update(run="START", running={"number": 0, "name": "PART-A"})
st, body = call(8097, "POST", "/delete?ip=1.2.3.4&name=//CNC_MEM/USER/PATH1/PART-A", T)
check(st == 400 and b"active program" in body and "//CNC_MEM/USER/PATH1/PART-A" in PDF, "folder: running program can't be deleted")
RUN.update(run="RESET")
st, body = call(8097, "POST", "/delete?ip=1.2.3.4&name=//CNC_MEM/USER/PATH1/PART-A", T)
check(st == 200 and "//CNC_MEM/USER/PATH1/PART-A" not in PDF, "folder: delete by name")
for path in ("/upload?name=//CNC_MEM/USER/../../x", "/upload?name=//MEMCARD/O1", "/upload?name=//CNC_MEM/USER/PATH1/",
             "/programs?folder=//CNC_MEM/USER/PATH1", "/programs?folder=//CNC_MEM/./", "/upload?name=//CNC_MEM/" + "A" * 300):
    st, body = call(8097, "GET", path + "&ip=1.2.3.4", T)
    check(st == 400, f"bad path refused: {path[:40]}")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
