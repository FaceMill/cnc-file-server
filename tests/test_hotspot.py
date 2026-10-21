"""Setup hotspot (OFFICE_MODE=hotspot): the office side is the Pi's own access point.
cncadmin.cli office-mode, the generated firewall (checked with the real nft) and Samba
config, validation, and the network page."""
import os, re, subprocess
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def cli(*args):
    return subprocess.run(["python", "-m", "cncadmin.cli", *args], capture_output=True, text=True)


def generated(name):
    return open(f"/app/generated/{name}").read()


def nft_ok():
    return subprocess.run(["nft", "-c", "-f", "/app/generated/nftables.conf"], capture_output=True).returncode == 0


DHCP = 'udp sport 68 udp dport 67 accept'
check(DHCP not in generated("nftables.conf"), "office network mode: no hotspot rules")

r = cli("office-mode", "--hotspot", "cnc-setup-test")
site = config.load()
check(r.returncode == 0 and site["office"]["hotspot"] and site["office"]["hotspot_ssid"] == "cnc-setup-test",
      f"office-mode --hotspot recorded ({r.stdout.strip()[-60:]})")
check(config.HOTSPOT_SUBNET in site["office"]["allowed_subnets"], "hotspot subnet allowed")
fw = generated("nftables.conf")
check(DHCP in fw and "ip saddr 10.42.0.0/24 meta l4proto { tcp, udp } th dport 53 accept" in fw,
      "firewall: DHCP and DNS for laptops on the hotspot")
check(re.search(r'iifname "eth9" ip saddr \{ [^}]*10\.42\.0\.0/24[^}]*\} tcp dport \{ 22, 80, 443, 445, 8443, 9090 \} accept', fw),
      "firewall: admin page, SSH and office share open to the hotspot subnet")
check("type filter hook forward priority 0; policy drop;" in fw, "firewall: still nothing forwarded (no route to the CNC network)")
check(nft_ok(), "hotspot ruleset passes the real nft -c")
check("10.42.0.0/24" in generated("smb-office.conf").split("hosts allow =")[1].splitlines()[0],
      "office share accepts the hotspot subnet")

# Validation: the hotspot subnet can't be dropped by accident, the SSID is checked
site = config.load()
site["office"]["allowed_subnets"] = ["10.20.0.0/16"]
config.save(site)
check(config.HOTSPOT_SUBNET in config.load()["office"]["allowed_subnets"], "saving without the hotspot subnet puts it back")
for bad in ("", "x" * 33, "bad\nname"):
    try:
        site = config.load(); site["office"]["hotspot_ssid"] = bad; config.save(site); ok = False
    except ValueError:
        ok = True
    check(ok, f"hotspot name {bad[:10]!r} refused")
check(cli("office-mode", "--hotspot", "x" * 33).returncode != 0 and config.load()["office"]["hotspot_ssid"] == "cnc-setup-test",
      "office-mode with a bad SSID changes nothing")

# Network page
config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client(); c.environ_base["REMOTE_ADDR"] = "10.42.0.23"
BASE = "https://localhost"


def csrf():
    c.get(BASE + "/login")
    with c.session_transaction(base_url=BASE) as s:
        return s["csrf"]


c.post(BASE + "/login", data={"csrf": csrf(), "username": "admin", "password": "correct-horse-1"}, follow_redirects=True)
page = c.get(BASE + "/network").get_data(as_text=True)
check("setup hotspot" in page and "cnc-setup-test" in page and "https://10.42.0.1:8443" in page,
      "network page: hotspot name and addresses shown")
check('action="/network/office"' not in page, "network page: no office address form while on the hotspot")
before = config.load()["office"]
r = c.post(BASE + "/network/office", data={"csrf": csrf(), "method": "manual", "address": "10.42.0.9/24",
                                           "allowed_subnets": "10.42.0.0/24"}, follow_redirects=True)
check("address is fixed" in r.get_data(as_text=True) and config.load()["office"] == before,
      "office address change refused while on the hotspot")

# Back to the office network
r = cli("office-mode", "--client", "--office-subnet", "192.168.75.0/24")
site = config.load()
check(r.returncode == 0 and not site["office"]["hotspot"] and site["office"]["hotspot_ssid"] == "cnc-setup-test",
      "office-mode --client: hotspot off (name and password kept for next time)")
check(config.HOTSPOT_SUBNET not in site["office"]["allowed_subnets"] and "192.168.75.0/24" in site["office"]["allowed_subnets"],
      f"hotspot subnet removed, office subnet allowed ({site['office']['allowed_subnets']})")
check(DHCP not in generated("nftables.conf") and nft_ok(), "firewall back to office rules (valid)")
page = c.get(BASE + "/network").get_data(as_text=True)
check('action="/network/office"' in page, "network page: office address form back")
r = cli("office-mode", "--client")
check(r.returncode == 0 and config.load()["office"]["allowed_subnets"] == site["office"]["allowed_subnets"],
      "office-mode --client when already off: no change")

# --- Settings page: switching the hotspot on and off (with rollback) ---------------
import time
from cncadmin.network import office_change, HOTSPOT_CONFIRM_SECONDS


def applied():
    for _ in range(50):
        if office_change.pending and office_change.pending.get("deadline"):
            return True
        time.sleep(0.2)
    return False


page = c.get(BASE + "/settings").get_data(as_text=True)
check("Switch the hotspot on" in page and "The Pi leaves its current Wi-Fi network" in page and "No internet" in page,
      "settings page: hotspot form with warnings")
saved_pw = config.load()["office"]["hotspot_password"]
r = c.post(BASE + "/settings/hotspot/on", data={"csrf": csrf(), "ssid": "bench-test"}, follow_redirects=True)
check("Tick the box" in r.get_data(as_text=True) and not office_change.pending, "hotspot on refused without the acknowledgement")
r = c.post(BASE + "/settings/hotspot/on", data={"csrf": csrf(), "ssid": "bench-test", "password": "short", "understood": "1"},
           follow_redirects=True)
check("8-63 characters" in r.get_data(as_text=True) and not office_change.pending, "hotspot password under 8 characters refused")
r = c.post(BASE + "/settings/hotspot/on", data={"csrf": csrf(), "ssid": "bench-test", "password": "", "understood": "1"})
with c.session_transaction(base_url=BASE) as sess:
    flashed = repr(sess.get("_flashes"))
page = c.get(BASE + "/settings").get_data(as_text=True)
check(office_change.pending and office_change.pending["new"]["hotspot"] and office_change.pending["seconds"] == HOTSPOT_CONFIRM_SECONDS,
      f"hotspot on: pending, {HOTSPOT_CONFIRM_SECONDS} s to confirm")
check(office_change.pending["new"]["hotspot_password"] == saved_pw, "blank password keeps the saved one")
check("Switching to the hotspot" in page and saved_pw in page, "settings page shows the network and password to join")
check("bench-test" in flashed and saved_pw not in flashed, "password not put in the session cookie (flash message)")
check(applied() and DHCP in generated("nftables.conf") and nft_ok(), "hotspot on applied: firewall allows DHCP (valid)")
check(not config.load()["office"]["hotspot"], "not saved until confirmed")
c.post(BASE + "/network/confirm", data={"csrf": csrf()})
site = config.load()
check(site["office"]["hotspot"] and site["office"]["hotspot_ssid"] == "bench-test" and not office_change.pending,
      "confirmed: hotspot on, new name saved")
page = c.get(BASE + "/settings").get_data(as_text=True)
check("Switch off, back to the office Wi-Fi" in page and "bench-test" in page and saved_pw in page,
      "settings page: hotspot details and the switch-off form")
check("10.42.0.0/24" not in page.split('name="allowed_subnets">')[1].split("</textarea>")[0],
      "switch-off form: office subnets only")

r = c.post(BASE + "/settings/hotspot/off", data={"csrf": csrf(), "allowed_subnets": ""}, follow_redirects=True)
check("Enter the office subnet" in r.get_data(as_text=True) and not office_change.pending, "switch-off needs an office subnet")
c.post(BASE + "/settings/hotspot/off", data={"csrf": csrf(), "allowed_subnets": "192.168.75.0/24"})
check(applied() and DHCP not in generated("nftables.conf"), "hotspot off applied: hotspot rules gone")
c.post(BASE + "/network/revert", data={"csrf": csrf()})
check(config.load()["office"]["hotspot"] and DHCP in generated("nftables.conf") and not office_change.pending,
      "not confirmed (reverted): hotspot back on, rules back")
c.post(BASE + "/settings/hotspot/off", data={"csrf": csrf(), "allowed_subnets": "192.168.75.0/24"})
applied()
c.post(BASE + "/network/confirm", data={"csrf": csrf()})
site = config.load()
check(not site["office"]["hotspot"] and site["office"]["allowed_subnets"] == ["192.168.75.0/24"]
      and DHCP not in generated("nftables.conf") and nft_ok(), "confirmed: back on the office network, its subnet allowed")
check(saved_pw in open(config.SITE_FILE).read() and oct(os.stat(config.SITE_FILE).st_mode & 0o777) == "0o600",
      "hotspot password kept in site.json (owner-only)")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
