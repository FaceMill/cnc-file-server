"""Regression checks for the second review's findings."""
import glob, io, json, os, re, time
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, monitor, network, system

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


config.set_admin_password_hash(generate_password_hash("correct-horse-1"))

# --- #4: an unconfirmed change left from a previous run is reverted in the background ----
calls = []
real_set_office = system.nm_set_office
def slow_set_office(office):
    calls.append(("office", office["method"]))
    time.sleep(3)
system.nm_set_office = slow_set_office
site = config.load()
config._write_private(network.PENDING_FILE, json.dumps({"old": site["office"], "new": site["office"], "deadline": time.time()}))
t0 = time.time()
app = create_app()
check(time.time() - t0 < 2, f"start-up isn't blocked by recovering a pending change ({time.time() - t0:.1f} s)")
time.sleep(4)
check(calls and not os.path.exists(network.PENDING_FILE), "the pending change was reverted in the background")

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
    return [re.sub("&#39;", "'", m) for m in re.findall(r'<div class="flash \w+">(.*?)</div>', r.get_data(as_text=True), re.S)]


post("/login", username="admin", password="correct-horse-1")

# --- #5: Revert during the "applying" window wins -----------------------------------
calls.clear()
network.APPLY_DELAY_SECONDS = 1
system.nm_set_office = lambda office: calls.append(office["allowed_subnets"][0])
post("/network/office", method="auto", allowed_subnets="10.20.0.0/16\n10.99.0.0/16")
post("/network/revert")
time.sleep(2.5)
check(network.office_change.pending is None and calls == ["10.20.0.0/16"],
      f"revert during 'applying': the new settings were never applied after it (calls {calls})")
system.nm_set_office = real_set_office

# --- #6: a half-failed CNC address change is rolled back ------------------------------
cnc_calls = []
def flaky_set_cnc(address):
    cnc_calls.append(address)
    if len(cnc_calls) == 1:
        raise RuntimeError("nmcli: connection activation failed")
system.nm_set_cnc = flaky_set_cnc
site = config.load()
form = {"address": "192.168.60.1/24", **{f"ip_{m['name']}": m["ip"].replace("192.168.50.", "192.168.60.") for m in site["machines"] if m["ip"]}}
r = post("/network/cnc", **form)
check(cnc_calls == ["192.168.60.1/24", "192.168.50.1/24"] and any("previous address was restored" in f for f in flashes(r))
      and config.load()["cnc"]["address"] == "192.168.50.1/24", f"failed CNC change rolled back: {cnc_calls}")

# --- #7/#13: something else where a machine's folder should be ------------------------
srv = system.SRV_DIR
os.rmdir(f"{srv}/haas1/received")
open(f"{srv}/haas1/received", "w").write("I am a file now")
os.makedirs("/tmp/outside", exist_ok=True)
os.rmdir(f"{srv}/haas2/received")
os.symlink("/tmp/outside", f"{srv}/haas2/received")
r = post("/network/general", workgroup="WORKGROUP2")
check(any("Workgroup updated" in f for f in flashes(r)), "saving settings still works with a file/symlink in place of received/")
check(os.path.isdir(f"{srv}/haas1/received") and not os.path.islink(f"{srv}/haas1/received")
      and glob.glob(f"{srv}/haas1/received.not-a-folder-*"), "file moved aside, real folder recreated")
check(os.path.isdir(f"{srv}/haas2/received") and not os.path.islink(f"{srv}/haas2/received")
      and os.stat("/tmp/outside").st_uid == 0, "symlink moved aside, its target not chowned")
r = c.post(BASE + "/machines/haas1/upload", data={"csrf": csrf(), "file": (io.BytesIO(b"x"), "received")},
           content_type="multipart/form-data", follow_redirects=True)
check(any("reserved name" in f for f in flashes(r)) and os.path.isdir(f"{srv}/haas1/received"), "upload named 'received' refused")

# --- #12: deleting the same name twice keeps both recycled copies ----------------------
for text in ("first", "second"):
    open(f"{srv}/haas1/SAME.NC", "w").write(text)
    post("/machines/haas1/delete-file", folder="programs", file="SAME.NC")
copies = sorted(open(p).read() for p in glob.glob(f"{srv}/haas1/.recycle/*SAME.NC"))
check(copies == ["first", "second"], f"both recycled copies kept: {copies}")

# --- #3: out-of-range FOCAS program numbers -------------------------------------------
post("/machines/new", name="focas9", label="FOCAS", type="fanuc-focas", ip="192.168.50.39", password="",
     focas_port="8193", focas_timeout="3")
for path in ("/machines/focas9/focas/read", "/machines/focas9/focas/delete"):
    for n in ("66770", "0", "-1", "10000"):
        r = post(path, number=n)
        check(any("1-9999" in f for f in flashes(r)), f"{path.rsplit('/', 1)[-1]} O{n} refused before reaching the CNC")

# --- #15: a damaged alert-state file doesn't stop alerting ------------------------------
for bad in ("{}", "[]", '{"open": []}'):
    open(monitor.STATE_FILE, "w").write(bad)
    try:
        monitor.check_and_alert(); ok = True
    except Exception:
        ok = False
    check(ok and isinstance(json.load(open(monitor.STATE_FILE))["open"], dict), f"alert state {bad!r} recovered")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
