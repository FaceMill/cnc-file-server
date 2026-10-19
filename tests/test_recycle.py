"""Recycle folders: retention setting, scheduled emptying by age, Empty now."""
import json, os, re, time
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, events, recycle

config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client(); c.environ_base["REMOTE_ADDR"] = "10.20.5.5"
BASE = "https://localhost"
DAY = 86400
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def csrf():
    c.get(BASE + "/login")
    with c.session_transaction(base_url=BASE) as s:
        return s["csrf"]


def post(path, **data):
    data.setdefault("csrf", csrf())
    return c.post(BASE + path, data=data, follow_redirects=True)


def flashes(r):
    return re.findall(r'<div class="flash \w+">(.*?)</div>', r.get_data(as_text=True), re.S)


post("/login", username="admin", password="correct-horse-1")

# --- setting -----------------------------------------------------------------
page = c.get(BASE + "/settings").get_data(as_text=True)
check('value="60"' in page and "Empty recycle folders now" in page, "Settings page shows the 60-day default")
raw = json.load(open(config.SITE_FILE)); raw.pop("recycle", None); json.dump(raw, open(config.SITE_FILE, "w"))
check(config.load()["recycle"]["retention_days"] == 60, "configs from before this setting default to 60 days")
r = post("/settings/recycle", retention_days="30")
check(config.load()["recycle"]["retention_days"] == 30 and any("older than 30 days" in f for f in flashes(r)), "retention saved (30 days)")
for bad in ["-1", "abc", "", "5000", "2.5"]:
    r = post("/settings/recycle", retention_days=bad)
    check(config.load()["recycle"]["retention_days"] == 30 and flashes(r), f"rejected retention {bad!r}")
post("/settings/recycle", retention_days="0")
check(config.load()["recycle"]["retention_days"] == 0, "0 = automatic emptying off")

# --- deletion time is what counts (rename sets ctime) -------------------------
srv = "/srv/cnc"
open(f"{srv}/haas1/OLD.NC", "w").write("old program")
time.sleep(1.5)
t_delete = time.time()
post("/machines/haas1/delete-file", folder="programs", file="OLD.NC")
st = os.lstat(f"{srv}/haas1/.recycle/OLD.NC")
check(st.st_ctime >= t_delete - 0.5 and st.st_ctime - st.st_mtime >= 1.0,
      f"recycled file's ctime = deletion time (mtime kept): ctime-mtime={st.st_ctime - st.st_mtime:.1f}s")

# --- a Samba-style office recycle tree, a symlink, and normal files -----------
os.makedirs(f"{srv}/.recycle/haas2/sub", exist_ok=True)
open(f"{srv}/haas2/A.NC", "w").write("a" * 1000); os.rename(f"{srv}/haas2/A.NC", f"{srv}/.recycle/haas2/sub/A.NC")
open(f"{srv}/haas2/KEEP.NC", "w").write("live program, not in recycle")
os.makedirs(f"{srv}/haas2/.recycle", exist_ok=True)
os.symlink(f"{srv}/haas2/KEEP.NC", f"{srv}/haas2/.recycle/link-to-live-file")
os.makedirs(f"{srv}/haas2/received", exist_ok=True); open(f"{srv}/haas2/received/R.NC", "w").write("received")
st = recycle.stats()
check(st["files"] == 3 and st["bytes"] >= 1011, f"stats count all recycle folders: {st}")

now = time.time()
check(recycle.purge(older_than_days=60, now=now + 59 * DAY)[0] == 0, "nothing removed before 60 days")
removed, freed = recycle.purge(older_than_days=60, now=now + 61 * DAY)
check(removed == 3, f"everything removed after 60 days ({removed} files, {freed} bytes)")
check(open(f"{srv}/haas2/KEEP.NC").read().startswith("live") and os.path.exists(f"{srv}/haas2/received/R.NC"),
      "symlink removed, its target and files outside .recycle untouched")
check(not os.path.exists(f"{srv}/.recycle/haas2") and os.path.isdir(f"{srv}/.recycle") and os.path.isdir(f"{srv}/haas2/.recycle"),
      "emptied subfolders removed, .recycle folders kept")

# --- scheduled run respects the setting ---------------------------------------
open(f"{srv}/haas1/.recycle/B.NC", "w").write("b")
recycle.run_scheduled()  # setting is 0 -> off
check(os.path.exists(f"{srv}/haas1/.recycle/B.NC"), "scheduled run does nothing when set to 0")
post("/settings/recycle", retention_days="60")
recycle.run_scheduled()
check(os.path.exists(f"{srv}/haas1/.recycle/B.NC"), "scheduled run keeps files newer than 60 days")

# --- Empty now -------------------------------------------------------------------
page = c.get(BASE + "/settings").get_data(as_text=True)
check("Permanently delete all 1 file(s)" in page, "Empty now asks for confirmation with the file count")
r = post("/settings/recycle/empty")
check(any("1 file(s)" in f for f in flashes(r)) and not os.path.exists(f"{srv}/haas1/.recycle/B.NC"), "Empty now removes everything")
check(any("emptied from the web page" in e["message"] for e in events.recent_events(5)), "Empty now is logged")
check("disabled" in c.get(BASE + "/settings").get_data(as_text=True), "button disabled when the recycle folders are empty")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
