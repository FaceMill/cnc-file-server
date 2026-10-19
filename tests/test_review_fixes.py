"""Regression tests for the review findings (one check per fix)."""
import io, json, os, re, stat, time
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, events, network, render, serial_dnc, system

network.APPLY_DELAY_SECONDS = 0
config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
BASE = "https://localhost"


def client(ip="10.20.5.5"):
    c = app.test_client(); c.environ_base["REMOTE_ADDR"] = ip
    return c


c = client()


def csrf(cl=None):
    cl = cl or c
    cl.get(BASE + "/login")
    with cl.session_transaction(base_url=BASE) as s:
        return s["csrf"]


def post(path, cl=None, follow=True, **data):
    cl = cl or c
    data.setdefault("csrf", csrf(cl))
    return cl.post(BASE + path, data=data, follow_redirects=follow)


def flashes(r):
    return [re.sub("&#39;", "'", m) for m in re.findall(r'<div class="flash \w+">(.*?)</div>', r.get_data(as_text=True), re.S)]


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    assert cond, msg


def mform(**kw):
    base = dict(name="haas9", label="Haas 9", type="haas-classic", ip="192.168.50.19", password="")
    base.update(kw); return base


# --- login: lockout and redirect -------------------------------------------
att = client("10.20.6.6")
for _ in range(5):
    post("/login", cl=att, username="admin", password="wrong")
r = post("/login", cl=att, follow=False, username="admin", password="correct-horse-1")
check(r.status_code == 429, "6th attempt after 5 failures locked out even with the right password")
r = post("/login", cl=client("10.20.7.7"), follow=False, username="admin", password="correct-horse-1")
check(r.status_code == 302, "other client IPs unaffected by the lockout")
for nxt, ok in [("/machines", True), ("//evil.com", False), ("/\\evil.com", False), ("/x:y", False), ("https://evil.com", False)]:
    cl = client("10.20.8.%d" % (len(nxt)))
    r = cl.post(BASE + "/login?next=" + nxt, data={"csrf": csrf(cl), "username": "admin", "password": "correct-horse-1"})
    check((r.headers["Location"] == nxt) == ok, f"login redirect next={nxt!r} -> {r.headers['Location']}")

post("/login", username="admin", password="correct-horse-1")

# --- validation -------------------------------------------------------------
cases = [
    (mform(label="x\n[global]\nguest ok = yes"), "label with line breaks"),
    (mform(label="x" * 61), "label over 60 chars"),
    (mform(name="backup"), "system account name 'backup'"),
    (mform(name="ftp"), "system account name 'ftp'"),
    (mform(password="line\nbreak"), "password with newline"),
    (mform(password="pässwört"), "password with accents"),
    (mform(type="fanuc-rs232", ip="192.168.50.40", serial_transport="tcp", serial_tcp_port="4001", serial_flow="rtscts",
           serial_baud="9600", serial_data_bits="7", serial_parity="E", serial_stop_bits="2", serial_send_delay="1",
           serial_line_delay_ms="0", serial_receive_timeout="1", serial_pause_timeout="30"), "RTS/CTS through a TCP device server"),
    (mform(type="fanuc-rs232", ip="192.168.50.40", serial_transport="tcp", serial_tcp_port="4001", serial_flow="xonxoff",
           serial_baud="9600", serial_data_bits="7", serial_parity="E", serial_stop_bits="2", serial_send_delay="nan",
           serial_line_delay_ms="0", serial_receive_timeout="1", serial_pause_timeout="30"), "NaN send delay"),
    (mform(type="fanuc-rs232", ip="", serial_transport="usb", serial_device="/dev/../app/config/secret_key", serial_flow="xonxoff",
           serial_baud="9600", serial_data_bits="7", serial_parity="E", serial_stop_bits="2", serial_send_delay="1",
           serial_line_delay_ms="0", serial_receive_timeout="1", serial_pause_timeout="30"), "device path with ..")]
for form, why in cases:
    r = post("/machines/new", **form)
    check(not config.find_machine(config.load(), form["name"]) and any(flashes(r)), f"rejected: {why}")
site = config.load()
site["machines"][0]["name"] = "fanuc\n"
try:
    config.validate(site); ok = False
except ValueError:
    ok = True
check(ok, "machine name with trailing newline rejected (fullmatch)")
try:
    render.render_all({**config.load(), "workgroup": "A\nB"}); ok = False
except ValueError:
    ok = True
check(ok, "renderer refuses any value containing a line break (defence in depth)")
site = config.load(); site["office"]["allowed_subnets"] = ["10.0.0.0/255.0.0.0", "10.20.5.5/16"]
config.validate(site)
check(site["office"]["allowed_subnets"] == ["10.0.0.0/8", "10.20.0.0/16"], "subnets normalised to canonical form")

# --- upload / delete (File Browser replacement) ------------------------------
def upload(name, filename, data, **kw):
    return c.post(BASE + f"/machines/{name}/upload", data={"csrf": csrf(), "file": (io.BytesIO(data), filename), **kw},
                  content_type="multipart/form-data", follow_redirects=True)

r = upload("haas1", "O5000.NC", b"%\nO5000\nM30\n%\n")
p = "/srv/cnc/haas1/O5000.NC"
st = os.stat(p)
check(os.path.isfile(p) and stat.S_IMODE(st.st_mode) == 0o664 and (st.st_uid, st.st_gid) == (2000, 2000),
      f"upload saved 0664 owned by cncfiles: {oct(stat.S_IMODE(st.st_mode))} {st.st_uid}:{st.st_gid}")
r = upload("haas1", "O5000.NC", b"NEW")
check(any("already exists" in f for f in flashes(r)) and open(p, "rb").read().startswith(b"%"), "upload won't overwrite without 'replace'")
r = upload("haas1", "O5000.NC", b"NEW", overwrite="on")
check(open(p, "rb").read() == b"NEW" and os.path.exists("/srv/cnc/haas1/.recycle/O5000.NC"), "replace keeps the old copy in .recycle")
r = upload("haas1", "../../etc/evil.NC", b"x")
check(os.path.exists("/srv/cnc/haas1/etc_evil.NC") and not os.path.exists("/srv/etc/evil.NC"), "upload name with ../ sanitised")
r = upload("haas1", ".hidden", b"x")
check(not os.path.exists("/srv/cnc/haas1/.hidden"), "hidden file names refused")
r = c.post(BASE + "/machines/haas1/upload", data={"csrf": csrf(), "file": (io.BytesIO(b"x" * (17 * 1024 * 1024)), "BIG.NC")},
           content_type="multipart/form-data")
check(r.status_code == 413, "uploads over 16 MB refused")
r = post("/machines/haas1/delete-file", folder="programs", file="etc_evil.NC")
check(not os.path.exists("/srv/cnc/haas1/etc_evil.NC") and os.path.exists("/srv/cnc/haas1/.recycle/etc_evil.NC"), "delete moves to .recycle")
r = post("/machines/haas1/delete-file", folder="programs", file="../../../app/config/site.json")
check(os.path.exists("/app/config/site.json") and any("Invalid" in f or "not found" in f for f in flashes(r)), "delete path traversal refused")
d = "/srv/cnc/haas1/received"
check(os.path.isdir(d) and stat.S_IMODE(os.stat(d).st_mode) == 0o770 and os.stat(d).st_uid == 2000, "received/ created 0770 owned by cncfiles")
page = c.get(BASE + "/machines/haas1").get_data(as_text=True)
check('enctype="multipart/form-data"' in page and "Move O5000.NC to the recycle folder?" in page, "machine page has upload form and delete buttons")

# --- XSS: confirm() text comes from a data attribute ------------------------
site = config.load(); config.find_machine(site, "haas2")["label"] = "Bob's Haas\"');alert(1);//"; config.save(site)
page = c.get(BASE + "/machines/haas2/edit").get_data(as_text=True)
check("confirm(this.dataset.msg)" in page and "confirm('" not in page and "alert(1)" in page and '\');alert' not in page,
      "label with quotes can't break out of the confirm() handler")

# --- pending actions survive a failed apply ---------------------------------
calls, real_restart = [], system.restart_container
def flaky(name):
    calls.append(name)
    if len(calls) == 1:
        raise RuntimeError("docker daemon hiccup")
system.restart_container = flaky
r = post("/machines/new", **mform(name="haas7", ip="192.168.50.17"))
check(any("applying failed" in f for f in flashes(r)), "failed restart reported")
pending = json.load(open("/app/generated/.pending-actions.json"))
check("samba-cnc" in pending, f"failed action remembered: {pending}")
r = post("/network/general", workgroup="WORKGROUP")
check(calls.count("samba-cnc") == 2 and json.load(open("/app/generated/.pending-actions.json")) == [],
      "next save retried the restart and cleared it")
system.restart_container = real_restart

# --- office network change: confirm/revert edge cases -----------------------
oc = network.office_change
real_set = system.nm_set_office
gate = __import__("threading").Event()
def slow_set(office):
    gate.wait(5)
system.nm_set_office = slow_set
r = post("/network/office", method="auto", allowed_subnets="10.20.0.0/16")
st = oc.status()
check(st and st["applying"] and st["seconds_left"] is None, "countdown doesn't start until the change is applied")
r = post("/network/confirm")
check(any("Nothing to confirm" in f for f in flashes(r)) and oc.pending, "confirm while still applying is refused, not falsely reported")
gate.set(); time.sleep(0.5)
check(oc.status()["seconds_left"] > 100, "countdown starts after apply")
def broken_set(office):
    raise RuntimeError("nmcli: connection activation failed")
system.nm_set_office = broken_set
r = post("/network/revert")
check(oc.pending is not None and os.path.exists(network.PENDING_FILE) and "FAILED" in (oc.last_error or ""),
      "failed revert keeps the pending change for retry and says so")
system.nm_set_office = real_set
oc.revert()
check(oc.pending is None and not os.path.exists(network.PENDING_FILE), "revert succeeds once the network is back")

# --- serial: buffer cap, sends refused while the port is down ---------------
serial_dnc.MAX_PUNCH = 1000
w = serial_dnc.MachineWorker({"id": "t", "name": "t", "port": "loop://", "baudrate": 9600, "bytesize": 7, "parity": "E",
                              "stopbits": "2", "flow_control": "none", "send_delay": 0, "line_delay_ms": 0,
                              "receive_timeout": 30, "pause_timeout": 30, "excluded_ext": "", "program_dir": "haas1",
                              "receive_dir": "haas1/received"})
w.start(); time.sleep(0.5)
w.ser.write(b"%" + b"G01X1\n" * 400)  # loop:// echoes back: 2400 bytes, no closing %
time.sleep(1.5)
check(any("Discarded" in e["message"] for e in events.recent_events(10, "t")), "oversized punch without % discarded and logged")
w.stop(); w.join(3)
r = post("/machines/new", **mform(name="dnc8", type="fanuc-rs232", ip="192.168.50.88", serial_transport="tcp", serial_tcp_port="4001",
                                  serial_enabled="on", serial_baud="9600", serial_data_bits="7", serial_parity="E", serial_stop_bits="2",
                                  serial_flow="xonxoff", serial_send_delay="1", serial_line_delay_ms="0", serial_receive_timeout="1",
                                  serial_pause_timeout="30"))
time.sleep(1.5)
open("/srv/cnc/dnc8/O1.NC", "w").write("%\nO1\nM30\n%\n")
r = post("/machines/dnc8/dnc/send", filename="O1.NC", delay="0")
check(any("isn't open" in f for f in flashes(r)), "manual send refused while the serial port can't be opened")
print("ALL PASSED")
os._exit(0)
