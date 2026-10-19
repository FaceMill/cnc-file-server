"""Host-side actions: firewall, NetworkManager, Docker containers, machine folders, status.

Set CNC_ADMIN_DRY_RUN=1 to log host-changing actions instead of running them
(for development off the Pi).
"""
import datetime
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from . import render, serial_dnc

log = logging.getLogger("cncadmin")

DRY_RUN = os.environ.get("CNC_ADMIN_DRY_RUN") == "1"
SRV_DIR = os.environ.get("CNC_SRV_DIR", "/srv/cnc")
REMOVED_DIR = os.path.join(SRV_DIR, ".removed")
CNC_UID = int(os.environ.get("CNC_UID", "2000"))
CNC_GID = int(os.environ.get("CNC_GID", "2000"))

OFFICE_CONN = "office-net"
CNC_CONN = "cnc-net"
SAMBA_OFFICE = "samba-office"
SAMBA_CNC = "samba-cnc"
FTP_CNC = "ftp-cnc"
SERVICES = [SAMBA_OFFICE, SAMBA_CNC, FTP_CNC, "focas-bridge", "filebrowser", "caddy", "portainer", "cnc-admin"]


def run(cmd, *, host_change=True):
    if DRY_RUN and host_change:
        log.info("DRY RUN: %s", " ".join(cmd))
        return ""
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout


# --- apply ------------------------------------------------------------------

def apply_site(site):
    """Render generated files for `site` and apply whatever changed.

    Returns a list of human-readable actions taken.
    """
    files = render.render_all(site)
    check_firewall(files[render.FIREWALL_FILE][0])
    for m in site["machines"]:
        ensure_machine_dir(m["name"])
    changed = render.write_all(files)

    # Actions still owed from an earlier apply that failed part-way are retried:
    # the files are already on disk, so they no longer show up as "changed".
    pending = _load_pending()
    if render.FIREWALL_FILE in changed:
        pending.add("firewall")
    if changed & render.SAMBA_CNC_FILES:
        pending.add(SAMBA_CNC)
    if changed & render.FTP_FILES:
        pending.add(FTP_CNC)
    if changed & render.SAMBA_OFFICE_FILES:
        pending.add(SAMBA_OFFICE)
    _save_pending(pending)

    actions = []
    steps = [("firewall", "firewall reloaded", lambda: run(["nft", "-f", render.path(render.FIREWALL_FILE)])),
             (SAMBA_CNC, "CNC file server restarted", lambda: restart_container(SAMBA_CNC)),
             (FTP_CNC, "CNC FTP server restarted", lambda: restart_container(FTP_CNC)),
             (SAMBA_OFFICE, "office file server restarted", lambda: restart_container(SAMBA_OFFICE))]
    for key, done, step in steps:
        if key in pending:
            step()
            pending.discard(key)
            _save_pending(pending)
            actions.append(done)
    serial_dnc.manager.sync(site)
    return actions


def _pending_path():
    return render.path(".pending-actions.json")


def _load_pending():
    try:
        with open(_pending_path()) as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def _save_pending(pending):
    tmp = _pending_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(pending), f)
    os.replace(tmp, _pending_path())


def check_firewall(ruleset):
    if DRY_RUN:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".nft") as f:
        f.write(ruleset)
        f.flush()
        run(["nft", "-c", "-f", f.name], host_change=False)


# --- machine folders --------------------------------------------------------

def ensure_machine_dir(name):
    """The machine's folder and its received/ subfolder, owned like everything Samba writes."""
    make_shared_dir(os.path.join(SRV_DIR, name))
    make_shared_dir(os.path.join(SRV_DIR, name, "received"))


def make_shared_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o770)
    if os.geteuid() == 0:
        st = os.stat(path)
        if (st.st_uid, st.st_gid) != (CNC_UID, CNC_GID):
            os.chown(path, CNC_UID, CNC_GID)


def write_shared_file(path, data):
    """Write a file under /srv/cnc so office users (Samba as cncfiles) can edit and delete it."""
    make_shared_dir(os.path.dirname(path))
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o664), "wb") as f:
        f.write(data)
    os.chmod(path, 0o664)
    if os.geteuid() == 0:
        os.chown(path, CNC_UID, CNC_GID)


def archive_machine_dir(name):
    """Move a removed machine's folder aside instead of deleting it; return the new path."""
    path = os.path.join(SRV_DIR, name)
    if not os.path.isdir(path):
        return None
    os.makedirs(REMOVED_DIR, mode=0o770, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(REMOVED_DIR, f"{name}-{stamp}")
    os.rename(path, dest)
    return dest


# --- NetworkManager (host, via D-Bus socket) --------------------------------

def nm_set_office(office):
    if office["method"] == "auto":
        args = ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""]
    else:
        args = ["ipv4.method", "manual", "ipv4.addresses", office["address"],
                "ipv4.gateway", office["gateway"], "ipv4.dns", " ".join(office["dns"])]
    run(["nmcli", "connection", "modify", OFFICE_CONN, *args])
    run(["nmcli", "connection", "up", OFFICE_CONN])


def nm_set_cnc(address):
    run(["nmcli", "connection", "modify", CNC_CONN, "ipv4.addresses", address])
    run(["nmcli", "connection", "up", CNC_CONN])


# --- Docker -----------------------------------------------------------------

def _docker():
    import docker
    return docker.from_env()


def restart_container(name):
    if DRY_RUN:
        log.info("DRY RUN: restart container %s", name)
        return
    _docker().containers.get(name).restart(timeout=10)


def container_states():
    try:
        client = _docker()
    except Exception:
        return {name: "unknown" for name in SERVICES}
    states = {}
    for name in SERVICES:
        try:
            states[name] = client.containers.get(name).status
        except Exception:
            states[name] = "missing"
    return states


def container_logs(name, tail=300):
    try:
        return _docker().containers.get(name).logs(tail=tail, timestamps=True).decode(errors="replace")
    except Exception as e:
        return f"Could not read logs: {e}"


def smb_sessions():
    """Usernames currently connected to the CNC-side Samba."""
    try:
        res = _docker().containers.get(SAMBA_CNC).exec_run(["smbstatus", "-b", "--json"])
        data = json.loads(res.output)
        return {s.get("username") for s in data.get("sessions", {}).values()}
    except Exception:
        return set()


def ftp_sessions():
    """Usernames currently logged in to the CNC-side FTP server."""
    try:
        res = _docker().containers.get(FTP_CNC).exec_run(["ps", "-eo", "args"])
        # vsftpd process titles look like "vsftpd: 192.168.50.21/fanuc1: IDLE"
        return set(re.findall(r"vsftpd: [\d.]+/([a-z0-9]+):", res.output.decode(errors="replace")))
    except Exception:
        return set()


def machine_files(name):
    """Files in a machine's folder (top level, no hidden files), newest first."""
    path = os.path.join(SRV_DIR, name)
    try:
        entries = [e for e in os.scandir(path) if e.is_file() and not e.name.startswith(".")]
    except FileNotFoundError:
        return []
    files = [{"name": e.name, "size": e.stat().st_size,
              "mtime": datetime.datetime.fromtimestamp(e.stat().st_mtime)} for e in entries]
    return sorted(files, key=lambda f: f["mtime"], reverse=True)


# --- status -----------------------------------------------------------------

def ping_many(ips):
    def ping(ip):
        return subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                              capture_output=True).returncode == 0
    with ThreadPoolExecutor(max_workers=max(1, len(ips))) as pool:
        return dict(zip(ips, pool.map(ping, ips)))


def interfaces():
    """{ifname: {"state": ..., "addresses": ["a.b.c.d/nn", ...]}}"""
    try:
        data = json.loads(run(["ip", "-j", "addr", "show"], host_change=False))
    except Exception:
        return {}
    return {
        i["ifname"]: {
            "state": i.get("operstate", "UNKNOWN"),
            "addresses": [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])
                          if a.get("family") == "inet"],
        }
        for i in data
    }


def interface_subnet(ifname):
    addrs = interfaces().get(ifname, {}).get("addresses", [])
    return str(ipaddress.ip_interface(addrs[0]).network) if addrs else None


def disk_usage():
    try:
        return shutil.disk_usage(SRV_DIR)
    except FileNotFoundError:
        return None

