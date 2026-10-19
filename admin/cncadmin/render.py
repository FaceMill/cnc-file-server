"""Renders generated/* (Samba/FTP configs, user lists, firewall) from the site config."""
import hashlib
import hmac
import os
import subprocess

from jinja2 import Environment, PackageLoader, StrictUndefined

from . import config
from .ntlm import nt_hash

GENERATED_DIR = os.environ.get("CNC_GENERATED_DIR", "/app/generated")
FTP_PASSIVE_PORTS = (30000, 30049)

def _single_line(value):
    """Defence in depth: no value may add lines to a config file (validation should stop it first)."""
    text = "" if value is None else str(value)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError(f"refusing to render a value containing control characters: {text!r}")
    return text


_env = Environment(loader=PackageLoader("cncadmin", "conf_templates"),
                   trim_blocks=True, lstrip_blocks=True, finalize=_single_line,
                   keep_trailing_newline=True, undefined=StrictUndefined)

# Which generated files each consumer depends on.
SAMBA_OFFICE_FILES = {"smb-office.conf", "office-users.txt"}
SAMBA_CNC_FILES = {"smb-cnc.conf", "cnc-users.txt"}
FTP_FILES = {"vsftpd.conf", "ftp-users.txt"}
FIREWALL_FILE = "nftables.conf"


def sha512_crypt(name, password):
    """Linux shadow hash for an FTP user. The salt is derived per user so the
    output is stable across renders (unchanged passwords don't restart FTP)."""
    salt = hmac.new(config.secret_key().encode(), name.encode(), hashlib.sha256).hexdigest()[:16]
    res = subprocess.run(["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
                         input=password, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def render_all(site):
    """Return {filename: (content, mode)}."""
    smb = config.machines_by_protocol(site, "smb")
    ftp = config.machines_by_protocol(site, "ftp")
    serial_tcp = [m for m in config.machines_by_protocol(site, "serial") if m["serial"]["transport"] == "tcp"]
    ctx = {"site": site, "office": site["office"], "cnc": site["cnc"],
           "office_users": site["office_users"], "smb_machines": smb, "ftp_machines": ftp,
           "serial_tcp_machines": serial_tcp, "cnc_ip": config.cnc_server_ip(site),
           "pasv_min": FTP_PASSIVE_PORTS[0], "pasv_max": FTP_PASSIVE_PORTS[1]}

    def tpl(name):
        return _env.get_template(name).render(ctx)

    return {
        "smb-office.conf": (tpl("smb-office.conf.j2"), 0o644),
        "smb-cnc.conf": (tpl("smb-cnc.conf.j2"), 0o644),
        "vsftpd.conf": (tpl("vsftpd.conf.j2"), 0o644),
        "nftables.conf": (tpl("nftables.conf.j2"), 0o644),
        "office-users.txt": ("".join(f"{u['name']} {u['nt_hash']}\n" for u in site["office_users"]), 0o600),
        "cnc-users.txt": ("".join(f"{m['name']} {nt_hash(m['password'])}\n" for m in smb), 0o600),
        "ftp-users.txt": ("".join(f"{m['name']} {sha512_crypt(m['name'], m['password'])} {m['ip']}\n" for m in ftp), 0o600),
    }


def write_all(files, directory=GENERATED_DIR):
    """Write rendered files atomically; return the set of names whose content changed."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    changed = set()
    for name, (content, mode) in files.items():
        path = os.path.join(directory, name)
        try:
            with open(path) as f:
                if f.read() == content:
                    continue
        except FileNotFoundError:
            pass
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        changed.add(name)
    return changed


def path(name):
    return os.path.join(GENERATED_DIR, name)
