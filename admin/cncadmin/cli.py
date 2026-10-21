"""Command-line setup used by host/install.sh.

    python -m cncadmin.cli init [--office-subnet CIDR] [--force]
    python -m cncadmin.cli render
    python -m cncadmin.cli set-admin-password
    python -m cncadmin.cli office-mode --hotspot SSID | --client [--office-subnet CIDR]
"""
import argparse
import getpass
import sys

from werkzeug.security import generate_password_hash

from . import config, render, system


def cmd_init(args):
    if config.exists() and not args.force:
        print(f"{config.SITE_FILE} already exists; use --force to overwrite.")
        return 1
    subnet = args.office_subnet or system.interface_subnet(args.office_if)
    if not subnet:
        print(f"Could not detect the subnet of {args.office_if}; pass --office-subnet.", file=sys.stderr)
        return 1
    site = config.default_site(subnet, args.office_if, args.cnc_if)
    config.save(site)
    cmd_render(args)
    print(f"Created {config.SITE_FILE}. Office access allowed from {subnet}.")
    print("Machine share passwords (also shown in the web UI):")
    for m in site["machines"]:
        print(f"  {m['name']:<10} {m['ip']:<15} {m['password']}")
    return 0


def cmd_render(args):
    config.focas_token()  # the FOCAS bridge container mounts this file
    site = config.load()
    for m in site["machines"]:
        system.ensure_machine_dir(m["name"])
    changed = render.write_all(render.render_all(site))
    print("Rendered:", ", ".join(sorted(changed)) or "no changes")
    return 0


def cmd_office_mode(args):
    """Record whether the office side is the Pi's setup hotspot (host/setup-hotspot.sh runs
    the hotspot itself); re-renders the firewall and Samba config."""
    site = config.load()
    office = site["office"]
    if args.hotspot:
        office.update(hotspot=True, hotspot_ssid=args.hotspot, method="auto", address="", gateway="", dns=[])
        if config.HOTSPOT_SUBNET not in office["allowed_subnets"]:
            office["allowed_subnets"].append(config.HOTSPOT_SUBNET)
        what = f"the setup hotspot '{args.hotspot}' ({config.HOTSPOT_SUBNET})"
    else:
        was_hotspot = office.get("hotspot")
        office["hotspot"] = False
        office.pop("hotspot_ssid", None)
        if was_hotspot:
            office["allowed_subnets"] = [s for s in office["allowed_subnets"] if s != config.HOTSPOT_SUBNET]
        detect = was_hotspot or not office["allowed_subnets"]  # back on the office network: allow its subnet
        subnet = args.office_subnet or (system.interface_subnet(office["interface"]) if detect else None)
        if subnet and subnet not in office["allowed_subnets"]:
            office["allowed_subnets"].append(subnet)
        if not office["allowed_subnets"]:
            print(f"Could not detect the subnet of {office['interface']}; pass --office-subnet.", file=sys.stderr)
            return 1
        what = f"the office network ({', '.join(office['allowed_subnets'])})"
    config.save(site)
    cmd_render(args)
    print(f"Office side: {what}.")
    return 0


def cmd_set_admin_password(args):
    # Ask again on a typo instead of failing: the installer stops if this fails.
    for _ in range(3):
        pw = getpass.getpass("New admin password (at least 10 characters): ")
        if len(pw) < 10:
            print("Use at least 10 characters.", file=sys.stderr)
            continue
        if getpass.getpass("Repeat: ") != pw:
            print("Passwords do not match.", file=sys.stderr)
            continue
        config.set_admin_password_hash(generate_password_hash(pw))
        print("Admin password set. Log in as 'admin'.")
        return 0
    print("No password set; run: sudo docker compose run --rm --no-deps cnc-admin "
          "python -m cncadmin.cli set-admin-password", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(prog="cncadmin")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("init")
    p.add_argument("--office-subnet")
    p.add_argument("--office-if", default="eth0")
    p.add_argument("--cnc-if", default="eth1")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)
    sub.add_parser("render").set_defaults(func=cmd_render)
    sub.add_parser("set-admin-password").set_defaults(func=cmd_set_admin_password)
    p = sub.add_parser("office-mode")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hotspot", metavar="SSID")
    g.add_argument("--client", action="store_true")
    p.add_argument("--office-subnet")
    p.set_defaults(func=cmd_office_mode)
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
