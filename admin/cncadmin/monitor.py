"""Health checks for the Status page, and alert emails.

Combines what cnc-admin can see itself (services, disk, backups, serial ports) with
the host snapshot written by host/health.py (temperature, power, SSD, time, UPS).
Every CHECK_SECONDS the issues are compared with the last run; new and resolved
problems are logged and, if enabled, emailed. Critical problems are re-sent daily.
"""
import datetime
import json
import logging
import os
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

from . import config, events, serial_dnc, system

log = logging.getLogger("cncadmin")

CHECK_SECONDS = 300
FIRST_CHECK_SECONDS = 60
REMIND_SECONDS = 24 * 3600
HOST_HEALTH_FILE = os.path.join(config.CONFIG_DIR, "host-health.json")
BACKUP_STATUS_FILE = os.path.join(config.CONFIG_DIR, "backup-status.json")
STATE_FILE = os.path.join(config.CONFIG_DIR, "alert-state.json")
REQUIRED_SERVICES = ["samba-office", "samba-cnc", "ftp-cnc", "focas-bridge", "caddy", "cnc-admin"]
WARN, CRIT = "warning", "critical"


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _age(ts):
    return time.time() - ts if ts else None


def _hours(seconds):
    return f"{seconds / 3600:.0f} h" if seconds < 172800 else f"{seconds / 86400:.0f} days"


def issues():
    """Current problems: [{"key", "level", "message"}], most severe first."""
    out = []

    def add(key, level, message):
        out.append({"key": key, "level": level, "message": message})

    # Services (skipped in dry-run/dev, where the containers don't exist)
    if not system.DRY_RUN:
        states = system.container_states()
        for name in REQUIRED_SERVICES:
            if states.get(name) not in ("running", "unknown"):
                add(f"service:{name}", CRIT, f"Service {name} is {states.get(name)}")

    # Program storage
    du = system.disk_usage()
    if du:
        pct = 100 * du.free / du.total
        if pct < 5:
            add("disk", CRIT, f"Program storage almost full: {pct:.0f}% free")
        elif pct < 10:
            add("disk", WARN, f"Program storage low: {pct:.0f}% free")

    # Host snapshot (host/health.py)
    h = _read_json(HOST_HEALTH_FILE)
    if h is None:
        add("host:missing", WARN, "Host health monitor hasn't reported (cnc-health.timer)")
    else:
        age = _age(h.get("time"))
        if age is not None and age > 20 * 60:
            add("host:stale", WARN, f"Host health report is {_hours(age)} old (cnc-health.timer)")
        t = h.get("soc_temperature")
        t = t if isinstance(t, (int, float)) else None
        if t is not None and t >= 85:
            add("temp", CRIT, f"Pi is overheating: {t:.0f} °C (check the cooler)")
        elif t is not None and t >= 80:
            add("temp", WARN, f"Pi is running hot: {t:.0f} °C")
        th = h.get("throttled") or {}
        if th.get("under_voltage_now"):
            add("power", CRIT, "Under-voltage: the power supply is too weak or failing")
        elif th.get("throttled_now") or th.get("freq_capped_now"):
            add("throttle", WARN, "Pi is throttling (heat or power)")
        s = h.get("ssd") or {}
        if s.get("passed") is False or s.get("critical_warning"):
            add("ssd", CRIT, f"SSD health check failed ({s.get('model') or s.get('device')}): replace it and restore from backup")
        elif (s.get("percentage_used") or 0) >= 95:
            add("ssd", CRIT, f"SSD nearly worn out: {s['percentage_used']}% of rated life used")
        elif (s.get("percentage_used") or 0) >= 80:
            add("ssd", WARN, f"SSD wear: {s['percentage_used']}% of rated life used")
        elif (s.get("media_errors") or 0) > 0:
            add("ssd", WARN, f"SSD reports {s['media_errors']} media error(s)")
        if h.get("ntp_synchronized") is False:
            add("time", WARN, "Clock not synchronised (NTP): file times may be wrong")
        u = h.get("ups") or {}
        if u.get("low_battery"):
            add("ups", CRIT, "UPS battery low: the Pi will shut down")
        elif u.get("on_battery"):
            add("ups", WARN, f"Power cut: running on UPS battery ({u.get('charge') or '?'}%)")

    # Backups (host/backup.sh)
    b = _read_json(BACKUP_STATUS_FILE)
    if b is None:
        add("backup", WARN, "Backups have never run")
    elif b.get("result") == "not_configured":
        add("backup", WARN, "Backups are not set up (sudo host/setup-backup.sh)")
    elif b.get("result") == "failed":
        add("backup", CRIT, f"Last backup FAILED: {b.get('message', '')}")
    elif (_age(b.get("time")) or 0) > 36 * 3600:
        add("backup", WARN, f"Last successful backup was {_hours(_age(b['time']))} ago")

    # Serial ports
    for name, w in list(serial_dnc.manager.workers.items()):
        if w.state == "error":
            add(f"serial:{name}", WARN, f"{w.m['name']}: serial port problem ({w.detail})")

    return sorted(out, key=lambda i: (i["level"] != CRIT, i["key"]))


def host_snapshot():
    return _read_json(HOST_HEALTH_FILE)


def backup_status():
    return _read_json(BACKUP_STATUS_FILE)


# --- email ------------------------------------------------------------------

def send_email(alerts, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = alerts["sender"]
    msg["To"] = ", ".join(alerts["recipients"])
    msg.set_content(body)
    port, host = alerts["smtp_port"], alerts["smtp_host"]
    if alerts["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=20)
    with smtp:
        if alerts["security"] == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if alerts["username"]:
            smtp.login(alerts["username"], alerts["password"])
        smtp.send_message(msg)


def server_name():
    return os.environ.get("OFFICE_HOSTNAME") or "CNC file server"


def _body(intro, items, current, resolved=()):
    lines = [intro, ""] + [f"  [{i['level'].upper()}] {i['message']}" for i in items]
    if resolved:
        lines += ["", "Resolved:"] + [f"  {i['message']}" for i in resolved]
    if current:
        lines += ["", "Everything currently wrong:"] + [f"  [{i['level'].upper()}] {i['message']}" for i in current]
    else:
        lines += ["", "No problems remain."]
    lines += ["", f"Status page: https://{server_name()}:8443/", f"Checked {datetime.datetime.now():%Y-%m-%d %H:%M}"]
    return "\n".join(lines)


def check_and_alert():
    """One monitoring round: log and email new, resolved and long-running critical problems."""
    current = issues()
    state = _read_json(STATE_FILE)
    if not isinstance(state, dict) or not isinstance(state.get("open"), dict):
        state = {"open": {}, "reminded": 0}  # missing or damaged: start fresh rather than stop alerting
    before = state["open"]
    now_open = {i["key"]: i for i in current}
    new = [i for k, i in now_open.items() if k not in before or before[k]["level"] != i["level"]]
    resolved = [before[k] for k in before if k not in now_open]
    for i in new:
        events.log_event(None, "error" if i["level"] == CRIT else "info", f"Health: {i['message']}")
    for i in resolved:
        events.log_event(None, "info", f"Health OK again: {i['message']}")

    emails = []
    if new:
        emails.append((f"[{server_name()}] {len(new)} new problem(s)", _body("New problem(s):", new, current, resolved)))
    elif resolved:
        emails.append((f"[{server_name()}] resolved: {len(resolved)} problem(s)", _body("Resolved:", resolved, current)))
    crit = [i for i in current if i["level"] == CRIT]
    if crit and not new and time.time() - state.get("reminded", 0) > REMIND_SECONDS:
        emails.append((f"[{server_name()}] still failing: {len(crit)} critical problem(s)", _body("Still failing:", crit, current)))
        state["reminded"] = time.time()
    if new:
        state["reminded"] = time.time()

    alerts = config.load()["alerts"]
    if alerts["enabled"]:
        for subject, body in emails:
            try:
                send_email(alerts, subject, body)
            except Exception as e:
                log.warning("Alert email failed: %s", e)
                events.log_event(None, "error", f"Alert email could not be sent: {e}")
    state["open"] = now_open
    config._write_private(STATE_FILE, json.dumps(state))
    return current


def _loop():
    time.sleep(FIRST_CHECK_SECONDS)
    while True:
        try:
            if config.exists():
                check_and_alert()
        except Exception:
            log.exception("Health check failed")
        time.sleep(CHECK_SECONDS)


_started = False


def start():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="health-monitor").start()
