import json, re, time
from werkzeug.security import generate_password_hash
from cncadmin import config, create_app, network

network.CONFIRM_SECONDS = 3
network.APPLY_DELAY_SECONDS = 0
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


check(c.get(BASE + "/").status_code == 302, "unauthenticated redirect to login")
check(c.post(BASE + "/login", data={"username": "admin", "password": "x"}).status_code == 400, "POST without CSRF rejected")
r = post("/login", username="admin", password="wrong")
check("Wrong username" in r.get_data(as_text=True), "wrong password rejected")
r = post("/login", username="admin", password="correct-horse-1")
check("Machines" in r.get_data(as_text=True) and "haas1" in r.get_data(as_text=True), "login + dashboard renders")

for p in ["/machines", "/machines/new", "/machines/haas1", "/machines/siemens1", "/machines/haas1/edit", "/users", "/network", "/logs", "/account"]:
    check(c.get(BASE + p).status_code == 200, f"GET {p}")
check("192.168.50.14" in c.get(BASE + "/machines/new").get_data(as_text=True), "new machine form suggests next free IP")

r = post("/machines/new", name="haas3", label="Haas VF-2", type="haas-classic", ip="192.168.50.14", password="")
check(any("added" in m for _, m in flashes(r)), f"add machine: {flashes(r)}")
site = config.load()
m = config.find_machine(site, "haas3")
check(m and len(m["password"]) == 8, "generated password stored")
check("[haas3]" in open("/app/generated/smb-cnc.conf").read(), "smb-cnc.conf has new share")
check("192.168.50.14" in open("/app/generated/nftables.conf").read(), "firewall allows new machine")
check(open("/app/generated/cnc-users.txt").read().count("\n") == 4, "cnc-users.txt has 4 users")

for bad, why in [(dict(name="haas3", ip="192.168.50.20"), "duplicate name"),
                 (dict(name="haas4", ip="192.168.50.11"), "duplicate IP"),
                 (dict(name="haas4", ip="10.0.0.5"), "IP outside CNC network"),
                 (dict(name="haas4", ip="192.168.50.1"), "Pi's own IP"),
                 (dict(name="Bad Name", ip="192.168.50.30"), "invalid name"),
                 (dict(name="global", ip="192.168.50.30"), "reserved name")]:
    r = post("/machines/new", label="x", type="haas-classic", password="", **bad)
    check(any(cat == "error" for cat, _ in flashes(r)), f"rejects {why}: {flashes(r)}")
check(len(config.load()["machines"]) == 4, "rejected machines were not saved")

r = post("/machines/haas3/edit", label="Haas VF-2 bay 3", type="haas-classic", ip="192.168.50.15", password="NEWPASS1")
m = config.find_machine(config.load(), "haas3")
check(m["ip"] == "192.168.50.15" and m["password"] == "NEWPASS1" and m["label"] == "Haas VF-2 bay 3", "edit machine")
r = post("/machines/haas3/edit", label="Haas VF-2 bay 3", type="haas-classic", ip="192.168.50.15", password="")
check(config.find_machine(config.load(), "haas3")["password"] == "NEWPASS1", "blank password keeps existing")
detail = c.get(BASE + "/machines/haas3").get_data(as_text=True)
check("NEWPASS1" in detail and "192.168.50.1" in detail and "255.255.255.0" in detail, "setup sheet shows settings")
check("system network adapter" in c.get(BASE + "/machines/siemens1").get_data(as_text=True), "Siemens PCU50 instructions")

import os
os.makedirs("/srv/cnc/haas3", exist_ok=True); open("/srv/cnc/haas3/O1.NC", "w").write("x")
r = post("/machines/haas3/delete")
check(config.find_machine(config.load(), "haas3") is None, "delete machine")
check(any(".removed" in m for _, m in flashes(r)) and not os.path.exists("/srv/cnc/haas3"), "folder archived, not deleted")

r = post("/users/add", name="programmer", password="short", repeat="short")
check(any(cat == "error" for cat, _ in flashes(r)), "short user password rejected")
r = post("/users/add", name="programmer", password="Office-pass-1", repeat="Office-pass-1")
check(config.find_office_user(config.load(), "programmer") is not None, "office user added")
check("valid users = programmer" in open("/app/generated/smb-office.conf").read(), "office share opened to user")
check("Office-pass-1" not in json.dumps(config.load()), "office password not stored in plain text")
r = post("/users/add", name="haas1", password="Office-pass-1", repeat="Office-pass-1")
check(any(cat == "error" for cat, _ in flashes(r)), "office user can't reuse machine name")

r = post("/network/general", workgroup="cncshop")
check(config.load()["workgroup"] == "CNCSHOP", "workgroup updated")
r = post("/network/cnc", address="192.168.60.1/24", ip_haas1="192.168.50.11", ip_haas2="192.168.50.12", ip_siemens1="192.168.50.13")
check(any(cat == "error" for cat, _ in flashes(r)) and config.load()["cnc"]["address"] == "192.168.50.1/24", "CNC subnet change with stale machine IPs rejected")
r = post("/network/cnc", address="192.168.60.1/24", ip_haas1="192.168.60.11", ip_haas2="192.168.60.12", ip_siemens1="192.168.60.13")
check(config.load()["cnc"]["address"] == "192.168.60.1/24", f"CNC subnet renumbered: {flashes(r)}")
r = post("/network/cnc", address="10.20.1.1/24", ip_haas1="10.20.1.11", ip_haas2="10.20.1.12", ip_siemens1="10.20.1.13")
check(any("overlaps" in m for _, m in flashes(r)), "CNC network overlapping office rejected")

r = post("/network/office", method="manual", address="10.20.9.9/16", gateway="10.20.0.1", dns="10.20.0.2", allowed_subnets="10.99.0.0/16")
check(any("locked out" in m for _, m in flashes(r)), "lockout protection")
r = post("/network/office", method="manual", address="10.20.9.9/16", gateway="10.20.0.1", dns="10.20.0.2", allowed_subnets="10.20.0.0/16")
check(any("https://10.20.9.9:8443" in m for _, m in flashes(r)), "office change pending with new URL")
time.sleep(0.5)
r = post("/machines/new", name="haas9", label="x", type="haas-classic", ip="192.168.60.30", password="")
check(any("pending" in m for _, m in flashes(r)), "other changes blocked while pending")
r = post("/network/confirm")
check(config.load()["office"]["address"] == "10.20.9.9/16" and network.office_change.pending is None, "office change confirmed")

r = post("/network/office", method="auto", allowed_subnets="10.20.0.0/16")
time.sleep(4.5)
check(config.load()["office"]["address"] == "10.20.9.9/16" and network.office_change.pending is None, "unconfirmed office change auto-reverted")
check("Not confirmed in time" in c.get(BASE + "/network").get_data(as_text=True), "revert reported in UI")

r = post("/account", current="correct-horse-1", new="another-pass-2", repeat="another-pass-2")
check(any("changed" in m for _, m in flashes(r)), "admin password change")
post("/logout")
check(c.get(BASE + "/").status_code == 302, "logout")
print("ALL PASSED")
import os; os._exit(0)
