"""Health checks, Status-page Health card, alert emails (against a fake SMTP server)."""
import json, os, re, socketserver, threading, time
from collections import namedtuple
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, events, monitor, system

fails = []
MAIL = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class SMTP(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: EHLO/MAIL/RCPT/DATA/QUIT."""
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 fake ESMTP")
        msg = {"rcpt": [], "data": ""}
        while True:
            line = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
            cmd = line[:4].upper()
            if cmd in ("EHLO", "HELO"):
                w("250 fake")
            elif cmd == "MAIL":
                w("250 OK")
            elif cmd == "RCPT":
                msg["rcpt"].append(line); w("250 OK")
            elif cmd == "DATA":
                w("354 go")
                lines = []
                while True:
                    l = self.rfile.readline().decode(errors="replace")
                    if l in (".\r\n", ".\n", ""):
                        break
                    lines.append(l)
                msg["data"] = "".join(lines); MAIL.append(dict(msg)); w("250 queued")
            elif cmd == "QUIT":
                w("221 bye"); return
            elif not line:
                return
            else:
                w("250 OK")


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 2525), SMTP)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

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
    return [re.sub("&#39;", "'", m) for m in re.findall(r'<div class="flash \w+">(.*?)</div>', r.get_data(as_text=True), re.S)]


def keys():
    return {i["key"]: i["level"] for i in monitor.issues()}


def host(**kw):
    d = {"time": int(time.time()), "soc_temperature": 55.0, "throttled": {"under_voltage_now": False},
         "ssd": {"passed": True, "percentage_used": 3, "critical_warning": 0, "media_errors": 0},
         "ntp_synchronized": True, "ups": None, "disks": []}
    d.update(kw)
    json.dump(d, open(monitor.HOST_HEALTH_FILE, "w"))


def backup(result, age=0, msg=""):
    json.dump({"time": int(time.time() - age), "result": result, "message": msg}, open(monitor.BACKUP_STATUS_FILE, "w"))


post("/login", username="admin", password="correct-horse-1")

# --- health rules -----------------------------------------------------------------
k = keys()
check(k.get("host:missing") == "warning" and k.get("backup") == "warning", f"fresh install: host monitor + backups flagged: {k}")
host(); backup("ok")
check(keys() == {}, "healthy host + recent backup: no issues")
host(soc_temperature=86.0)
check(keys().get("temp") == "critical", "86 °C is critical")
host(soc_temperature=81.0, throttled={"under_voltage_now": True})
k = keys(); check(k.get("temp") == "warning" and k.get("power") == "critical", "81 °C warning, under-voltage critical")
host(ssd={"passed": True, "percentage_used": 85, "critical_warning": 0})
check(keys().get("ssd") == "warning", "SSD 85% worn: warning")
host(ssd={"passed": False, "percentage_used": 10, "critical_warning": 0})
check(keys().get("ssd") == "critical", "SSD SMART failed: critical")
host(ntp_synchronized=False, ups={"configured": True, "status": "OB DISCHRG", "on_battery": True, "low_battery": False, "charge": "80"})
k = keys(); check(k.get("time") == "warning" and k.get("ups") == "warning", "clock unsynced + power cut flagged")
host(ups={"configured": True, "status": "OB LB", "on_battery": True, "low_battery": True})
check(keys().get("ups") == "critical", "UPS low battery: critical")
host(time=int(time.time()) - 3600)
check("host:stale" in keys(), "stale host report flagged")
host()
backup("failed", msg="Backup failed: repository unreachable")
check(keys().get("backup") == "critical", "failed backup is critical")
backup("ok", age=40 * 3600)
check(keys().get("backup") == "warning", "backup older than 36 h: warning")
backup("not_configured")
check("not set up" in [i for i in monitor.issues() if i["key"] == "backup"][0]["message"], "unconfigured backups explained")
DU = namedtuple("DU", "total used free")
real_du = system.disk_usage
system.disk_usage = lambda: DU(100, 97, 3)
check(keys().get("disk") == "critical", "3% free storage: critical")
system.disk_usage = real_du
backup("ok"); host()

page = c.get(BASE + "/").get_data(as_text=True)
check("all checks OK" in page, "Status page Health card: all OK")
host(soc_temperature=86.0)
page = c.get(BASE + "/").get_data(as_text=True)
check("overheating" in page and "pill bad" in page, "Status page Health card lists the problem")
host()

# --- alert settings validation ----------------------------------------------------------
base = dict(enabled="on", smtp_host="127.0.0.1", smtp_port="2525", security="none", username="", password="",
            sender="cnc@example.com", recipients="ops@example.com, me@example.com")
for bad, why in [(dict(smtp_host=""), "enabled without a server"), (dict(sender="not-an-email"), "bad sender"),
                 (dict(recipients="ops@example.com\nBcc: evil@example.com"), "header injection via recipients"),
                 (dict(smtp_port="99999"), "port out of range"), (dict(smtp_host="mail.example.com\nX"), "newline in server"),
                 (dict(security="tls1.0"), "unknown security")]:
    r = post("/settings/alerts", **{**base, **bad})
    check(not config.load()["alerts"]["enabled"] and flashes(r), f"rejected: {why}")
r = post("/settings/alerts", **{**base, "password": "smtp-secret"})
a = config.load()["alerts"]
check(a["enabled"] and a["recipients"] == ["ops@example.com", "me@example.com"] and a["password"] == "smtp-secret", "alert settings saved")
post("/settings/alerts", **base)
check(config.load()["alerts"]["password"] == "smtp-secret", "blank password keeps the saved one")
page = c.get(BASE + "/settings").get_data(as_text=True)
check("smtp-secret" not in page, "saved SMTP password never shown in the page")
post("/settings/alerts", **{**base, "clear_password": "on"})
check(config.load()["alerts"]["password"] == "", "clear password")

# --- emails -----------------------------------------------------------------------------
MAIL.clear()
r = post("/settings/alerts/test")
check(any("Test email sent" in f for f in flashes(r)) and len(MAIL) == 1 and "test alert" in MAIL[0]["data"]
      and len(MAIL[0]["rcpt"]) == 2, "test email delivered to both recipients")
if os.path.exists(monitor.STATE_FILE):
    os.remove(monitor.STATE_FILE)
MAIL.clear()
host(soc_temperature=86.0)
monitor.check_and_alert()
check(len(MAIL) == 1 and "new problem" in MAIL[0]["data"] and "overheating" in MAIL[0]["data"], "new problem emailed")
monitor.check_and_alert()
check(len(MAIL) == 1, "no repeat email while nothing changes")
monitor.REMIND_SECONDS = 0
monitor.check_and_alert()
check(len(MAIL) == 2 and "still failing" in MAIL[1]["data"], "critical problem reminder")
monitor.REMIND_SECONDS = 86400
host()
monitor.check_and_alert()
check(len(MAIL) == 3 and "resolved" in MAIL[2]["data"] and "No problems remain" in MAIL[2]["data"], "resolution emailed")
msgs = [e["message"] for e in events.recent_events(10)]
check(any("Health: Pi is overheating" in m for m in msgs) and any("Health OK again" in m for m in msgs), "health changes logged")
post("/settings/alerts", **{**base, "enabled": ""})
host(soc_temperature=86.0); MAIL.clear()
monitor.check_and_alert()
check(not MAIL and any("overheating" in e["message"] for e in events.recent_events(3)), "alerts off: logged but no email")
srv.shutdown()
post("/settings/alerts", **base)
host(); monitor.check_and_alert(); host(soc_temperature=86.0)
monitor.check_and_alert()
check(any("could not be sent" in e["message"] for e in events.recent_events(5)), "mail server down: failure logged, no crash")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
