#!/usr/bin/env python3
"""Host health snapshot for the admin page (run by cnc-health.timer every 5 minutes).

Collects what only the host can see: SoC temperature and throttling, SSD wear,
time sync, UPS state and disk space. Writes JSON to <project>/config/host-health.json;
cnc-admin turns it into warnings on the Status page and alert emails.
Standard library only; every probe is optional and never fails the whole run.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

PROJECT_DIR = os.environ.get("PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.environ.get("HEALTH_FILE", os.path.join(PROJECT_DIR, "config", "host-health.json"))
DATA_DIR = os.environ.get("DATA_DIR", "/srv/cnc")
UPS_NAME = os.environ.get("UPS_NAME", "ups@localhost")


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def soc_temperature():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def throttled():
    """vcgencmd get_throttled -> bit flags (0x50005 style). None if not a Pi."""
    out = run(["vcgencmd", "get_throttled"])
    if not out or "=" not in out:
        return None
    value = int(out.strip().split("=")[1], 16)
    return {"raw": hex(value),
            "under_voltage_now": bool(value & 0x1), "freq_capped_now": bool(value & 0x2),
            "throttled_now": bool(value & 0x4), "temp_limit_now": bool(value & 0x8),
            "under_voltage_since_boot": bool(value & 0x10000)}


def ssd():
    """SMART health of the first NVMe (or other) disk via smartctl -j."""
    devices = sorted(glob.glob("/dev/nvme[0-9]")) or sorted(glob.glob("/dev/sd[a-z]"))
    if not devices or not shutil.which("smartctl"):
        return None
    out = run(["smartctl", "-j", "-a", devices[0]], timeout=30)
    if out is None:
        # smartctl uses non-zero exit codes as bit flags even when it produced output
        r = subprocess.run(["smartctl", "-j", "-a", devices[0]], capture_output=True, text=True, timeout=30)
        out = r.stdout
    try:
        d = json.loads(out)
    except (ValueError, TypeError):
        return None
    nvme = d.get("nvme_smart_health_information_log", {})
    return {"device": devices[0], "model": d.get("model_name"),
            "passed": d.get("smart_status", {}).get("passed"),
            "percentage_used": nvme.get("percentage_used"),
            "critical_warning": nvme.get("critical_warning"),
            "media_errors": nvme.get("media_errors"),
            "temperature": d.get("temperature", {}).get("current")}


def time_sync():
    out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    return None if out is None else out.strip() == "yes"


def ups():
    if not shutil.which("upsc"):
        return None
    out = run(["upsc", UPS_NAME])
    if not out:
        return {"configured": False}
    vals = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)
    status = vals.get("ups.status", "")
    return {"configured": True, "status": status, "on_battery": "OB" in status.split(),
            "low_battery": "LB" in status.split(), "charge": vals.get("battery.charge")}


def disk(path):
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "total": u.total, "free": u.free}
    except OSError:
        return None


ERRORS = []


def safe(probe, *args):
    """One broken probe must not lose the whole snapshot: it reports nothing, and the
    error is listed under "errors"."""
    try:
        return probe(*args)
    except Exception as e:  # noqa: BLE001
        ERRORS.append(f"{probe.__name__}: {e.__class__.__name__}: {e}")
        return None


def main():
    snapshot = {
        "time": int(time.time()),
        "soc_temperature": safe(soc_temperature),
        "throttled": safe(throttled),
        "ssd": safe(ssd),
        "ntp_synchronized": safe(time_sync),
        "ups": safe(ups),
        "disks": [d for d in (safe(disk, "/"), safe(disk, DATA_DIR)) if d],
    }
    snapshot["errors"] = ERRORS
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)
    if "-v" in sys.argv:
        print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
