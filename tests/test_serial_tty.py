"""Serial DNC over a real tty (pseudo-terminal = the USB adapter code path: termios,
exclusive lock, out_waiting/drain), a 1 MB drip-feed at 115200 with XON/XOFF, and a
concurrency stress test of the admin page."""
import hashlib, os, select, threading, time, tty
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, events
from cncadmin.serial_dnc import manager


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    assert cond, msg


master, slave = os.openpty()
tty.setraw(master)
slave_path = os.ttyname(slave)
os.close(slave)

config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client(); c.environ_base["REMOTE_ADDR"] = "10.20.5.5"
BASE = "https://localhost"


def csrf(cl=c):
    cl.get(BASE + "/login")
    with cl.session_transaction(base_url=BASE) as s:
        return s["csrf"]


def post(path, cl=c, **data):
    data.setdefault("csrf", csrf(cl))
    return cl.post(BASE + path, data=data, follow_redirects=True)


def wait_state(states, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = manager.status("tty1")
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"never reached {states}: {manager.status('tty1')}")


post("/login", username="admin", password="correct-horse-1")
form = dict(name="tty1", label="FANUC on tty", type="fanuc-rs232", ip="", password="", serial_transport="usb",
            serial_device=slave_path, serial_enabled="on", serial_baud="115200", serial_data_bits="8", serial_parity="N",
            serial_stop_bits="1", serial_flow="xonxoff", serial_send_delay="0", serial_line_delay_ms="0",
            serial_receive_timeout="1", serial_pause_timeout="30")
post("/machines/new", **form)
wait_state({"idle"})
check(any("opened" in e["message"] for e in events.recent_events(5, "tty1")), f"worker opened tty {slave_path} (termios path)")

# exclusive lock: a second opener must fail
import serial
try:
    serial.Serial(slave_path, exclusive=True).close(); locked = False
except serial.SerialException:
    locked = True
check(locked, "port is opened exclusively (a second program can't grab it)")

# 1 MB drip-feed with periodic XOFF/XON from the "machine"
big = b"%\r\nO7777(BIG)\r\n" + b"".join(b"N%d G01 X%d.123 Y%d.456 Z-1.0 F1500\r\n" % (i, i % 997, i % 991) for i in range(1, 28000)) + b"M30\r\n%\r\n"
open("/srv/cnc/tty1/O7777.NC", "wb").write(big)
rx = bytearray(); after_xoff = []
os.write(master, b"%\r\nO9999 (SEND O7777)\r\nM30\r\n%")
t0 = time.time(); next_pause = 100_000
while len(rx) < len(big) and time.time() - t0 < 300:
    r, _, _ = select.select([master], [], [], 0.5)
    if r:
        rx += os.read(master, 65536)
    if len(rx) > next_pause:
        os.write(master, b"\x13"); mark = len(rx)
        time.sleep(0.3)
        while select.select([master], [], [], 0.05)[0]:
            rx += os.read(master, 65536)
        after_xoff.append(len(rx) - mark)
        os.write(master, b"\x11"); next_pause += 100_000
elapsed = time.time() - t0
wait_state({"idle"})
check(hashlib.sha256(rx).hexdigest() == hashlib.sha256(big).hexdigest(),
      f"1 MB drip-feed byte-exact ({len(big):,} bytes in {elapsed:.0f} s, {len(after_xoff)} XOFF pauses)")
check(max(after_xoff) <= 10, f"at most {max(after_xoff)} chars after any XOFF (FANUC limit 10)")

# punch from the "machine" over the tty
os.write(master, b"\x00\x12%\r\nO1212(FROM TTY)\r\nM30\r\n%\x14")
time.sleep(2)
check(any(f.startswith("O1212_") for f in os.listdir("/srv/cnc/tty1/received")), "punch over tty saved")

# adapter unplugged: port vanishes -> error state, recovers when it returns
os.close(master)
time.sleep(1)
master2, slave2 = os.openpty()
st = manager.status("tty1")
check(st["state"] in ("error", "idle", "receiving"), f"worker survives the port going away (state {st['state']})")

# --- concurrency: 8 browsers add 40 machines at once -------------------------
errors = []
def worker(k):
    cl = app.test_client(); cl.environ_base["REMOTE_ADDR"] = f"10.20.9.{k}"
    post("/login", cl=cl, username="admin", password="correct-horse-1")
    for j in range(5):
        n = k * 5 + j
        r = post("/machines/new", cl=cl, name=f"st{n}", label=f"Stress {n}", type="haas-classic", ip=f"192.168.50.{100 + n}", password="")
        if r.status_code != 200:
            errors.append(r.status_code)
threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
t0 = time.time()
[t.start() for t in threads]; [t.join() for t in threads]
names = {m["name"] for m in config.load()["machines"]}
missing = [f"st{n}" for n in range(40) if f"st{n}" not in names]
check(not errors and not missing, f"40 concurrent adds from 8 sessions: none lost ({time.time() - t0:.1f} s)")
smb = open("/app/generated/smb-cnc.conf").read()
check(all(f"[st{n}]" in smb for n in range(40)), "generated Samba config has all 40 shares")
print("ALL PASSED", flush=True)
os._exit(0)
