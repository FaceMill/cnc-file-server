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
2. Choose **Raspberry Pi 5**, then **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**.
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

The installer expects exactly `eth0` = office and `eth1` = CNC.

## 5. Get the code onto the Pi

The repository is **private**, so cloning needs a GitHub credential. The simplest is a
read-only token:

1. On GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token*. Repository access: only `cnc-file-server`. Permissions:
   *Contents: Read-only*. Short expiry is fine.
2. On the Pi:
   ```bash
   sudo apt install -y git
   sudo git clone https://github.com/<your-account>/cnc-file-server.git /opt/cnc-server
   # Username: your GitHub user. Password: paste the token (it is not stored).
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

Everything else (machines, users, IPs, firewall) is configured later in the web page.

## 7. Run the installer

```bash
cd /opt/cnc-server
sudo ./host/install.sh
```

It's safe to run over SSH. It takes 15–30 minutes, mostly building the container images,
and does the following:

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
sudo cp libfwlib32.so /opt/cnc-server/focas/lib/libfwlib32.so   # 32-bit ARM Linux build
cd /opt/cnc-server && sudo docker compose build focas-bridge && sudo docker compose up -d focas-bridge
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
sudo git pull                       # needs the token again for a private repo
sudo docker compose build --pull
sudo docker compose up -d
```
Rebuild every month or two, even with no code changes, to pick up Debian security fixes
in the images. The OS updates itself.

**Back up** `/srv/cnc/` and `/opt/cnc-server/config/` (see the README).

**Reset the admin password:**
```bash
cd /opt/cnc-server
sudo docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli set-admin-password
```

**Logs:** the admin page *Logs*, `sudo docker compose logs <service>`, or Cockpit.

**Move the Pi to a new office address:** change the DHCP reservation (IT) and reboot. Or use
the Network page, which keeps the 2-minute confirm and rollback.

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
