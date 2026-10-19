# Connecting the CNC machines

The admin page shows a **printable setup sheet** for each machine, with its exact IP,
share name, user name, and password (Machines → *machine* → Setup sheet). Take it to
the machine. This page gives the general procedure.

> Setting numbers and menu paths are from general Haas/Siemens documentation.
> **Verify against the manual for your control and software version.**

Defaults: server (Pi) `192.168.50.1`, mask `255.255.255.0`, no gateway,
workgroup `WORKGROUP`. Always connect **by IP**, not by name.

## Haas Classic control

Networking settings on the SETNG page (around settings 900–916):

| Setting                         | Value                               |
|---------------------------------|-------------------------------------|
| CNC Network Name                | short unique name, e.g. `HAAS1`     |
| Obtain Address Automatically    | OFF                                 |
| IP Address                      | from the setup sheet (e.g. 192.168.50.11) |
| Subnet Mask                     | 255.255.255.0                       |
| Default Gateway                 | blank / 0.0.0.0                     |
| Domain / Workgroup              | `WORKGROUP`                         |
| Remote Server Name              | `192.168.50.1`                      |
| Remote Share Path               | share name, e.g. `haas1`            |
| User Name / Password            | from the setup sheet                |

Power-cycle the control. The share appears as the network device in List Program.

Troubleshooting:
- Admin page → Status: is the machine "reachable"? If not, check the cable, switch, and IP settings.
- Admin page → Logs → `samba-cnc`: look for authentication errors from the machine's IP.
- After the CNC file server restarts (adding, editing, or removing a machine), the Haas
  may need to reselect the network device or be power-cycled.

## Siemens Sinumerik 840D powerline, PCU 50 (Windows XP, HMI Advanced)

The PCU 50 has two network ports:
- **Eth 1, company network**: connect this to the CNC switch.
- **Eth 2, system network** (192.168.214.x): connects to the NCU and the machine
  control panel. **Do not change it.**

1. Exit to Windows with the service/OEM login.
2. Control Panel → Network Connections → **Eth 1** → TCP/IP properties:
   IP from the setup sheet (e.g. `192.168.50.13`), mask `255.255.255.0`, no gateway, no DNS.
3. In Explorer, choose Tools → Map Network Drive: drive `N:`, folder `\\192.168.50.1\siemens1`,
   tick **Reconnect at logon**, choose "Connect using a different user name", and enter
   `siemens1` with the password from the setup sheet.
4. Make the drive visible in HMI Advanced (Services area). The exact steps depend on
   the HMI version, so check the HMI Advanced commissioning manual. Restart the HMI.

Windows XP uses SMB1 with NTLMv1 by default, which the CNC side accepts. No registry
changes should be needed.

## Other control types

The admin page has setup instructions for every supported control type
(Machines → *machine* → Setup sheet). In summary:

### Network share (SMB)
Haas NGC (Settings → Network / Net Share), Siemens 840D sl / 828D (Commissioning → HMI →
Logical drives), Heidenhain TNC (Program Management → Net → mount SMB), and Windows-based
controls (map a network drive in Windows). Server `192.168.50.1`, share = machine ID.

### FTP: FANUC with Data Server, FANUC embedded Ethernet, Mitsubishi, Brother
The machine is the FTP **client**; the Pi is the FTP host.

| FANUC screen / parameter            | Value                                     |
|-------------------------------------|-------------------------------------------|
| ETHPRM → COMMON: IP / mask / router | machine IP, 255.255.255.0, blank          |
| FTP TRANSFER (host 1): host / port  | `192.168.50.1` / `21`                     |
| User / password / login folder      | machine ID / from setup sheet / blank     |
| Parameter 20 (I/O channel)          | `5` = Data Server, `9` = embedded Ethernet |
| Data Server mode                    | FTP mode (to work directly on the Pi's folder) |

Screen names differ between 0i-D/F and 30i/31i/32i. Check the FANUC Ethernet
or Data Server manual. Active and passive FTP both work.

### FOCAS: FANUC with Ethernet (Pi pushes and pulls programs)
Instead of the operator fetching files, the Pi writes and reads programs directly in CNC memory.
On the control: SYSTEM → ETHPRM → embedded port → COMMON (IP, mask, no router) and the **FOCAS2** screen
(TCP port 8193). Turn the program-protect key off to allow writing. O8000–O9999 may be protected by
parameter 3202. The machine's admin page shows the CNC status and its program directory, with
*Send to CNC*, *Read*, and *Delete* buttons. The active program can't be overwritten or deleted.

The FOCAS library is FANUC's software; see `focas/lib/README.md` about licensing.

### RS-232 serial DNC
For machines with no Ethernet port. The engine comes from serial-dnc: the Pi listens on each serial port all the time.
**[SERIAL-PORTS.md](SERIAL-PORTS.md)** covers adapters, null-modem wiring (including the jumpers that avoid
FANUC alarm 086), stable port names, and testing.

Connect each machine to a USB-to-RS-232 adapter on the Pi (FTDI chipset recommended) or to a serial device
server on the CNC switch (Moxa NPort in TCP Server mode, with its serial settings matching the control).

**Calling a program from the machine:** create a small program with the request comment and punch it out:

```
%
O9999 (SEND O1234)
M30
%
```

`SEND` accepts an exact file name, a name without extension, or a program number. Within the machine's
send delay, switch the control to DNC/Remote/Tape mode and press Cycle Start. The Pi streams the file as it runs.

- **Saving to the Pi:** punch out any other program. It's saved to `<machine>/received/O<number>_<date-time>.nc`.
- **Abort:** reset the control and punch the call program again, or press *Cancel send* on the machine's page.
- **Manual send:** each program on the machine's page has a *Send* button with its own delay.

Fanuc (0i/16i/18i/21i/30i): 0020 = I/O channel (0); 0101 bit 0 = 1 (2 stop bits); 0102 = 0; 0103 = 10/11/12
(4800/9600/19200 baud). ISO code, 7 data bits, even parity, XON/XOFF.
Haas: settings 11 (baud), 12 (parity), 13 (stop bits), 14 = XON/XOFF, 37 (data bits), 55 (DNC from MDI) = ON.

## File names

Keep names short and plain for the older controls:
- Haas: `O` plus 5 digits, e.g. `O01234.NC`
- Siemens: `PARTNAME.MPF` (main program) and `SUBNAME.SPF` (subroutine), upper case
