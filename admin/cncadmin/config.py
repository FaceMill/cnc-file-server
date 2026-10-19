"""Site configuration (config/site.json): the single source of truth.

Everything else (Samba configs, user lists, firewall rules) is generated from it.
"""
import ipaddress
import math
import json
import os
import re
import secrets
import threading

CONFIG_DIR = os.environ.get("CNC_CONFIG_DIR", "/app/config")
SITE_FILE = os.path.join(CONFIG_DIR, "site.json")
ADMIN_FILE = os.path.join(CONFIG_DIR, "admin.json")
SECRET_KEY_FILE = os.path.join(CONFIG_DIR, "secret_key")
FOCAS_TOKEN_FILE = os.path.join(CONFIG_DIR, "focas-token")

# Held around every load-modify-save-apply sequence.
lock = threading.RLock()

PROTOCOLS = {
    "smb": "Network share (SMB): machine maps a drive on the Pi",
    "ftp": "FTP: machine connects to the Pi's FTP server",
    "serial": "RS-232 serial DNC: Pi sends/receives over a serial line",
    "focas": "FOCAS: Pi reads/writes programs in CNC memory over Ethernet",
}

MACHINE_TYPES = {
    "haas-classic": {"label": "Haas Classic control", "protocol": "smb"},
    "haas-ngc": {"label": "Haas Next Generation Control (NGC)", "protocol": "smb"},
    "siemens-840d-pcu50": {"label": "Siemens 840D powerline (PCU 50, Windows XP)", "protocol": "smb"},
    "siemens-operate": {"label": "Siemens 840D sl / 828D (SINUMERIK Operate)", "protocol": "smb"},
    "heidenhain-tnc": {"label": "Heidenhain TNC / iTNC", "protocol": "smb"},
    "windows-cnc": {"label": "Windows-based control (Mazak Matrix/Smooth, Okuma OSP-P, Hurco, ...)", "protocol": "smb"},
    "fanuc-dataserver": {"label": "FANUC with Data Server (FTP)", "protocol": "ftp"},
    "fanuc-embedded": {"label": "FANUC embedded Ethernet, no Data Server (FTP)", "protocol": "ftp"},
    "ftp-other": {"label": "Other FTP-capable control (Mitsubishi M70/M80, Brother, ...)", "protocol": "ftp"},
    "fanuc-focas": {"label": "FANUC with Ethernet, via FOCAS (0i-D/F, 16i/18i/21i, 30i/31i/32i)", "protocol": "focas"},
    "fanuc-rs232": {"label": "FANUC RS-232 (no Ethernet)", "protocol": "serial"},
    "serial-other": {"label": "Other RS-232 control (older Haas, Fadal, Mazak, Okuma, ...)", "protocol": "serial"},
}

# RS-232 DNC settings (engine from serial-dnc). The machine requests programs by
# punching "(SEND name)"; anything else it punches is saved to <machine>/received/.
SERIAL_DEFAULTS = {
    "enabled": True,         # listen on the port
    "transport": "usb",      # usb: adapter on the Pi; tcp: serial device server on the CNC network
    "device": "",            # /dev/serial/by-id/... (usb)
    "tcp_port": 4001,        # raw TCP port of the device server (tcp)
    "baud": 9600,
    "data_bits": 7,
    "parity": "E",
    "stop_bits": "2",
    "flow": "xonxoff",
    "send_delay": 10.0,      # seconds between a request and the start of sending
    "line_delay_ms": 0,      # pause after every line; 0 unless the machine overruns
    "receive_timeout": 3.0,  # seconds of silence that end a punch without a closing %
    "pause_timeout": 30.0,   # minutes paused by XOFF/CTS before a send is aborted; 0 = never
    "excluded_ext": "",      # file types never listed or sent, e.g. "bak, txt"
}
SERIAL_CHOICES = {
    "transport": ["usb", "tcp"],
    "baud": [300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200],
    "data_bits": [5, 6, 7, 8],
    "parity": ["E", "N", "O", "M", "S"],
    "stop_bits": ["1", "1.5", "2"],
    "flow": ["xonxoff", "rtscts", "dsrdtr", "none"],
}
RECEIVED_SUBDIR = "received"

# FOCAS: the Pi connects to the CNC (TCP 8193) through the focas-bridge container.
FOCAS_DEFAULTS = {
    "port": 8193,
    "timeout": 10,           # seconds to wait for the CNC to answer
    "replace": False,        # Send overwrites a program with the same O-number
}


def normalize_focas(f):
    return {**FOCAS_DEFAULTS, **{k: v for k, v in (f or {}).items() if k in FOCAS_DEFAULTS}}


def normalize_serial(s):
    """Fill defaults and drop settings from older versions of the config."""
    out = {**SERIAL_DEFAULTS, **{k: v for k, v in (s or {}).items() if k in SERIAL_DEFAULTS}}
    out["stop_bits"] = str(out["stop_bits"])
    return out


# Machine names double as SMB share and user names; old controls want them short.
# Always used with fullmatch(): "$" alone also matches before a trailing newline.
MACHINE_NAME_RE = re.compile(r"[a-z][a-z0-9]{0,11}")
USER_NAME_RE = re.compile(r"[a-z][a-z0-9._-]{0,19}")
WORKGROUP_RE = re.compile(r"[A-Za-z0-9_-]{1,15}")
LABEL_MAX = 60
# Printable ASCII without spaces: old controls can type these, and no line breaks reach config files.
PASSWORD_RE = re.compile(r"[\x21-\x7e]{1,14}")
DEVICE_RE = re.compile(r"/dev/(?!.*\.\.)[A-Za-z0-9._:+/-]+")
EXCLUDED_EXT_RE = re.compile(r"[a-z0-9]+(, [a-z0-9]+)*|")
RESERVED_NAMES = {"global", "homes", "printers", "print", "ipc", "cnc", "root",
                  "nobody", "cncfiles", "admin", "removed", "received"}
# Accounts that already exist in the Debian images: useradd would fail on them and
# take a file server down.
SYSTEM_ACCOUNTS = {"daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp",
                   "proxy", "www-data", "backup", "list", "irc", "gnats", "_apt", "ftp", "sshd",
                   "messagebus", "systemd-network", "systemd-resolve", "systemd-timesync", "polkitd"}
RESERVED_NAMES |= SYSTEM_ACCOUNTS
PASSWORD_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_password(length=8):
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(length))


def default_site(office_subnet, office_if="eth0", cnc_if="eth1"):
    return {
        "workgroup": "WORKGROUP",
        "office": {
            "interface": office_if,
            "method": "auto",
            "address": "",
            "gateway": "",
            "dns": [],
            "allowed_subnets": [office_subnet],
        },
        "cnc": {"interface": cnc_if, "address": "192.168.50.1/24"},
        "machines": [
            {"name": "haas1", "label": "Haas #1", "type": "haas-classic",
             "ip": "192.168.50.11", "password": gen_password()},
            {"name": "haas2", "label": "Haas #2", "type": "haas-classic",
             "ip": "192.168.50.12", "password": gen_password()},
            {"name": "siemens1", "label": "Siemens 840D", "type": "siemens-840d-pcu50",
             "ip": "192.168.50.13", "password": gen_password()},
        ],
        "office_users": [],
    }


def exists():
    return os.path.exists(SITE_FILE)


def load():
    with open(SITE_FILE) as f:
        site = json.load(f)
    for m in site["machines"]:
        if "serial" in m:
            m["serial"] = normalize_serial(m["serial"])
        if "focas" in m:
            m["focas"] = normalize_focas(m["focas"])
    return site


def save(site):
    validate(site)
    _write_private(SITE_FILE, json.dumps(site, indent=2) + "\n")


def _write_private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(text)
    os.replace(tmp, path)


def cnc_network(site):
    return ipaddress.ip_interface(site["cnc"]["address"]).network


def cnc_server_ip(site):
    return str(ipaddress.ip_interface(site["cnc"]["address"]).ip)


def protocol(machine):
    return MACHINE_TYPES[machine["type"]]["protocol"]


def machines_by_protocol(site, proto):
    return [m for m in site["machines"] if protocol(m) == proto]


def find_machine(site, name):
    return next((m for m in site["machines"] if m["name"] == name), None)


def find_office_user(site, name):
    return next((u for u in site["office_users"] if u["name"] == name), None)


# --- validation -------------------------------------------------------------

def _ipv4(value, what):
    try:
        return ipaddress.IPv4Address(value)
    except ValueError:
        raise ValueError(f"{what}: '{value}' is not a valid IPv4 address")


def _ipv4_iface(value, what):
    try:
        iface = ipaddress.IPv4Interface(value)
    except ValueError:
        raise ValueError(f"{what}: '{value}' is not valid, use address/prefix e.g. 10.1.2.3/24")
    if not 8 <= iface.network.prefixlen <= 30:
        raise ValueError(f"{what}: prefix must be between /8 and /30")
    if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        raise ValueError(f"{what}: {iface.ip} is the network or broadcast address")
    return iface


def _subnet(value, what):
    try:
        return ipaddress.IPv4Network(value, strict=False)
    except ValueError:
        raise ValueError(f"{what}: '{value}' is not a valid subnet, e.g. 10.1.0.0/16")


def _number(v):
    """A real, finite number (not bool, NaN or infinity)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(site):
    """Raise ValueError with a user-facing message if the site config is inconsistent."""
    if not WORKGROUP_RE.fullmatch(site["workgroup"]):
        raise ValueError("Workgroup: 1-15 letters, digits, '-' or '_'")

    office = site["office"]
    if office["method"] not in ("auto", "manual"):
        raise ValueError("Office network: method must be DHCP or static")
    if office["method"] == "manual":
        iface = _ipv4_iface(office["address"], "Office address")
        if office["gateway"] and _ipv4(office["gateway"], "Office gateway") not in iface.network:
            raise ValueError("Office gateway must be inside the office address's subnet")
        office["dns"] = [str(_ipv4(dns, "Office DNS server")) for dns in office["dns"]]
        if office["gateway"]:
            office["gateway"] = str(_ipv4(office["gateway"], "Office gateway"))
        office["address"] = str(iface)
    if not office["allowed_subnets"]:
        raise ValueError("At least one allowed office subnet is required")
    office_nets = [_subnet(s, "Allowed office subnet") for s in office["allowed_subnets"]]
    # Store the canonical form (10.20.0.0/16): it is written verbatim into nftables and smb.conf.
    office["allowed_subnets"] = [str(n) for n in office_nets]

    cnc_iface = _ipv4_iface(site["cnc"]["address"], "CNC network address")
    cnc_net = cnc_iface.network
    if office["interface"] == site["cnc"]["interface"]:
        raise ValueError("Office and CNC networks must use different interfaces")
    for net in office_nets:
        if net.overlaps(cnc_net):
            raise ValueError(f"CNC network {cnc_net} overlaps office subnet {net}; they must be separate")

    names, ips, devices = set(), set(), set()
    for m in site["machines"]:
        if not isinstance(m.get("label"), str) or len(m["label"]) > LABEL_MAX \
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in m["label"]):
            raise ValueError(f"Display name of '{m['name']}': up to {LABEL_MAX} characters, no line breaks or control characters")
        label = m.get("label") or m["name"]
        if not isinstance(m["name"], str) or not MACHINE_NAME_RE.fullmatch(m["name"]):
            raise ValueError(f"Machine name '{m['name']}': 1-12 lowercase letters/digits, starting with a letter")
        if m["name"] in RESERVED_NAMES or m["name"] in names:
            raise ValueError(f"Machine name '{m['name']}' is reserved or already used")
        names.add(m["name"])
        if m["type"] not in MACHINE_TYPES:
            raise ValueError(f"{label}: unknown machine type")
        proto = protocol(m)

        if proto == "serial":
            s = m.get("serial") or {}
            for key, choices in SERIAL_CHOICES.items():
                if s.get(key) not in choices:
                    raise ValueError(f"{label}: invalid serial setting '{key}'")
            for key, lowest, highest, what in [("send_delay", 0, 3600, "Send delay"), ("line_delay_ms", 0, 10000, "Line delay"),
                                               ("pause_timeout", 0, 24 * 60, "Pause timeout")]:
                if not _number(s.get(key)) or not lowest <= s[key] <= highest:
                    raise ValueError(f"{label}: {what} must be a number from {lowest} to {highest}")
            if not _number(s.get("receive_timeout")) or not 0 < s["receive_timeout"] <= 600:
                raise ValueError(f"{label}: Receive timeout must be more than 0 and at most 600 seconds")
            if s["transport"] == "tcp" and s["flow"] in ("rtscts", "dsrdtr"):
                raise ValueError(f"{label}: hardware flow control can't be seen through a serial device server; "
                                 "use XON/XOFF, or set hardware flow control in the device server itself")
            if not EXCLUDED_EXT_RE.fullmatch(s.get("excluded_ext", "")):
                raise ValueError(f"{label}: skip file types must be extensions like 'bak, txt'")
            if s["transport"] == "usb":
                if not DEVICE_RE.fullmatch(s.get("device", "")):
                    raise ValueError(f"{label}: choose the USB serial adapter (a /dev/... path)")
                if s["device"] in devices:
                    raise ValueError(f"{label}: serial adapter {s['device']} is already used by another machine")
                devices.add(s["device"])
                if m["ip"]:
                    raise ValueError(f"{label}: a machine on a USB serial adapter has no IP address")
            elif not isinstance(s.get("tcp_port"), int) or not 1 <= s["tcp_port"] <= 65535:
                raise ValueError(f"{label}: serial device server TCP port must be 1-65535")

        if proto == "focas":
            f = m.get("focas") or {}
            if not isinstance(f.get("port"), int) or not 1 <= f["port"] <= 65535:
                raise ValueError(f"{label}: FOCAS port must be 1-65535 (normally 8193)")
            if not isinstance(f.get("timeout"), int) or not 1 <= f["timeout"] <= 60:
                raise ValueError(f"{label}: FOCAS timeout must be 1-60 seconds")

        needs_ip = proto != "serial" or m["serial"]["transport"] == "tcp"
        if needs_ip:
            what = "serial device server IP" if proto == "serial" else "IP"
            ip = _ipv4(m["ip"], f"{label} {what}")
            if ip not in cnc_net or ip in (cnc_net.network_address, cnc_net.broadcast_address):
                raise ValueError(f"{label}: {ip} is not a usable address in the CNC network {cnc_net}")
            if ip == cnc_iface.ip:
                raise ValueError(f"{label}: {ip} is the Pi's own CNC address")
            if ip in ips:
                raise ValueError(f"{label}: IP {ip} is already used by another machine")
            ips.add(ip)
            m["ip"] = str(ip)

        if proto in ("smb", "ftp") and not PASSWORD_RE.fullmatch(m["password"]):
            raise ValueError(f"{label}: password must be 1-14 letters, digits or symbols (no spaces or accents)")

    for u in site["office_users"]:
        if not USER_NAME_RE.fullmatch(u["name"]):
            raise ValueError(f"User name '{u['name']}': lowercase letters, digits, '.', '_' or '-'")
        if u["name"] in RESERVED_NAMES or u["name"] in names:
            raise ValueError(f"User name '{u['name']}' is reserved or already used")
        names.add(u["name"])
        if not re.fullmatch(r"[0-9A-F]{32}", u["nt_hash"]):
            raise ValueError(f"User '{u['name']}': invalid password hash")


# --- admin login ------------------------------------------------------------

def admin_password_hash():
    try:
        with open(ADMIN_FILE) as f:
            return json.load(f)["password_hash"]
    except FileNotFoundError:
        return None


def set_admin_password_hash(password_hash):
    _write_private(ADMIN_FILE, json.dumps({"password_hash": password_hash}) + "\n")


def focas_token():
    """Shared secret between cnc-admin and the FOCAS bridge (created on first use)."""
    try:
        with open(FOCAS_TOKEN_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        token = secrets.token_hex(32)
        _write_private(FOCAS_TOKEN_FILE, token + "\n")
        os.chmod(FOCAS_TOKEN_FILE, 0o644)  # read by the bridge container (unprivileged user)
        return token


def secret_key():
    try:
        with open(SECRET_KEY_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        key = secrets.token_hex(32)
        _write_private(SECRET_KEY_FILE, key + "\n")
        return key
