"""Fuzz config.validate + render: every site that validates must render configs
that the real tools accept. Writes each accepted render to /out/<n>/ for
testparm / nft -c checks, plus a manifest of what each case contained."""
import json, os, random, sys
from cncadmin import config, render

random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 300

HOSTILE_TEXT = ["normal", "Haas #1", "a\nb", "x\n[global]\nguest ok = yes", "semi;colon", "hash # comment",
                "brace } { ", "quote \" '", "back\\slash", "%U %m macro", "ünïcødé 加工", "   ", "",
                "x" * 300, "tab\there", "cr\rhere", "$(reboot)", "`id`", "nul\x00byte"]
SUBNETS = ["10.20.0.0/16", "10.20.5.5/16", "10.0.0.0/255.0.0.0", "10.10.0.0/16", "0.0.0.0/0",
           "10.20.0.0/33", "10.20.0.0", "garbage", "10.20.0.0/16 ", "192.168.50.0/24", "fe80::/64", "1.2.3.4/32"]
IPS = ["192.168.50.11", "192.168.50.0", "192.168.50.255", "192.168.50.1", " 192.168.50.12", "192.168.050.13",
       "192.168.50.14\n", "10.0.0.1", "", "192.168.50.300", "::1"]
NAMES = ["haas1", "Haas1", "a", "x" * 12, "x" * 13, "global", "1abc", "ha-as", "ha as", "fanuc\n", "ipc"]
PASSWORDS = ["ABC12345", "", "pass word", "p" * 14, "p" * 15, "ünï", "q\"';$", "line\nbreak", "%u"]


def rand_machine(i):
    t = random.choice(list(config.MACHINE_TYPES))
    m = {"name": random.choice(NAMES) if random.random() < .3 else f"m{i}",
         "label": random.choice(HOSTILE_TEXT), "type": t,
         "ip": random.choice(IPS) if random.random() < .3 else f"192.168.50.{20 + i}",
         "password": random.choice(PASSWORDS) if random.random() < .4 else config.gen_password()}
    p = config.MACHINE_TYPES[t]["protocol"]
    if p == "serial":
        s = dict(config.SERIAL_DEFAULTS)
        s["transport"] = random.choice(["usb", "tcp"])
        s["device"] = random.choice(["/dev/ttyUSB0", "/dev/serial/by-id/x", "", "ttyUSB0", "/dev/../etc/passwd"]) if s["transport"] == "usb" else ""
        if s["transport"] == "usb":
            m["ip"] = random.choice(["", "192.168.50.99"])
        s["excluded_ext"] = random.choice(["", "bak", "a\nb"])
        s["send_delay"] = random.choice([0, 10.0, -1, "x"])
        m["serial"] = s
    if p == "focas":
        m["focas"] = {"port": random.choice([8193, 0, 70000, "8193"]), "timeout": random.choice([10, 0, 61]), "replace": False}
    return m



def valid_machine(i):
    t = random.choice(list(config.MACHINE_TYPES))
    m = {"name": f"m{i}", "label": f"Machine {i}", "type": t, "ip": f"192.168.50.{20 + i}", "password": config.gen_password()}
    p = config.MACHINE_TYPES[t]["protocol"]
    if p == "serial":
        m["serial"] = dict(config.SERIAL_DEFAULTS, transport="tcp")
    if p == "focas":
        m["focas"] = dict(config.FOCAS_DEFAULTS)
    return m


def mutate(site):
    """Apply one hostile change; return a description."""
    ms = site["machines"]
    choice = random.randrange(9)
    if choice == 0 and ms:
        v = random.choice(HOSTILE_TEXT); random.choice(ms)["label"] = v; return f"label={v!r}"
    if choice == 1:
        v = random.choice(SUBNETS); site["office"]["allowed_subnets"] = [v]; return f"subnet={v!r}"
    if choice == 2 and ms:
        v = random.choice(IPS); random.choice(ms)["ip"] = v; return f"ip={v!r}"
    if choice == 3 and ms:
        v = random.choice(NAMES); random.choice(ms)["name"] = v; return f"name={v!r}"
    if choice == 4 and ms:
        v = random.choice(PASSWORDS); random.choice(ms)["password"] = v; return f"password={v!r}"
    if choice == 5:
        v = random.choice(["WORKGROUP", "bad group", "x" * 16, "a\nb", "SHOPNET", "%U"]); site["workgroup"] = v; return f"workgroup={v!r}"
    if choice == 6:
        v = random.choice(["programmer", "j.smith", "Bad", "a\nb", "m0", "x" * 21]); site["office_users"].append({"name": v, "nt_hash": "8846F7EAEE8FB117AD06BDD830B7586C"}); return f"office user={v!r}"
    if choice == 7:
        sm = [m for m in ms if "serial" in m]
        if sm:
            v = random.choice(["a\nb", "bak, txt", "x]\n[y"]); sm[0]["serial"]["excluded_ext"] = v; return f"excluded_ext={v!r}"
    if choice == 8:
        v = random.choice(["192.168.50.1/24", "192.168.50.0/24", "192.168.50.1/31", "10.20.0.1/24", "192.168.60.1/24"]); site["cnc"]["address"] = v; return f"cnc={v!r}"
    return "none"


accepted = rejected = 0
reasons = {}
manifest = {}
for case in range(N):
    site = config.default_site("10.20.0.0/16")
    site["machines"] += [valid_machine(i) for i in range(random.randint(0, 5))]
    what = mutate(site)
    try:
        config.validate(site)
    except (ValueError, TypeError, KeyError):
        rejected += 1
        key = what.split("=")[0]
        reasons.setdefault(key, set()).add(what)
        continue
    accepted += 1
    out = f"/out/{case}"
    os.makedirs(out)
    render.write_all(render.render_all(site), out)
    manifest[case] = what

json.dump(manifest, open("/out/manifest.json", "w"), indent=0)
print(f"cases={N} accepted={accepted} rejected={rejected}")
accepted_kinds = {}
for w in manifest.values():
    accepted_kinds.setdefault(w.split("=")[0], set()).add(w)
for k, v in sorted(accepted_kinds.items()):
    print("ACCEPTED", k, sorted(v)[:12])
