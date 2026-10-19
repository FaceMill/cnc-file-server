# CNC File Server: SMB1 ⇄ SMB3 Bridge on Raspberry Pi

## Goal

Three CNC machines can only speak SMB1, which is insecure and must not be exposed on
the office network. Future machines may use FTP (FANUC) or RS-232 instead. Engineers need a normal (SMB3) network drive to drop CNC programs
on, and the machines must be able to load programs from it **and save programs back**.
Everything should be manageable from a web page.

| Machine   | Control                                         | Share      | CNC-side IP (default) |
|-----------|-------------------------------------------------|------------|-----------------------|
| Haas #1   | Haas Classic control                            | `haas1`    | 192.168.50.11         |
| Haas #2   | Haas Classic control                            | `haas2`    | 192.168.50.12         |
| Siemens   | Sinumerik 840D powerline, PCU 50 (Windows XP)   | `siemens1` | 192.168.50.13         |

## Supported connection types

The CNC side supports the three ways CNCs commonly get programs. Choose the
control type when adding a machine, and the admin page sets up the right one.

| Connection | How it works | Control types offered |
|---|---|---|
| **Network share (SMB)** | The machine maps a drive on the Pi. SMB1 for old controls; newer ones negotiate SMB2/3 automatically | Haas Classic, Haas NGC, Siemens 840D PCU 50 (XP), Siemens 840D sl / 828D (Operate), Heidenhain TNC/iTNC, Windows-based controls (Mazak Matrix/Smooth, Okuma OSP-P, Hurco) |
| **FTP** | The machine logs in to the Pi's FTP server (vsftpd) and is locked to its own folder. Active and passive mode | FANUC with Data Server (FTP mode, including DNC operation from the Pi), FANUC embedded Ethernet without Data Server, other FTP controls (Mitsubishi M70/M80, Brother) |
| **FOCAS** (FANUC Ethernet) | The Pi reads and writes programs **directly in CNC memory** over Ethernet (TCP 8193), from the machine's page: live CNC status (mode, run state, active program, control model, alarm text, operator message), the CNC's program directory or program folders (30i/31i/32i, 0i-F: browse folders, named and 8-digit programs), Send to CNC (optionally replacing an existing program), Read from CNC, and Delete. Uses FANUC's FOCAS2 library (`libfwlib32`, via github.com/Ladder99/fanuc-driver) in a `focas-bridge` container (32-bit ARM on a Pi, x86-64 on a PC) | FANUC 0i-D/F, 16i/18i/21i (with Ethernet), 30i/31i/32i |
| **RS-232 serial DNC** | Engine merged from **serial-dnc**. The Pi listens on each port all the time. The machine **requests** a program by punching `(SEND O1234)`; after a send delay the Pi drip-feeds it. Anything else the machine punches is saved to `<machine>/received/`. XON/XOFF, RTS/CTS or DSR/DTR flow control; 4-byte writes to stay within FANUC's 10-character XOFF limit | FANUC RS-232 (no Ethernet), other serial controls (older Haas, Fadal, Mazak, Okuma) |

Serial lines connect either to a **USB-to-RS-232 adapter** on the Pi (FTDI chipset
recommended, one per machine) or to a **serial device server** on the CNC switch
(e.g. Moxa NPort 5110/5150 in "TCP Server" raw mode). The device server suits
machines far from the Pi.

All three connection types use the same per-machine folder, so office users drop
files the same way regardless of how the machine connects.

Each machine login (SMB or FTP) is accepted **only from that machine's own IP**,
so one machine's credentials can't be used from another.

Not supported yet:
- **Pushing to a Data Server's own FTP server** (where the CNC is the server). The supported
  mode is the Data Server connecting to the Pi as the FTP host.

## Decisions

- **CNCs mount a remote share.** The Pi is the file server for both sides. Nothing is
  copied: two Samba servers serve the same folders, so a file saved on one side
  appears on the other immediately.
- **Two-way, no approval workflow.**
- **Simple local accounts** (no Active Directory).
- **No VLAN.** A dedicated switch for the CNCs on the Pi's second network port.
- **IT approval required** before connecting the Pi to the office network.
- **IT supplies** the office IP (DHCP reservation), hostname, and subnet.
- **Web management**: a custom admin page (`cnc-admin`) owns all configuration.

## Architecture

```
 Office LAN                                         Dedicated CNC switch (192.168.50.0/24)
 ──────────                                         ─────────────────────────────────────
 Office PCs ──SMB3 (encrypted)──▶ eth0 ┌──────────────┐ eth1 ◀──SMB1/2/3── Haas, Siemens, Heidenhain...
 Browser ─────HTTPS─────────────▶      │ Raspberry Pi │      ◀──FTP─────── FANUC Data Server / embedded
                                       │ 192.168.50.1 │      ───TCP──────▶ serial device server ─RS-232─▶ old CNC
                                       │              │ USB ─RS-232────────────────────────────────────▶ old CNC
                                       └──────────────┘
                                   /srv/cnc/<machine>/
```

- **eth0 (onboard)**: office network. SMB3 only, with encryption and signing
  required. Web pages are reachable only on this port, and only from the allowed
  office subnets.
- **eth1 (USB 3 gigabit adapter)**: CNC network with no gateway. Runs SMB (SMB1 with
  NTLMv1 for the Haas Classic and Windows XP; SMB2/3 for newer controls) and FTP.
  The firewall opens each service only to the machines that use it.
- **USB serial adapters**, if any: RS-232 to machines without Ethernet.
- **IP forwarding is off and the forward chain drops everything.** The CNCs can't
  reach the office or the internet, and the office can't reach the CNCs.
- Each machine's login sees only its own folder. Office users see all machine folders.

## Configuration model

`config/site.json` is the single source of truth, edited through the admin page.
From it, cnc-admin generates `generated/`:

| Generated file      | Used by                                   |
|---------------------|-------------------------------------------|
| `smb-office.conf`   | `samba-office` container                  |
| `smb-cnc.conf`      | `samba-cnc` container                     |
| `office-users.txt`  | `samba-office` (NT password hashes)       |
| `cnc-users.txt`     | `samba-cnc` (NT password hashes)          |
| `vsftpd.conf`       | `ftp-cnc` container                       |
| `ftp-users.txt`     | `ftp-cnc` (SHA-512 crypt hashes and each machine's IP) |
| `nftables.conf`     | host firewall (included from `/etc/nftables.conf` at boot) |

On every change, cnc-admin validates the whole config, rewrites the generated files,
and reloads or restarts only what changed: the firewall, `samba-cnc`, `ftp-cnc`, or `samba-office`.
Serial DNC runs inside cnc-admin itself (it needs access to the USB serial adapters), with one
listener thread per serial machine, restarted automatically when its settings change. Its event log
is in `config/events.db`.

## Admin web page (`https://<pi>:8443`)

| Page         | What you can do                                                          |
|--------------|---------------------------------------------------------------------------|
| Status       | Per machine: on the network (ping), currently connected to its share. Service health, interface addresses, free disk space |
| Machines     | Add, edit, or remove machines (name, control type, IP, password, serial line settings). Each machine page lists its files and has a printable **setup sheet** with the exact values and steps for that control type. Serial machines get **Send to machine** / **Receive from machine** buttons with live progress |
| Office users | Add or remove SMB3 users and reset their passwords (stored only as hashes) |
| Network      | Office: DHCP/static, IP, gateway, DNS, allowed subnets, with **automatic 2-minute rollback** unless confirmed. CNC: Pi address and machine IPs (renumber together). Workgroup |
| Logs         | Last 300 lines from each container (failed CNC logins show up here)       |
| Account      | Change the admin password                                                 |

Safety features:
- Every change is validated before it's applied: no duplicate IPs or names, machine IPs
  must be inside the CNC network, the CNC network must not overlap the office network,
  and names are reserved where needed.
- Office network changes roll back after 2 minutes unless confirmed. This also covers
  an admin container restart in the middle of a change. A change that would lock out
  your own computer is refused unless you explicitly override it.
- Removing a machine moves its folder to `/srv/cnc/.removed/` instead of deleting it.
- Login, CSRF protection, and secure session cookies. Only reachable over HTTPS from
  the office subnets.

**Note:** cnc-admin controls the host firewall, network, and Docker, so it is
effectively root on the Pi. Use a strong admin password.

## Other web tools

| URL                   | Tool          | Purpose                                         |
|-----------------------|---------------|-------------------------------------------------|
| `https://<pi>/`       | (redirect)    | Goes to the admin page on :8443                 |
| `https://<pi>:9090`   | Cockpit       | OS updates, reboot, disk, system logs (Pi login) |

Uploading, downloading, viewing and deleting programs is built into each machine's
page in the admin site. **File Browser was removed** after the security review: the
pinned version had a default `admin/admin` login, and the project has many recent
high-severity advisories, some still unpatched. **Portainer is off by default**: it
gives full control of Docker (root on the Pi), and anyone on the office network could
claim its first-run admin. It can still be started on demand, bound to the Pi itself
and reached over an SSH tunnel (see `docker-compose.yml`).

## Safety nets

- **Recycle bin**: files deleted over SMB or from the admin page go to hidden `.recycle/`
  folders (one for the office share, one per machine). Files deleted more than **60 days**
  ago are removed automatically (Settings page: adjustable, 0 = off; "Empty now" button).
  The age counts from deletion: moving a file into the recycle folder sets its change time.
- **Nightly backups** off the Pi with restic (host/backup.sh, cnc-backup.timer), checked weekly,
  restore drill automated (tests/test_backup.sh and the full-stack test).
- **Pinned versions**: Debian Bookworm Samba 4.17 is pinned. SMB1 is deprecated
  upstream, so Samba is only upgraded on purpose, after testing.
- Oplocks are disabled on both sides, so the two Samba instances never cache files the
  other side is changing.

## Testing

Everything below runs in Docker on a Mac; **nothing has run on a real Pi or a real CNC
yet.** Run it all again with `tests/run_all.sh --fullstack` (see `tests/README.md`).

| Suite | What it covers |
|---|---|
| Full stack (simulated Pi), 53 checks | Docker-in-Docker "Pi" with an office and a CNC network, the real compose stack and generated firewall, simulated office PCs, Haas, FANUC FTP, a serial device server and a rogue device. Isolation (no forwarding, closed ports, destination checks), SMB3/SMB1/FTP file flow in both directions, serial punch to office, FOCAS bridge path, restarts, fallback firewall |
| Admin web app, 49 | Login, CSRF, machines, users, validation, CNC renumbering, office change with rollback |
| Control types, 41 | Every control type renders, generates the right shares/users/firewall |
| Serial DNC over TCP, 32 | `(SEND ...)` requests, byte-exact sends, ≤10 chars after XOFF, punches saved, interrupts, cancel, live settings |
| Serial DNC over a tty, 8 | The USB-adapter code path, exclusive lock, 1.16 MB drip-feed byte-exact through XOFF pauses, adapter unplugged, 40 concurrent admin edits |
| FOCAS bridge (FANUC's real library on 32-bit ARM, x86-64 and 32-bit x86), FOCAS admin side | Library loads (every function used exists in each build), FANUC error codes, token, input checks, model/alarm/message and folder-listing parsing, replace restores the old program on failure (by O-number and in folders), folder path checks |
| Review fixes, 40 + 20 | One regression check per fix from the two security and correctness reviews |
| Recycle, health/alerts, appearance, 21 / 33 / 33 | Retention purge never follows symlinks; host health and email alerts; themes and logo validation |
| Backup / restore drill, 24 | Real `backup.sh` / `restore.sh` with restic: wipe everything, restore, compare every file |
| Fuzzing | ~1500 random/hostile configs; everything accepted must pass real `nft -c` and `testparm` with no injected settings |
| Browser (Chromium), 39 | Every page renders, form fields follow the control type, live DNC status and progress, FOCAS table, upload/delete, no JavaScript errors |
| Static | ruff, shellcheck, Caddyfile and compose validation, pip-audit |

### Independent review (security and correctness)

Two independent reviews of the code found real problems. All of these were fixed, and
each has a regression test:
- **Config injection:** a machine display name with line breaks could inject Samba settings
  (shown enabling guest access). Names ending in a newline, and subnets in netmask form,
  broke the Samba config and the firewall.
- **Exposed web tools:** File Browser with `admin/admin`, and an unclaimed Portainer, were
  reachable from the office.
- **Firewall:** rules didn't check which local address traffic was for (cross-network SMB reached
  the other Samba at TCP level, though Samba still refused the login). There was also no fallback
  if the generated rules failed at boot.
- **Admin page:** blank when reached by IP, so an office IP change could never be confirmed.
  Rollback edge cases fixed (failed revert, early countdown, confirm during apply).
- **Partial failures:** a failed container restart was forgotten; it is now retried on the next save.
- **FOCAS:** "replace" could lose a program if the write failed; the bridge had no authentication;
  downloads had no time limit.
- **Serial:** received files weren't deletable by office users. A port-setting edit aborted a running
  transfer. Sends could queue on a dead port. Receive buffers had no size limit.
- **Accounts:** names like `backup` or `ftp` could crash-loop a file server.
- **Web:** XSS in confirm dialogs, an open redirect after login, and no login rate limit.
- **Build:** images couldn't be built on the Pi (Docker NAT is disabled, so builds now use the host network).
- **Flask:** upgraded to 3.1.3 for a published vulnerability.

A second review, after backups, health alerts, themes and logo upload were added, found
15 more issues. All fixed, with regression checks (`tests/test_review2_fixes.py` and the
suites for each feature). The main ones:
- **FOCAS:** program numbers outside 1-9999 wrapped in the library's 16-bit field
  (O66770 would have deleted O1234). Now refused by both the admin page and the bridge.
- **Office network rollback:** recovering an unconfirmed change at start-up blocked the admin
  page; pressing Revert while the change was still being applied let the new settings win.
- **CNC network:** a half-failed address change left the Pi and the config disagreeing; the
  old address is now restored.
- **Machine folders:** a file or symlink where `received/` should be broke every save (and a
  symlink could have pointed the ownership fix elsewhere). It is now moved aside and the folder
  recreated, without following links.
- **Recycle:** deleting the same file name twice overwrote the first recycled copy.
- **Alerts:** a damaged alert-state file stopped all alert emails.
- **Logo upload:** only real PNG/JPEG (checked by content, not name), 1 MB limit, served with
  `nosniff` and a locked-down content policy so an uploaded file can't run script.
- Checked and **not** a problem: Caddy's on-demand TLS can't be triggered from outside
  (tested with arbitrary SNI names).

### Container vulnerability scan (Trivy)

All images were scanned for HIGH and CRITICAL CVEs. Fixes applied:
- **Caddy:** upgraded 2.8.4 → 2.11.7. The old version had 93 findings with fixes, several in Caddy itself.
- **Fresh bases:** every image is now built on freshly pulled base images with Debian security updates applied.
- **Python packages:** `urllib3` pinned to 2.8.0.
- **Build tools removed:** `pip` and `setuptools` are no longer in the running images, so their bundled
  libraries are gone too.

Result: **no HIGH or CRITICAL finding with an available fix** in any of our images. Remaining:
- Caddy 2.11.7 has 4 HIGH findings in its Go runtime, fixed only in a future Caddy release.
- Debian bookworm issues with no fix ("will not fix", "fix deferred" or unfixed). The critical ones
  don't apply here:
  - zlib's minizip isn't used.
  - The lmdb CVEs are in py-lmdb, not used.
  - SQLite overflow needs attacker-supplied SQL; the event log uses parameterised queries.
  - glib's D-Bus code is only used by nmcli talking to the Pi's own NetworkManager.

Rebuild images regularly (`docker compose build --pull`) and rescan:
```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy image --severity HIGH,CRITICAL local/cnc-admin:1.0
```

## Hardware (bill of materials)

- Raspberry Pi 5, 4 GB or 8 GB
- NVMe HAT and a 256 GB NVMe SSD (boot from the SSD; avoid SD cards for 24/7 use)
- USB 3 gigabit Ethernet adapter (RTL8153 or AX88179 chipset), for eth1
- Official 27 W USB-C power supply, plus a small UPS or a surge protector
- Case with active cooling
- 5- or 8-port unmanaged gigabit switch for the CNC network, plus cabling to each machine
- For serial machines: an FTDI-based USB-to-RS-232 adapter per machine, or a Moxa NPort
  5110/5150 serial device server, plus a cable wired for the control (FANUC: DB25)

## Install (summary)

1. Flash Raspberry Pi OS Lite 64-bit to the NVMe drive, enable SSH, and connect eth0 to the office network.
2. Copy this folder to `/opt/cnc-server`, then `cp .env.example .env` and set `OFFICE_HOSTNAME`.
3. `sudo ./host/install.sh`. This installs Docker, Cockpit, and nftables, creates the
   network connections, writes the initial config (3 machines with generated passwords),
   asks for the admin password, loads the firewall, and starts everything.
4. Open `https://<pi>:8443`, add office users, and print a setup sheet for each machine.

## Phases

1. **Approval**: review with IT / cyber security, covering the dual-homed Pi,
   the isolated SMB1 segment, and the Windows XP PCU 50 on that segment. Get the DHCP
   reservation, hostname, and office subnet.
2. **Bench build**: install on the Pi. Test SMB3 from a Windows PC, and run a laptop on
   the CNC switch with SMB1 enabled to stand in for a machine.
3. **First machine (Haas #1)**: follow its setup sheet. Load a program, save one back,
   and check it from the office side.
4. **Siemens 840D (PCU 50)**: configure the company-network adapter and map the drive, then repeat the tests.
5. **Haas #2** and cut-over: retire the old SMB1 share.
6. **Operations**: backups (verified with a test restore), a one-page runbook for shop staff.

## Open items / risks

- **FOCAS library licence**: `libfwlib32` is FANUC's proprietary software. fanuc-driver's Apache
  licence covers Ladder99's code, not necessarily FANUC's binaries. Confirm licensing with FANUC
  (FOCAS2 library) before production. A FANUC-supplied copy can be dropped into `focas/lib/`.
- **FOCAS on the Pi 5**: FANUC has no 64-bit ARM library, so the Pi uses the 32-bit ARM one
  (x86-64 hosts get the native x86-64 build). The Pi 5 runs 32-bit containers natively,
  and the library's 32K segment alignment suits the Pi 5's 16K-page kernel. Verify on the real Pi;
  the fallback is `kernel=kernel8.img` (4K pages) in `/boot/firmware/config.txt`.
- **FOCAS struct layouts** follow FANUC's `fwlib32.h` (checked against the x86-64 library's
  disassembly for the 32-bit "long"). Program upload/download, status, alarm text and program
  folders must be verified on a real control (start with a test program number or scratch folder).
  The folder functions (`cnc_rdpdf_*`, `cnc_upstart4`/`dwnstart4`, `cnc_pdf_del`) are only used when
  the control reports program folders; older controls keep the O-number functions.

- **FTP and SMB1 are plaintext/weak** by design for old controls. This is acceptable
  only because the CNC network is isolated, with no gateway and no routing.
- **Serial pin-outs and parameters** vary by control. Use a null modem with the
  CNC-side jumpers (see `docs/SERIAL-PORTS.md`), and test each cable and its settings on the bench first.
- **serial-dnc repo**: its engine now lives in `admin/cncadmin/serial_dnc.py` and `dnc_files.py`.
  Fixes made in one place need copying to the other, or the standalone repo can be retired.

- **Windows XP PCU 50** is itself an unsupported OS. The isolated switch, with no
  gateway and no routing, is what protects it. Don't connect it to anything else.
- **Haas Classic authentication**: NTLMv1 is allowed. If a control still fails, look
  at the `samba-cnc` log. LANMAN auth (weaker still) can be added to
  `admin/cncadmin/conf_templates/smb-cnc.conf.j2` as a last resort.
- **8.3 filenames** on older controls: name mangling is on (the default). Agree on a
  naming convention such as `O01234.NC`.
- **Serial speed at high baud:** to stay within FANUC's XOFF limit the engine writes 4 bytes
  at a time; at 115200 baud a drip-feed runs at about 60% of line speed (fine at 4800/9600).
- **NTLMv1 on the CNC network** (needed by Haas Classic and XP) lets anyone who can capture traffic
  there recover a machine's password hash. Keep the CNC switch physically controlled.
- **Rebuild images periodically** (`docker compose build --pull`) to pick up Debian security updates.
