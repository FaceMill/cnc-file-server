# Serial ports: from the CNC to the Docker container

Adapted from the serial-dnc project. This guide covers everything between the RS-232 port on the
machine and the **Serial port** field in the admin page: adapters, cables, the Pi, Docker and testing. Work through it in order. Each step has
a test, so you know a step works before you rely on it in the next one.

```
 CNC RS-232 port ──cable── USB-serial adapter ──USB── Raspberry Pi ─Docker── cnc-admin container
   (DB25/DB9)     (null modem   (FTDI etc.)           /dev/ttyUSB0             /dev/serial/by-id/...
                   + jumpers)                         /dev/serial/by-id/...    (same device)
```

Contents:
1. [Choose adapters](#1-choose-adapters)
2. [Cables and wiring](#2-cables-and-wiring)
3. [Set up the Pi](#3-set-up-the-pi)
4. [Give each port a stable name](#4-give-each-port-a-stable-name)
5. [Test the port on the host, before Docker](#5-test-the-port-on-the-host-before-docker)
6. [How Docker gets access to the ports](#6-how-docker-gets-access-to-the-ports)
7. [Test the port inside the container](#7-test-the-port-inside-the-container)
8. [Configure the machine in the admin page](#8-configure-the-machine-in-the-admin-page)
9. [Adding a machine later](#9-adding-a-machine-later)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Choose adapters

| Chip | Verdict |
|---|---|
| **FTDI FT232R / FT232RL** (1 port) | **Recommended.** Well supported in Linux, has a unique serial number (so a stable name), reliable flow control. |
| **FTDI FT4232H** (4 ports in one box) | Recommended if you'll have several machines. Each port gets its own name (`...-if00`, `-if01`, ...). |
| Prolific PL2303 | Works, but many cheap ones are counterfeit chips that misbehave. |
| WCH CH340 / CH341 | Works, but has **no serial number**. Two identical adapters get the same by-id name, so see [4b](#4b-identical-adapters-without-serial-numbers). |

Also check that:
- The adapter does **true RS-232** (±12 V), not a 3.3 V/5 V "TTL" serial adapter. TTL adapters can damage the CNC port and won't talk to it.
- It has a **DB9 male** connector. The cable adapts it to the machine.
- You buy from a reputable supplier. FTDI-based adapters from known brands avoid fake chips.

**Distance.** RS-232 is specified for about 15 m (50 ft). At 9600 baud with good shielded cable, longer runs
often work, but noise from spindle drives and VFDs gets worse with length. If machines are far from the
server, either keep the USB adapter on the server and run shielded RS-232 cable, or use an **Ethernet serial
server** (Moxa NPort, Lantronix) next to each machine. The Port field accepts `socket://<ip>:<port>` for
those (set the device to "TCP server / raw socket" mode).

## 2. Cables and wiring

The CNC and the adapter are both "DTE" devices, so the cable must be a **null modem**: transmit on one end
goes to receive on the other.

### Pinouts

| Signal | DB9 (adapter) | DB25 (typical CNC) |
|---|---|---|
| TXD (transmit) | 3 | 2 |
| RXD (receive) | 2 | 3 |
| GND (signal ground) | 5 | 7 |
| RTS | 7 | 4 |
| CTS | 8 | 5 |
| DTR | 4 | 20 |
| DSR | 6 | 6 |
| DCD | 1 | 8 |

### Cable A: XON/XOFF (software flow control). Use this for Fanuc and Haas.

```
 Adapter DB9                         CNC DB25
   3 TXD  ─────────────────────────  3 RXD
   2 RXD  ─────────────────────────  2 TXD
   5 GND  ─────────────────────────  7 GND
   7 RTS ┐                           4 RTS ┐
   8 CTS ┘ jumper                    5 CTS ┘ jumper
   1 DCD ┐                           6 DSR ┐
   4 DTR ├ jumper                    8 DCD ├ jumper
   6 DSR ┘                          20 DTR ┘
   Shield: connect to the DB25 shell at the CNC end only
```

The jumpers make each end think the other side is always ready. Without the CNC-side jumpers, a Fanuc
control stops with **alarm 086 (DR signal off)**. Only three wires actually carry data.

### Cable B: RTS/CTS (hardware flow control). Use it only if the control is set for hardware handshake.

```
 Adapter DB9                         CNC DB25
   3 TXD  ─────────────────────────  3 RXD
   2 RXD  ─────────────────────────  2 TXD
   5 GND  ─────────────────────────  7 GND
   7 RTS  ─────────────────────────  5 CTS
   8 CTS  ─────────────────────────  4 RTS
   4 DTR  ─────────────────────────  6 DSR + 8 DCD
   6 DSR + 1 DCD  ─────────────────  20 DTR
```

Check these against the connector diagram in your machine's manual. Some controls (e.g. newer Fanuc
with a DB9 or a 20-pin Honda connector) use different pins, and some machine builders rewire the front-panel port.

## 3. Set up the Pi

`host/install.sh` already does 3b and 3c (it removes brltty and ModemManager and installs Docker from
Docker's repository). The steps are kept here for troubleshooting.

Docker can only pass USB serial ports through on **Linux**. Docker Desktop on Windows or Mac can't reach them.

### 3a. Plug in and identify the adapter

Watch the kernel log while you plug in the adapter:

```bash
sudo dmesg -w          # plug the adapter in, then press Ctrl+C
```

Expected output:

```
usb 1-1.2: new full-speed USB device number 5 using xhci_hcd
ftdi_sio 1-1.2:1.0: FTDI USB Serial Device converter detected
usb 1-1.2: FTDI USB Serial Device converter now attached to ttyUSB0
```

`attached to ttyUSB0` means the driver loaded and the port exists. Also check:

```bash
lsusb                                   # the adapter should be listed (e.g. "Future Technology Devices")
ls -l /dev/ttyUSB* /dev/ttyACM*         # device nodes
ls -l /dev/serial/by-id/                # stable names, see step 4
```

### 3b. Remove software that takes over serial ports

Desktop Linux installs often include two programs that interfere with serial ports:

```bash
# brltty (a braille display driver) claims CH340/CH341 adapters, so the port vanishes right after plugging in.
# dmesg shows "usbfs: interface 0 claimed by ch341 while 'brltty' sets config".
sudo apt remove brltty

# ModemManager probes new serial ports with modem "AT" commands. The CNC receives them as garbage
# and the port is busy for several seconds after plugging in.
sudo systemctl disable --now ModemManager 2>/dev/null
```

Unplug and replug the adapters afterwards.

### 3c. Docker must come from Docker's own repository

```bash
snap list docker 2>/dev/null && echo "Docker is the SNAP version"
```

If Docker was installed as a **snap** (`sudo snap install docker`), its sandbox can block device access
and some bind mounts. Install Docker from Docker's apt repository instead
(https://docs.docker.com/engine/install/debian/).

## 4. Give each port a stable name

`/dev/ttyUSB0`, `/dev/ttyUSB1`, … are numbered **in the order the adapters are detected**. After a reboot or
replug, the Haas adapter can become `ttyUSB1` and the Fanuc `ttyUSB0`, and then each program goes to the
wrong machine. **Never use `/dev/ttyUSBn` in the admin page.** Use one of these names instead.

### 4a. `/dev/serial/by-id/` (the normal choice)

```bash
ls -l /dev/serial/by-id/
```

Expected output:

```
usb-FTDI_FT232R_USB_UART_A10K1234-if00-port0 -> ../../ttyUSB1
usb-FTDI_FT232R_USB_UART_A10K5678-if00-port0 -> ../../ttyUSB0
```

The name includes the adapter's serial number (`A10K1234`), so it follows **that adapter** no matter which
USB socket it's in or what order things start. Label each adapter and cable with the last few characters.

To find which adapter is which, unplug one and run `ls` again. The entry that disappears is that adapter.

### 4b. Identical adapters without serial numbers

CH340 adapters, and some cheap PL2303 ones, have no serial number. Two of them get the **same** by-id name
(or none). Use the physical USB socket instead:

```bash
ls -l /dev/serial/by-path/
```

Expected output:

```
pci-0000:00:14.0-usb-0:1.2:1.0-port0 -> ../../ttyUSB0
pci-0000:00:14.0-usb-0:1.3:1.0-port0 -> ../../ttyUSB1
```

These names stay fixed **as long as each adapter stays in the same USB socket**. Label the sockets.

### 4c. Optional: your own names (`/dev/cnc-haas`)

A udev rule can give each adapter a readable name. Find the adapter's serial number:

```bash
udevadm info /dev/ttyUSB0 | grep -E "ID_SERIAL_SHORT|ID_VENDOR_ID|ID_MODEL_ID"
```

Expected output:

```
E: ID_SERIAL_SHORT=A10K1234
E: ID_VENDOR_ID=0403
E: ID_MODEL_ID=6001
```

Create `/etc/udev/rules.d/99-cnc-serial.rules` with one line per machine:

```
SUBSYSTEM=="tty", ENV{ID_SERIAL_SHORT}=="A10K1234", SYMLINK+="cnc-haas-vf2"
SUBSYSTEM=="tty", ENV{ID_SERIAL_SHORT}=="A10K5678", SYMLINK+="cnc-fanuc-robodrill"
```

For adapters without a serial number, match the USB socket instead:
`SUBSYSTEM=="tty", ENV{ID_PATH}=="pci-0000:00:14.0-usb-0:1.2:1.0", SYMLINK+="cnc-haas-vf2"`
(`udevadm info` shows `ID_PATH`).

Apply the rules and check:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/cnc-*
```

The admin page lists `/dev/cnc-*` names first in the Serial port suggestions. Names must start with `cnc-` to be listed,
but you can type any path.

## 5. Test the port on the host, before Docker

Testing on the host first tells you whether a fault is in the cable/adapter or in Docker.

Install a simple terminal program:

```bash
sudo apt install picocom
```

### 5a. Loopback test (adapter only, no CNC)

Short **pin 2 to pin 3** on the adapter's DB9 with a paperclip or loopback plug, then run:

```bash
picocom --baud 9600 /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K1234-if00-port0
```

Type a few characters. If they appear, the adapter sends and receives correctly. If nothing appears, the
adapter or driver is faulty. Exit with **Ctrl+A** then **Ctrl+X**.

### 5b. Receive from the CNC (cable + settings)

Connect the cable to the machine. Open picocom with the **same settings as the control**. For example,
Fanuc at 4800 baud, 7 data bits, even parity, 2 stop bits:

```bash
picocom --baud 4800 --databits 7 --parity e --stopbits 2 --flow x \
        /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K1234-if00-port0
```

On the control, punch out (output) a short program. You should see it scroll in picocom.

| What you see | Meaning |
|---|---|
| Readable program text | Cable and settings are right |
| Nothing at all | Wrong wiring (TX/RX not crossed), missing jumpers, wrong I/O channel on the control, or the control is waiting for DSR/CTS |
| Garbage characters | Baud, data bits, parity or stop bits don't match the control |
| Fanuc alarm 086 | Missing DSR jumper on the CNC end of the cable (see Cable A) |

Close picocom before starting Serial DNC, because only one program can use a port at a time.

## 6. How Docker gets access to the ports

These are the relevant lines of `docker-compose.yml`:

```yaml
    volumes:
      - /dev:/dev
      - /run/udev:/run/udev:ro
    device_cgroup_rules:
      - "c 188:* rmw"     # /dev/ttyUSB*
      - "c 166:* rmw"     # /dev/ttyACM*
```

What each part does:

- **`/dev:/dev`** makes the host's device folder visible inside the container, including
  `/dev/serial/by-id`, `/dev/serial/by-path` and any `/dev/cnc-*` names. Adapters plugged in **later**
  appear inside the running container straight away.
- **`device_cgroup_rules`**: seeing a device file is not enough, because Docker blocks opening devices by default. Each
  rule allows one class of device, identified by its **major number**:
  - `188` = USB serial adapters (`ttyUSB*`: FTDI, Prolific, CH340, …)
  - `166` = USB "modem class" serial (`ttyACM*`: some adapters and Arduino-type boards)

  Nothing else is allowed, so the container cannot open disks, consoles, etc.
- **`/run/udev`** (read-only) lets the container read device details for the port list.

Check a device's major number with `ls -l`. It's the first number before the comma:

```bash
ls -l /dev/ttyUSB0
# crw-rw---- 1 root dialout 188, 0 Oct 19 08:12 /dev/ttyUSB0
#                           ^^^ major number 188
```

If your adapter shows a different major number, add a matching rule, e.g. `"c 204:* rmw"`, then run
`docker compose up -d`. `cat /proc/devices` lists all of them.

### Built-in COM ports (`/dev/ttyS0`)

A motherboard or PCI-card COM port is `/dev/ttyS*` with **major number 4**. Major 4 also covers the
system consoles, so don't add a `c 4:*` rule. Pass in just that port, by adding to the `cnc-admin` service:

```yaml
    devices:
      - /dev/ttyS0:/dev/ttyS0
```

Built-in ports are always present, so the hot-plug problem below doesn't apply to them.

### Why not `devices:` for USB adapters, or `privileged: true`?

- `devices: - /dev/serial/by-id/...` works, but the adapter **must be plugged in when the container
  starts**. If it's unplugged and replugged later, the container keeps the old, dead device until you restart
  it, and if the adapter is missing at startup the container won't start at all. The cgroup-rule approach
  above doesn't have these problems.
- `privileged: true` gives the container full access to the host. It isn't needed and isn't recommended.

## 7. Test the port inside the container

With the stack running (`sudo docker compose up -d`):

```bash
# Does the container see the ports?
sudo docker exec cnc-admin ls -l /dev/serial/by-id/

# Full list with details, as Python sees it
sudo docker exec cnc-admin python -m serial.tools.list_ports -v
```

To test that the container can open the port and run a loopback test, first **untick "Enabled"** for that
machine in the admin page, so the port is free. Then, with pins 2–3 shorted:

```bash
sudo docker exec cnc-admin python -c "
import serial, sys
p = serial.Serial(sys.argv[1], 9600, timeout=1)
p.write(b'LOOPBACK OK'); print(p.read(11))
" /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K1234-if00-port0
```

| Result | Meaning |
|---|---|
| `b'LOOPBACK OK'` | The container can use the port |
| `b''` | The port opened but nothing came back. Check the loopback jumper. |
| `Permission denied` | Missing `device_cgroup_rules` entry for this major number (step 6) |
| `No such file or directory` | Wrong path, the adapter is unplugged, or `/dev:/dev` is missing from compose |
| `Could not exclusively lock port` | Something else has the port open: a machine still enabled in the admin page, picocom, brltty or ModemManager |

Tick **Enabled** again afterwards.

## 8. Configure the machine in the admin page

**Machines → Add machine** (or **Edit** on an existing machine). Choose a **RS-232** control type and
**USB-to-RS-232 adapter**, then:

| Field | What to enter |
|---|---|
| Serial port | Pick from the suggestions: `/dev/cnc-*`, `/dev/serial/by-id/*` or `/dev/serial/by-path/*`. Never `/dev/ttyUSBn`. |
| Baud rate | Same as the control |
| Data bits / Parity / Stop bits | Same as the control. ISO code is usually **7 data bits, even parity**. |
| Flow control | **XON/XOFF** with Cable A. **RTS/CTS** only with Cable B and hardware handshake set on the control. |
| Send delay | Seconds between the call program and the send. This is time to switch to DNC mode and press Cycle Start. |
| Line delay | Leave at 0. Raise to 5–20 ms only if the control overflows. |
| Pause timeout | 30 min default. A send paused longer than this is aborted, so a later Cycle Start can't resume an old program partway through. |

Typical starting points (confirm on the control):

| Control | Baud | Data | Parity | Stop | Flow |
|---|---|---|---|---|---|
| Fanuc 0i / 16i / 18i / 21i | 4800 or 9600 | 7 | Even | 2 | XON/XOFF |
| Haas | 9600 (up to 115200 on newer) | 7 | Even | 1 | XON/XOFF |

After saving, the machine shows **listening** on the Status page (**idle** on its own page). That means the port is open.

Then do the first end-to-end test: punch the call program `O9999 (SEND O1234)`, watch the countdown on the
machine's page, switch the control to DNC/Remote and press Cycle Start.

## 9. Adding a machine later

No container restart is needed:

1. Plug in the new adapter.
2. Run `ls -l /dev/serial/by-id/` and note the new entry. Optionally add a udev name (step 4c).
3. Test on the host with picocom (step 5). Close picocom afterwards.
4. In the admin page, choose **Add machine**, pick the port, enter the settings and save.

The only exception is an adapter with a major number not yet in `device_cgroup_rules`. Add the rule, then
run `sudo docker compose up -d`.

## 10. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Port missing from the dropdown | Is it on the host (`ls /dev/serial/by-id/`)? If not, see step 3. If it's on the host but not in the container, check that `/dev:/dev` is in the compose file. |
| Adapter appears, then vanishes after a second | brltty (step 3b) |
| **ERROR: Permission denied** | Missing cgroup rule for the device's major number (step 6) |
| **ERROR: could not exclusively lock port** | Another program has the port open: two machines with the same port, picocom, ModemManager, or another DNC program |
| **ERROR: No such file or directory** | Adapter unplugged, or by-id path changed (e.g. it was replaced with a different adapter). The server retries every 5 s and recovers by itself when the adapter returns. |
| Machine punches out but nothing arrives | TX/RX not crossed, missing ground, wrong I/O channel on the control, or wrong port selected in the UI |
| Garbled text received | Baud, data bits, parity or stop bits don't match the control |
| Machine receives garbage at plug-in or boot | ModemManager (step 3b) |
| Fanuc **alarm 086** (DR off) | DSR not driven. Add the CNC-side jumpers 6-8-20 (Cable A). |
| Fanuc **alarm 087** (buffer overflow) | Flow control mismatch: set XON/XOFF on both ends. If it persists, add a 5–20 ms line delay. |
| Send stalls at "paused by XOFF" and never resumes | The control is reset, at an M00 stop, or waiting. Reset and send the call program again, or press **Cancel send**. |
| Works for a while, then random errors | Electrical noise: use shielded cable with the shield grounded at one end, route away from drive and spindle cables, or lower the baud rate |
| Two identical adapters swap machines | Adapters without serial numbers. Use by-path or udev names (step 4b/4c). |

Useful commands:

```bash
sudo dmesg | grep -iE "tty|ftdi|pl2303|ch34" | tail     # driver messages
udevadm info /dev/ttyUSB0                              # adapter details
sudo fuser -v /dev/ttyUSB0                             # which process has the port open
sudo docker compose logs --tail 50 cnc-admin           # container log
```

**Logs → DNC activity** in the admin page shows each machine's port open/close events, requests and errors.
