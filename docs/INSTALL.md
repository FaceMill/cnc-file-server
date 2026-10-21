# Installing the CNC File Server

Step-by-step installation on a Raspberry Pi 5. Plan on about two hours for the first
install, including the bench test. Steps marked **(IT)** need your IT department.

- [0. Before you start](#0-before-you-start)
- [1. Assemble the hardware](#1-assemble-the-hardware)
- [2. Install Raspberry Pi OS](#2-install-raspberry-pi-os)
- [3. First boot](#3-first-boot)
- [4. Check the network ports](#4-check-the-network-ports)
- [5. Get the code onto the Pi](#5-get-the-code-onto-the-pi)
- [6. Configure `.env`](#6-configure-env)
- [7. Run the installer](#7-run-the-installer)
- [8. Check the installation](#8-check-the-installation)
- [9. First login and configuration](#9-first-login-and-configuration)
- [10. Map the share on office PCs](#10-map-the-share-on-office-pcs)
- [11. Bench test before connecting machines](#11-bench-test-before-connecting-machines)
- [12. Connect the machines](#12-connect-the-machines)
- [13. Optional extras](#13-optional-extras)
- [14. Maintenance](#14-maintenance)
- [15. Backups](#15-backups)
- [16. Alerts, time and UPS](#16-alerts-time-and-ups)
- [17. Disaster recovery (the SSD or Pi died)](#17-disaster-recovery-the-ssd-or-pi-died)
- [Ports for IT](#ports-for-it)

---

## 0. Before you start

**(IT)** Get approval for a Pi connected to both the office network and an isolated CNC
network, and ask IT for:

| Item | Example | Used for |
|---|---|---|
| A hostname | `cnc-files.example.com` | `.env`, the HTTPS certificate, the office share path |
| A DHCP reservation for the Pi's **onboard** Ethernet MAC (step 3 shows it) | `10.10.x.y` | The office address |
| The office subnet(s) allowed to use the server | `10.10.0.0/16` | Firewall (admin page → Network) |

Plan the CNC network: the Pi is `192.168.50.1/24` by default and each machine gets a
fixed address (`.11`, `.12`, …). It must not overlap any office subnet.

You need: a computer with an SD card reader or USB-NVMe enclosure, a network cable to
the office network, and SSH (built into Windows 10+, macOS and Linux).

## 1. Assemble the hardware

1. Fit the NVMe HAT and SSD, plus the active cooler, following the HAT's instructions.
2. Plug the **USB 3 Ethernet adapter** into a blue USB 3 port. It becomes `eth1` (CNC side).
3. Connect the Pi's **onboard** Ethernet port (`eth0`) to the office network.
4. Don't connect the CNC switch yet.

## 2. Install Raspberry Pi OS

1. On your computer, install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi 5**, then **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**
   (the current release, Debian 13 "Trixie"; Bookworm works too). For a test bench, the
   desktop version is fine; see [Test bench](#test-bench-wi-fi-and-usb-adapters).
3. Choose the storage:
   - **NVMe SSD in a USB enclosure** (best), or
   - an SD card. You can move to NVMe later with the *SD Card Copier* tool and the boot-order setting below.
4. Click **Next → Edit settings**:
   - Hostname: the short name, e.g. `cnc-files`
   - Username and a strong password (this is the Pi login, also used for Cockpit)
   - Locale and time zone: yours
   - Services: **enable SSH**, preferably with your public key
5. Write the image and put the SSD (or SD card) in the Pi.
6. If booting from NVMe with an SD card still present, set NVMe first after the first boot:
   `sudo raspi-config` → *Advanced Options* → *Boot Order* → *NVMe/USB Boot*.

## 3. First boot

Power on and wait a minute. Find the Pi's address (IT's DHCP list, or `ping cnc-files.local`),
then:

```bash
ssh <user>@<pi-address>
sudo apt update && sudo apt full-upgrade -y
ip link show eth0        # "link/ether xx:xx:..." is the MAC to give IT (step 0)
sudo reboot
```

## 4. Check the network ports

```bash
ip -br link
```

You should see `eth0` (onboard) and `eth1` (the USB adapter). If the adapter is called
something like `enx00e04c...`, turn off predictable interface names, then reboot:

`sudo raspi-config` → *Advanced Options* → *Network Interface Names* → **No**

By default the installer uses `eth0` = office and `eth1` = CNC. Other names (a Wi-Fi
office side, several CNC ports) are set in `.env` (`OFFICE_IF`, `CNC_IF`).

## 5. Get the code onto the Pi

```bash
sudo apt install -y git
sudo git clone https://github.com/<owner>/cnc-file-server.git /opt/cnc-server   # this repository's URL
```

(Alternatively, copy the folder from your computer: `scp -r cnc-file-server <user>@<pi>:` then
`sudo mv ~/cnc-file-server /opt/cnc-server`.)

## 6. Configure `.env`

```bash
cd /opt/cnc-server
sudo cp .env.example .env
sudo nano .env
```

| Setting | Set to |
|---|---|
| `OFFICE_HOSTNAME` | The hostname from IT, e.g. `cnc-files.example.com` |
| `CNC_UID` / `CNC_GID` | Leave `2000` unless that ID is already used on the Pi |
| `ENABLE_NMBD` | Leave `no` unless a control can only connect by name instead of IP |
| `OFFICE_IF` | Leave `eth0` (Wi-Fi test bench: `wlan0`) |
| `CNC_IF` | Leave `eth1`. Several ports, e.g. `"eth1 eth2"`, are bridged as `br-cnc` (the Pi acts as the switch) |

Everything else (machines, users, IPs, firewall) is configured later in the web page.

## 7. Run the installer

```bash
cd /opt/cnc-server
sudo ./host/install.sh
```

It's safe to run over SSH. It takes 15–30 minutes, mostly building the container images,
and does the following:

0. **Pi 5 only, first run:** a Pi 5 boots a kernel with 16K memory pages, which can't run the
   32-bit ARM FANUC FOCAS library. The installer switches to Raspberry Pi's standard 4K-page
   kernel (`kernel=kernel8.img` in `/boot/firmware/config.txt`, backup kept next to it) and
   stops. **Reboot (`sudo reboot`) and run the installer again.** `getconf PAGESIZE` shows
   `4096` once it's in effect.

1. Updates the OS. Installs nftables, Cockpit, unattended upgrades and restic.
   Removes `brltty` and ModemManager, which interfere with USB serial adapters.
2. Installs Docker, with Docker's own firewall handling turned off (this project's
   firewall is the only one).
3. Creates the `cncfiles` account and `/srv/cnc` (the program folders).
4. Sets up the network:
   - renames the `eth0` connection to `office-net`, still on DHCP;
   - creates `cnc-net` on `eth1` at `192.168.50.1/24` with no gateway;
   - turns IP forwarding off and reverse-path filtering on.
5. Builds the images, including the 32-bit FOCAS bridge.
6. Creates the site configuration with three starting machines: `haas1`, `haas2`,
   `siemens1`. Their share passwords are printed and are also shown on the admin page.
7. **Asks you for the admin password** (at least 10 characters). Store it safely.
8. Loads the firewall and makes it load at boot, with the locked-down fallback.
9. Starts everything. Containers restart automatically after a reboot.

It finishes with the admin URL.

## 8. Check the installation

```bash
cd /opt/cnc-server
sudo docker compose ps                  # 6 services "running": samba-office, samba-cnc,
                                        # ftp-cnc, focas-bridge, cnc-admin, caddy
ip -br addr                             # eth0 office address, eth1 192.168.50.1/24
sysctl net.ipv4.ip_forward              # = 0
sudo nft list ruleset | head -30        # the generated firewall
```

If a service isn't running: `sudo docker compose logs <service>`.

Reboot once (`sudo reboot`) and check again: everything should come back on its own.

## 9. First login and configuration

1. Open `https://<OFFICE_HOSTNAME>:8443`.
   - The certificate is from the Pi's own internal CA, so the browser warns once. You
     can accept it, or install `/opt/cnc-server/data/caddy/caddy/pki/authorities/local/root.crt`
     as trusted on office PCs, or replace it with a certificate from your organisation (step 13).
2. Log in as `admin` with the password from step 7.
3. **Network** page: the installer allowed only the office subnet `eth0` was on.
   - Add any other subnets IT wants to allow, one per line, then click *Apply*.
   - Then confirm the change within 2 minutes. If you don't, it reverts on its own.
4. **Office users** page: add a login for each programmer (or a shared one).
5. **Machines** page: rename, edit or remove the starting machines, and add yours. For each one:
   - choose its **control type**;
   - for network machines, give it a fixed IP in the CNC network;
   - open its page and **print the setup sheet**.

## 10. Map the share on office PCs

In File Explorer, choose *This PC → Map network drive*, then:

- Folder: `\\<OFFICE_HOSTNAME>\CNC`
- Tick *Connect using different credentials* and enter an office user from step 9.

Or from a command prompt:

```
net use Z: \\cnc-files.example.com\CNC /user:programmer /persistent:yes
```

The share requires SMB3 with encryption (Windows 8 / Server 2012 and newer).

## 11. Bench test before connecting machines

Plug the CNC switch into the USB adapter (`eth1`). Then use a laptop as a stand-in machine:

1. Give the laptop a fixed IP of one of the machines, e.g. `192.168.50.11` /
   `255.255.255.0`, with no gateway, and plug it into the CNC switch.
2. From the laptop, connect to `\\192.168.50.1\haas1` with user `haas1` and the password
   from that machine's setup sheet.
   - Windows 10/11 has the SMB1 client off, but negotiates SMB2/3 with the Pi automatically.
   - On Linux: `smbclient //192.168.50.1/haas1 -U haas1 -m NT1` tests SMB1 specifically.
3. Copy a file both ways and check it appears in the office share, and the other way round.
4. Check that the laptop **cannot** reach the admin page (`https://192.168.50.1:8443`)
   or anything on the office network.
5. On the Status page, the machine shows *reachable* and *connected*.

## 12. Connect the machines

Do one machine at a time, starting with the least critical:

1. Enter the values from its setup sheet on the control. Per-control details are in
   [cnc-client-setup.md](cnc-client-setup.md).
2. Load a program from the Pi and save one back. Check it from the office.
3. **Serial machines:**
   - Read [SERIAL-PORTS.md](SERIAL-PORTS.md) first. It covers adapters, null-modem wiring,
     and stable port names.
   - Plug the adapter in. It shows up under `/dev/serial/by-id/` without a restart.
   - Add the machine with that port, then punch the call program `O9999 (SEND O1234)`.
4. **FOCAS machines:**
   - Read the licence note in [../focas/lib/README.md](../focas/lib/README.md).
   - Start with a spare program number: Send, Read, then Delete it. Do that before using
     real programs.

## 13. Optional extras

**Use a FANUC-supplied FOCAS library** instead of the downloaded one:

```bash
sudo cp libfwlib32.so /opt/cnc-server/focas/lib/libfwlib32.so   # the build for FOCAS_PLATFORM in .env
cd /opt/cnc-server && sudo ./host/build-images.sh focas && sudo docker compose up -d focas-bridge
```

**A certificate from your organisation's CA:**
1. Put `cert.pem` and `key.pem` in `/opt/cnc-server/data/caddy/`.
2. In `caddy/Caddyfile`, replace each `tls internal` with `tls /data/cert.pem /data/key.pem`.
3. Run `sudo docker compose restart caddy`.

**Portainer** (off by default; it gives full control of Docker):

```bash
sudo docker compose --profile portainer up -d portainer
# from your computer, via an SSH tunnel:
ssh -L 9443:127.0.0.1:9443 <user>@<pi>      # then open https://localhost:9443
```

## 14. Maintenance

**Update:**
```bash
cd /opt/cnc-server
sudo git pull
sudo ./host/build-images.sh
sudo docker compose up -d
```
Rebuild every month or two, even with no code changes, to pick up Debian security fixes
in the images. The OS updates itself.

**Back up:** see [15. Backups](#15-backups). For staff, print [RUNBOOK.md](RUNBOOK.md).

**Reset the admin password:**
```bash
cd /opt/cnc-server
sudo docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli set-admin-password
```

**Logs:** the admin page *Logs*, `sudo docker compose logs <service>`, or Cockpit.

**Move the Pi to a new office address:** change the DHCP reservation (IT) and reboot. Or use
the Network page, which keeps the 2-minute confirm and rollback.

## 15. Backups

Nightly backups of every program (`/srv/cnc`) and the whole configuration
(`config/`, `.env`) with [restic](https://restic.net). Backups are encrypted,
deduplicated, and checked once a week. The last 3 runs are kept, plus 14 daily,
8 weekly and 12 monthly copies.

1. Choose a destination off the Pi:
   - a USB disk mounted at, for example, `/mnt/backup`;
   - an SFTP server: set up an SSH key for root first;
   - a restic REST server.

   Ask IT for space on a file server if possible.
2. Run:
   ```bash
   sudo /opt/cnc-server/host/setup-backup.sh
   ```
   It asks for the destination and creates a random repository password, then shows it.
   **Write the password down and keep it off the Pi.** Without it, the backups can't be
   restored after the Pi is lost. It then runs a first backup and enables the nightly
   timer (02:30).
3. The Status page shows the last backup and warns if one fails or is more than 36 hours old.

By hand:
```bash
sudo /opt/cnc-server/host/backup.sh                    # back up now
sudo /opt/cnc-server/host/restore.sh list              # list backups
sudo /opt/cnc-server/host/restore.sh file /srv/cnc/haas1/O1234.NC   # old version next to the current file
```

## 16. Alerts, time and UPS

**Alert emails:** admin page → *Settings* → *Alert emails*. Enter your mail server (ask IT
for an internal relay), the sender and the recipients, then click **Send a test email**.

The server checks every 5 minutes and emails new problems, resolved problems, and a daily
reminder for anything critical. It checks:
- services and disk space;
- backups;
- serial ports;
- the Pi itself: temperature, under-voltage and throttling, SSD wear and SMART status, clock and UPS.

The Pi's checks come from `host/health.py`, which `cnc-health.timer` runs every 5 minutes.

**Time:** the Pi syncs its clock from the internet by default. If IT has a time server:
```bash
sudo mkdir -p /etc/systemd/timesyncd.conf.d
printf '[Time]\nNTP=ntp.example.com\n' | sudo tee /etc/systemd/timesyncd.conf.d/local.conf
sudo systemctl restart systemd-timesyncd
timedatectl            # "System clock synchronized: yes"
```

**UPS (recommended):** with a USB UPS (APC, Eaton, CyberPower …) and
[NUT](https://networkupstools.org), the Pi shuts down cleanly before the battery runs out,
and the Status page shows power cuts.

1. Install NUT:
   ```bash
   sudo apt install -y nut
   ```
2. Set `/etc/nut/nut.conf` to:
   ```
   MODE=standalone
   ```
3. Add the UPS to `/etc/nut/ups.conf`:
   ```
   [ups]
     driver = usbhid-ups
     port = auto
   ```
4. Add a monitor user to `/etc/nut/upsd.users`, with a password of your choice:
   ```
   [upsmon]
     password = <choose-one>
     upsmon primary
   ```
5. Add these lines to `/etc/nut/upsmon.conf`:
   ```
   MONITOR ups@localhost 1 upsmon <choose-one> primary
   SHUTDOWNCMD "/sbin/shutdown -h +0"
   ```
6. Restart NUT and check that it can read the UPS:
   ```bash
   sudo systemctl restart nut-server nut-monitor
   upsc ups@localhost ups.status          # OL = on line power
   ```

## 17. Disaster recovery (the SSD or Pi died)

You need the backup destination, the **repository password** from step 15, and this repository.

1. Replace the hardware. Do steps 1–7 of this guide, using the same hostname and `.env`.
   The installer creates a fresh configuration for now.
2. Point the new Pi at the old backups. Paste the saved password in place of
   `<saved repository password>`:
   ```bash
   echo '<saved repository password>' | sudo tee /etc/cnc-backup.pass >/dev/null
   printf 'RESTIC_REPOSITORY=<destination>\nRESTIC_PASSWORD_FILE=/etc/cnc-backup.pass\n' | sudo tee /etc/cnc-backup.env >/dev/null
   sudo chmod 600 /etc/cnc-backup.pass /etc/cnc-backup.env
   ```
3. Restore everything:
   ```bash
   sudo /opt/cnc-server/host/restore.sh list
   sudo /opt/cnc-server/host/restore.sh all
   ```
   This puts back every program and the whole configuration: machines, users, passwords,
   network settings and the admin password. It then regenerates the server configs,
   reloads the firewall and restarts the services.
4. Check the Status page, then connect one machine and one office PC. Nightly backups
   continue into the same repository.

The full-stack test (`tests/fullstack.sh`) runs this procedure every time: it backs up,
wipes the programs and configuration, restores, and checks that machines and the admin
login work again.

## Other hosts (not a Pi)

The server isn't tied to the Pi. Any **Linux** machine running Docker Engine can host it,
for example an x86-64 mini PC or a VM, as long as it has:

- **Two network ports** (office and CNC switch). In a VM, two bridged adapters.
- **NetworkManager, nftables and systemd** (Debian 12 or Ubuntu 22.04/24.04). The admin page
  changes addresses through `nmcli`; the firewall and timers run on the host.
- **Docker Engine**, not Docker Desktop: Desktop hides the network ports behind its own VM.

Install the OS, then follow sections 5 to 17 as written. `host/install.sh` picks the FOCAS
library for the CPU and writes `FOCAS_PLATFORM` to `.env`:

| CPU (`uname -m`) | FOCAS bridge |
|---|---|
| `aarch64` (Pi) | 32-bit ARM container (FANUC has no 64-bit ARM library) |
| `x86_64` | native x86-64 |
| `i686` | 32-bit x86 |

After building, it checks the library loads. If it doesn't (some 64-bit ARM servers can't
run 32-bit containers), only FANUC FOCAS machines are affected. Pi-only health readings
(SoC temperature, throttling) are blank on other hardware.

Not yet tested on real non-Pi hardware; the FOCAS bridge has been tested on all three
platforms under emulation.

## Test bench (Wi-Fi and USB adapters)

For trying the server before IT approval, without the office cabling:

- **Office side on Wi-Fi.** In Raspberry Pi Imager, set the Wi-Fi network along with SSH. Use
  a home router or phone hotspot, not the work Wi-Fi, until IT has approved the Pi. The
  allowed office subnet is taken from the Wi-Fi network at install time; your laptop must be
  on it.
- **CNC side on two USB Ethernet adapters**, one test machine or laptop on each, no switch:
  the installer bridges them as `br-cnc` with `192.168.50.1/24`. Machine-to-machine traffic
  passes through the bridge, as it would through a switch; nothing is routed to the office.
- **Desktop OS is fine.** The Pi's own browser reaches the admin page at
  `https://localhost:8443` even if a network change goes wrong. Once installed, the firewall
  closes everything else on the office side (VNC/RDP included); use SSH, the local screen, or
  Cockpit. Raspberry Pi Connect still works (it connects outward).

`.env`:

```bash
OFFICE_IF=wlan0
CNC_IF="eth1 eth2"
```

### Setup hotspot (no office network at all)

The Pi can be its own Wi-Fi access point instead: your laptop joins the Pi's network and gets
the admin page and the office share, with no office network or IT involvement. In `.env`:

```bash
OFFICE_IF=wlan0
OFFICE_MODE=hotspot
HOTSPOT_SSID=cnc-setup            # optional; default cnc-setup-<hostname>
# HOTSPOT_PASSWORD=               # optional; the installer creates one and prints it
CNC_IF="eth0 eth1"                # the Pi's own port is free for a machine
```

- **Run the installer on the Pi's own screen and keyboard** (the desktop terminal), not over
  SSH on the Wi-Fi: it switches the Wi-Fi to the hotspot as its last step, which would cut the
  session (it refuses unless `HOTSPOT_OVER_SSH=1`). It still needs internet until then, so the Pi
  must be on a Wi-Fi network (or cable) when you start it.
- At the end it prints the network name and password. On the laptop, join that Wi-Fi, then
  open **https://10.42.0.1:8443** (accept the certificate warning). The office share is
  `\\10.42.0.1\CNC`; Cockpit is `https://10.42.0.1:9090`.
- The hotspot has **no internet** and **no route to the CNC network** (the firewall drops all
  forwarding). The Pi has one Wi-Fi radio, so while the hotspot is on it isn't on any other
  Wi-Fi network. For updates, or to move to a real office network: set `OFFICE_MODE=client` and
  run the installer again. It first rejoins the Wi-Fi network the Pi was on before (kept, with
  its password), then carries on.
- By hand: `sudo ./host/setup-hotspot.sh status`, `... off` (back to the previous Wi-Fi),
  `... on`.
- The **Wi-Fi country** must be set (Raspberry Pi Imager does this; otherwise
  `sudo raspi-config` → *Localisation Options* → *WLAN Country*), or the hotspot won't start.
- Not tested on real Wi-Fi hardware yet; the profiles, firewall and admin page are (in
  containers, with real NetworkManager and nft).

With two USB adapters the names `eth1`/`eth2` can swap between reboots; with a bridge it
doesn't matter. Check the names with `ip -br link` before installing. If you change
`OFFICE_IF`/`CNC_IF` after installing, the installer refuses to continue until
`config/site.json` matches (edit its two `"interface"` fields, or start again).

The installer tells NetworkManager never to auto-configure the CNC ports
(`/etc/NetworkManager/conf.d/90-cnc-ports.conf`), so a replugged adapter rejoins the CNC
network instead of trying DHCP.

For the real server, rebuild with Raspberry Pi OS Lite on the SSD: onboard `eth0` to the office,
one USB adapter (`eth1`) to the CNC switch.

## Ports for IT

The firewall is generated from the configuration and drops everything else.

| Interface | From | Port | Service |
|---|---|---|---|
| eth0 (office) | Allowed office subnets | TCP 22 | SSH |
| | | TCP 80, 443 | Redirect to the admin page |
| | | TCP 8443 | Admin page (HTTPS) |
| | | TCP 9090 | Cockpit (HTTPS) |
| | | TCP 445 | Office share (SMB3, encrypted) |
| eth1 (CNC) | SMB machines' IPs only | TCP 139, 445; UDP 137, 138 | Machine shares (SMB1–3) |
| | FTP machines' IPs only | TCP 21, 30000–30049 | FTP (active and passive) |
| outbound | Pi | any | OS and image updates (office side); FOCAS 8193 and serial device servers (CNC side) |

No traffic is forwarded between `eth0` and `eth1`.
