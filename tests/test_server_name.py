"""Office file server name: from OFFICE_HOSTNAME, used as the Samba NetBIOS name, and shown
on the Office users page as the login prefix (NAME\\user)."""
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


for host, want in [("cnc-files.example.com", "CNC-FILES"), ("shop-pi", "SHOP-PI"),
                   ("a-very-long-server-name.example.com", "A-VERY-LONG-SER"),
                   ("under_score.example.com", "UNDER-SCORE"), ("", "CNC-FILES"),
                   ("10.20.0.5", "CNC-FILES"), ("1234.example.com", "CNC-FILES"),
                   ("-x-.example.com", "X")]:
    got = name_for(host)
    check(got == want, f"OFFICE_HOSTNAME {host!r} -> {got!r} (want {want!r})")
check(all(len(name_for(h)) <= 15 for h in ("x" * 40, "a.b", "ünïcode.example.com")), "never longer than 15 characters")

name_for("shop-pi.example.com")
site = config.load()
conf = render.render_all(site)["smb-office.conf"][0]
check("netbios name = SHOP-PI\n" in conf, "office Samba config uses the name")
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
check("SHOP-PI\\programmer" in page and "\\\\shop-pi.example.com\\CNC" in page,
      "Office users page: folder and login with the name in front")
check("/user:SHOP-PI\\username" in page, "Office users page: net use command")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
