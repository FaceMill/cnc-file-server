"""Merged serial-dnc engine inside cnc-admin, against a fake FANUC on a TCP
serial device server (192.168.50.40:4001 on lo)."""
import glob, os, re, socket, threading, time
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, events
from cncadmin.serial_dnc import manager

XON, XOFF = b"\x11", b"\x13"


class FakeFanuc:
    """Accepts the worker's connection (again after each reconnect) and records what it sends."""
    def __init__(self):
        self.rx = bytearray()
        self.conn = None
        self.connections = 0
        self.lock = threading.Lock()
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("192.168.50.40", 4001)); self.srv.listen(4)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            conn, _ = self.srv.accept()
            self.conn = conn; self.connections += 1
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        while True:
            try:
                d = conn.recv(4096)
            except OSError:
                return
            if not d:
                return
            with self.lock:
                self.rx += d

    def punch(self, data):
        self.conn.sendall(data)

    def clear(self):
        with self.lock:
            self.rx.clear()

    def wait_rx(self, n, timeout=20):
        end = time.time() + timeout
        while len(self.rx) < n and time.time() < end:
            time.sleep(0.02)
        return len(self.rx)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    assert cond, msg


def wait_state(name, states, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = manager.status(name)
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"{name} never reached {states}; now {manager.status(name)}")


def last_events(n=10):
    return [e["message"] for e in events.recent_events(n, "fanuc3")]


fanuc = FakeFanuc()
config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client()
c.environ_base["REMOTE_ADDR"] = "10.20.5.5"
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


post("/login", username="admin", password="correct-horse-1")
form = dict(name="fanuc3", label="FANUC 0-M", type="fanuc-rs232", ip="192.168.50.40", password="",
            serial_transport="tcp", serial_tcp_port="4001", serial_enabled="on", serial_baud="9600",
            serial_data_bits="7", serial_parity="E", serial_stop_bits="2", serial_flow="xonxoff",
            serial_send_delay="1", serial_line_delay_ms="0", serial_receive_timeout="1",
            serial_pause_timeout="30", serial_excluded_ext=".BAK, txt")
r = post("/machines/new", **form)
check(any(cat == "ok" for cat, _ in flashes(r)), f"serial machine added: {flashes(r)}")
m = config.find_machine(config.load(), "fanuc3")
check(m["serial"]["excluded_ext"] == "bak, txt" and m["serial"]["send_delay"] == 1.0, "serial-dnc settings stored and normalised")
st = wait_state("fanuc3", {"idle"})
check(fanuc.connections == 1, "worker opened the port and is listening")

folder = "/srv/cnc/fanuc3"
small = b"%\nO0042(SMALL)\nG00X0Y0\nM30\n%\n"                     # LF only: must be sent unmodified
big = b"%\r\nO0100(BIG)\r\n" + b"".join(b"N%d G01 X%d.0 Y%d.0 F100\r\n" % (i, i, i) for i in range(1, 140)) + b"M30\r\n%\r\n"
open(f"{folder}/O0042.NC", "wb").write(small)
open(f"{folder}/PART-A.NC", "wb").write(big)
open(f"{folder}/notes.bak", "wb").write(b"old")

# 1. Request by program number -> waiting with countdown -> exact bytes
fanuc.clear()
fanuc.punch(b"\x00\x00%\r\nO9999 (SEND O0042)\r\nM30\r\n%\x14")
st = wait_state("fanuc3", {"waiting"})
check(st["countdown"] in (0, 1) and st["file"] == "O0042.NC", f"request found O0042 by program number, countdown {st['countdown']}")
fanuc.wait_rx(len(small)); wait_state("fanuc3", {"idle"})
check(bytes(fanuc.rx) == small, "file sent byte for byte, unmodified (LF kept)")
check(any("Request received: SEND O0042" in e for e in last_events()), "request logged")

# 2. FANUC XOFF limit: <= 10 characters after XOFF
fanuc.clear()
fanuc.punch(b"%\r\nO9999 (SEND PART-A)\r\nM30\r\n%")
wait_state("fanuc3", {"sending"})
fanuc.wait_rx(400)
fanuc.punch(XOFF)
at_xoff = len(fanuc.rx)
time.sleep(1.5)
after = len(fanuc.rx) - at_xoff
st = manager.status("fanuc3")
check(after <= 10, f"only {after} chars arrived after XOFF (FANUC alarm 087 above 10)")
check("XOFF" in st["detail"], f"status shows paused: {st['detail']}")
fanuc.punch(XON)
fanuc.wait_rx(len(big)); wait_state("fanuc3", {"idle"})
check(bytes(fanuc.rx) == big, "send resumed after XON and completed exactly")

# 3. Punching a normal program saves it
fanuc.punch(b"\x00\x12%\r\nO1234(FROM CONTROL)\r\nG90G54\r\nM30\r\n%\x14\x00")
time.sleep(1.5)
saved = glob.glob(f"{folder}/received/O1234_*.nc")
check(len(saved) == 1, f"punched program saved to received/: {[os.path.basename(p) for p in saved]}")
data = open(saved[0], "rb").read()
check(data.startswith(b"%\r\nO1234") and data.endswith(b"%") and b"\x00" not in data and b"\x12" not in data, "NUL/DC codes stripped, program intact")
page = c.get(BASE + "/machines/fanuc3").get_data(as_text=True)
check(os.path.basename(saved[0]) in page and "PART-A.NC" in page and "notes.bak" not in page, "page lists programs (skipped type hidden) and received")

# 4. Skipped file types can't be requested or sent
fanuc.punch(b"%\r\nO9999 (SEND notes.bak)\r\nM30\r\n%")
time.sleep(1.5)
check(manager.status("fanuc3")["state"] == "idle" and any("No file matching 'notes.bak'" in e for e in last_events()), "request for a skipped type refused")
r = post("/machines/fanuc3/dnc/send", filename="notes.bak", delay="0")
check(any("skipped" in msg for _, msg in flashes(r)), "manual send of a skipped type refused")
r = post("/machines/fanuc3/dnc/send", filename="../../../etc/passwd", delay="0")
check(any(cat == "error" for cat, _ in flashes(r)), "path traversal refused")

# 5. Manual send from the web page with a custom delay
fanuc.clear()
r = post("/machines/fanuc3/dnc/send", filename="O0042.NC", delay="0")
check(any("queued" in msg for _, msg in flashes(r)), "manual send queued")
fanuc.wait_rx(len(small)); wait_state("fanuc3", {"idle"})
check(bytes(fanuc.rx) == small, "manual send delivered")

# 6. Reset and call again: a new request aborts the running send
fanuc.clear()
fanuc.punch(b"%\r\nO9999 (SEND PART-A)\r\nM30\r\n%")
wait_state("fanuc3", {"sending"}); fanuc.wait_rx(300)
fanuc.punch(b"%\r\nO9999 (SEND O0042)\r\nM30\r\n%")
st = wait_state("fanuc3", {"waiting"})
check(st["file"] == "O0042.NC", "new request took over")
check(any("aborted" in e and "machine sent new data" in e for e in last_events()), "old send aborted on new request")
wait_state("fanuc3", {"sending"}); wait_state("fanuc3", {"idle"})
check(bytes(fanuc.rx).endswith(small), "new program sent after the abort")

# 7. Cancel from the web page
fanuc.punch(b"%\r\nO9999 (SEND PART-A)\r\nM30\r\n%")
wait_state("fanuc3", {"sending"})
post("/machines/fanuc3/dnc/cancel")
wait_state("fanuc3", {"idle"})
check(any("cancelled at" in e for e in last_events()), "cancel stopped the send")

# 8. Live status JSON, viewer, download, dashboard, logs
js = c.get(BASE + "/machines/fanuc3/dnc/status").get_json()
check(js["state"] == "idle", "status endpoint")
v = c.get(BASE + "/machines/fanuc3/view?folder=programs&file=PART-A.NC").get_data(as_text=True)
check("N139 G01 X139.0" in v and "Send to FANUC 0-M" in v, "file viewer shows program with send button")
d = c.get(BASE + f"/machines/fanuc3/download?folder=received&file={os.path.basename(saved[0])}")
check(d.status_code == 200 and d.data == data, "download received program")
dash = c.get(BASE + "/").get_data(as_text=True)
check("listening" in dash and "Request received" in dash, "dashboard shows listening + recent DNC activity")
check("Request received: SEND O0042" in c.get(BASE + "/logs?c=dnc").get_data(as_text=True), "DNC activity log page")

# 9. Settings change restarts the worker; disable stops it; delete removes it
before = fanuc.connections
post("/machines/fanuc3/edit", **{**form, "serial_send_delay": "2", "label": "FANUC 0-M renamed"})
time.sleep(0.5)
check(fanuc.connections == before and manager.get("fanuc3").m["send_delay"] == 2.0
      and manager.get("fanuc3").m["name"] == "FANUC 0-M renamed", "delay/name edit applied live, port not reopened")
post("/machines/fanuc3/edit", **{**form, "serial_send_delay": "2", "serial_baud": "4800"})
wait_state("fanuc3", {"idle"}); time.sleep(0.3)
check(fanuc.connections == before + 1 and manager.get("fanuc3").m["baudrate"] == 4800, "baud change reopened the port")
post("/machines/fanuc3/dnc/restart"); wait_state("fanuc3", {"idle"}); time.sleep(0.3)
check(fanuc.connections == before + 2, "Restart port reopens")
post("/machines/fanuc3/edit", **{k: v for k, v in form.items() if k != "serial_enabled"})
check(manager.status("fanuc3")["state"] == "disabled", "disabling stops the listener")
r = post("/machines/fanuc3/dnc/send", filename="O0042.NC", delay="0")
check(any("not running" in msg for _, msg in flashes(r)), "send refused while disabled")
post("/machines/fanuc3/delete")
check(manager.get("fanuc3") is None and config.find_machine(config.load(), "fanuc3") is None, "delete removes machine and worker")
print("ALL PASSED")
os._exit(0)
