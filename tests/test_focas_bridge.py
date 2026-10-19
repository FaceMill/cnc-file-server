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
focas.status = lambda h: {"run": "RESET", "running": {"number": 1}}
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
st, body = call(8097, "POST", "/download?ip=1.2.3.4", T, b"G00X0\nM30\n")
check(st == 400 and b"O-number" in body, "program without O-number refused")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
