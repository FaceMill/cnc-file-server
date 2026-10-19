# CNC File Server: shop-floor runbook

A one-page guide for everyday problems. Print it and keep it near the machines.

**Admin page:** `https://<server-name>:8443`. The person who looks after the server has the
login. **Office share:** `\\<server-name>\CNC`

---

### Where are the programs?
- On office PCs: `\\<server-name>\CNC\<machine>\`, one folder per machine.
- Programs a serial machine punched out are in `<machine>\received\`.
- **Deleted a file by mistake?** It's in the hidden `.recycle` folder for 60 days.
  - In Windows Explorer, turn on *View → Show → Hidden items*.
  - Then look in `\\<server-name>\CNC\.recycle\`, which holds files deleted from the office.
  - Or look in `\\<server-name>\CNC\<machine>\.recycle\`, which holds files deleted at the machine or on the admin page.
  - Copy the file back to where it was.
  - Older versions are in the nightly backup. The administrator can restore a single file.

### A machine can't see its programs
1. Is the machine on? Is its network cable plugged into the **CNC switch**?
2. Open the admin page, then **Status**. Find the machine:
   - **no reply**: a cable, switch, or IP setting problem at the machine.
   - **reachable** but not connected: the network-drive settings on the control.
     Compare them with the machine's **setup sheet** (Machines → machine → Print setup sheet).
3. Haas Classic: after the server restarts, re-select the network device, or power-cycle the control.

### Serial (RS-232) machine
- To **load a program**, punch the call program from the control:
  ```
  %
  O9999 (SEND O1234)
  M30
  %
  ```
  Then switch to DNC / Remote / Tape and press **Cycle Start** before the countdown ends.
  The machine's page shows the countdown.
- To **save a program to the server**, just punch it out.
- **Stuck** ("paused by XOFF" forever): reset the control and call the program again,
  or press **Cancel send** on the machine's page.
- **Alarm 086** on a FANUC: cable or handshake jumpers. **Alarm 087**: flow control.
  The administrator should check `docs/SERIAL-PORTS.md`.

### The Status page shows a warning
| Message | What to do |
|---|---|
| Service … is stopped | Restart the server (below). If it comes back, tell the administrator. |
| Program storage low / almost full | Empty the recycle folders (Settings), delete old programs, tell the administrator. |
| Last backup FAILED / backups not set up | Tell the administrator the same day. |
| Pi is overheating / throttling | Check that the cooler fan spins and the vents aren't blocked. |
| Under-voltage | The power supply is failing: use the official 27 W supply. |
| SSD health / wear | Tell the administrator: the SSD needs replacing soon. A backup exists. |
| Power cut: running on UPS battery | The server shuts down on its own before the battery runs out. |
| Clock not synchronised | File times may be wrong; the administrator checks the time server. |

### Restart the server
1. Best: admin page → *Cockpit* (`https://<server-name>:9090`) → **Reboot**.
2. Or, if nothing responds, unplug the power, wait 10 seconds, and plug it back in.
   Avoid doing this while a machine is saving.

It takes about 2 minutes to come back. The machines reconnect by themselves; a Haas Classic
may need its network device re-selected.

### Don't
- Don't plug the CNC switch into the office network, or the office network into the CNC switch.
- Don't connect anything else to the CNC switch (laptops, Wi-Fi access points).
- Don't change network settings on the admin page without the administrator.

### Who to call
| | Name | Phone |
|---|---|---|
| Server administrator | | |
| IT help desk (network) | | |
| Machine service (Haas / FANUC / Siemens) | | |
