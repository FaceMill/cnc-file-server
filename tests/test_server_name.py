"""Office file server name: always CNC-FILES, used as the Samba NetBIOS name, and shown
on the Office users page as the login prefix (CNC-FILES\\user)."""
import os
from werkzeug.security import generate_password_hash
from cncadmin import config, render, create_app

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def name_for(host):
    os.environ["OFFICE_HOSTNAME"] = host
    return config.office_server_name()


for host in ["cnc-files.example.com", "shop-pi.example.com", "", "10.20.0.5"]:
    got = name_for(host)
    check(got == "CNC-FILES", f"OFFICE_HOSTNAME {host!r} -> {got!r} (always CNC-FILES)")

name_for("shop-pi.example.com")
site = config.load()
conf = render.render_all(site)["smb-office.conf"][0]
check("netbios name = CNC-FILES\n" in conf, "office Samba config uses the name")
check("netbios name = CNCSRV" in render.render_all(site)["smb-cnc.conf"][0], "CNC side unchanged")

site["office_users"] = [{"name": "programmer", "nt_hash": "0" * 32}]
config.save(site)
config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client()
BASE = "https://localhost"
c.get(BASE + "/login")
with c.session_transaction(base_url=BASE) as s:
    token = s["csrf"]
c.post(BASE + "/login", data={"csrf": token, "username": "admin", "password": "correct-horse-1"})
page = c.get(BASE + "/users").get_data(as_text=True)
check("CNC-FILES\\programmer" in page and "\\\\shop-pi.example.com\\CNC" in page,
      "Office users page: folder and login with the name in front")
check("/user:CNC-FILES\\username" in page, "Office users page: net use command")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
