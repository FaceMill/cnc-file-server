"""Protocol tests: every machine type, FTP rendering, serial DNC against a fake
FANUC on a TCP serial device server (192.168.50.40:4001 added to lo)."""
import os, re
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app

config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client()
c.environ_base["REMOTE_ADDR"] = "10.20.5.5"
BASE = "https://localhost"


def csrf():
    c.get(BASE + "/login")
    with c.session_transaction(base_url=BASE) as s:
        return s["csrf"]


def post(path, **data):
    data.setdefault("csrf", csrf())
    return c.post(BASE + path, data=data, follow_redirects=True)


def flashes(r):
    return re.findall(r'<div class="flash (\w+)">(.*?)</div>', r.get_data(as_text=True), re.S)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    assert cond, msg


post("/login", username="admin", password="correct-horse-1")
serial_tcp = dict(serial_transport="tcp", serial_tcp_port="4001", serial_baud="115200", serial_data_bits="7",
                  serial_parity="E", serial_stop_bits="2", serial_flow="xonxoff", serial_enabled="on")

# One machine of every type
ip = 30
for t, info in config.MACHINE_TYPES.items():
    name = re.sub(r"[^a-z0-9]", "", t)[:10]
    form = dict(name=name, label=info["label"][:30], type=t, password="")
    if info["protocol"] == "serial":
        if t == "fanuc-rs232":
            form.update(serial_tcp, ip="192.168.50.40")
        else:
            form.update(serial_transport="usb", serial_device_manual="/dev/ttyUSB0", ip="192.168.50.99",
                        serial_baud="9600", serial_data_bits="8", serial_parity="N", serial_stop_bits="1",
                        serial_flow="rtscts", serial_enabled="on")
    else:
        form["ip"] = f"192.168.50.{ip}"; ip += 1
    r = post("/machines/new", **form)
    check(any(cat == "ok" for cat, _ in flashes(r)), f"add {t}: {flashes(r)}")
    page = c.get(BASE + f"/machines/{name}").get_data(as_text=True)
    check("How to connect" in page and info["label"][:20] in page, f"setup sheet renders for {t}")

site = config.load()
usb = config.find_machine(site, "serialothe")
check(usb["ip"] == "" and usb["serial"]["device"] == "/dev/ttyUSB0" and usb["serial"]["flow"] == "rtscts", "USB serial machine: IP dropped, settings stored")
fan = config.find_machine(site, "fanucrs232")
check(fan["serial"]["transport"] == "tcp" and fan["ip"] == "192.168.50.40", "TCP serial machine stored")
check("Parameter 20" in c.get(BASE + "/machines/fanucdatas").get_data(as_text=True), "FANUC data server instructions")
check("[EMBED]" in c.get(BASE + "/machines/fanucembed").get_data(as_text=True), "FANUC embedded instructions")

g = lambda f: open(f"/app/generated/{f}").read()
check("[fanucdatas]" not in g("smb-cnc.conf") and "[haasngc]" in g("smb-cnc.conf"), "only SMB machines get SMB shares")
check("server max protocol = SMB3" in g("smb-cnc.conf"), "CNC SMB accepts SMB2/3 for newer controls")
ftp_users = g("ftp-users.txt").split("\n")
check(sorted(l.split()[0] for l in ftp_users if l) == ["fanucdatas", "fanucembed", "ftpother"], "FTP users = FTP machines")
check(all(l.split()[1].startswith("$6$") for l in ftp_users if l), "FTP passwords stored as SHA-512 crypt")
before = g("ftp-users.txt")
post("/network/general", workgroup="WORKGROUP2")
check(g("ftp-users.txt") == before, "FTP hashes stable across renders (no needless FTP restart)")
check("listen_address=192.168.50.1" in g("vsftpd.conf"), "vsftpd bound to CNC address")
nft = g("nftables.conf")
check(re.search(r"192\.168\.50\.3\d.*tcp dport \{ 21, 30000-30049 \}", nft) is not None, "firewall opens FTP for FTP machines only")
check("192.168.50.40" not in nft.split("tcp dport { 139")[0].split("SMB machines")[-1], "serial device server not given SMB access")

# Validation
for bad, why in [(dict(name="bad1", type="serial-other", serial_transport="usb", serial_device_manual="/dev/ttyUSB0"), "duplicate USB adapter"),
                 (dict(name="bad2", type="serial-other", serial_transport="usb", serial_device_manual=""), "USB without adapter"),
                 (dict(name="bad3", type="fanuc-rs232", ip="", **{k: v for k, v in serial_tcp.items()}), "TCP serial without IP"),
                 (dict(name="bad4", type="fanuc-dataserver", ip="192.168.50.30"), "FTP machine duplicate IP")]:
    r = post("/machines/new", label="x", password="", **bad)
    check(any(cat == "error" for cat, _ in flashes(r)), f"rejects {why}: {flashes(r)}")

dash = c.get(BASE + "/").get_data(as_text=True)
check("adapter missing" in dash and "RS-232 DNC" in dash and "FTP" in dash, "dashboard shows protocols and USB adapter state")
print("ALL PASSED")
os._exit(0)
