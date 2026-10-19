"""Create the demo site: one machine of each kind, sample programs, admin password
'demo-password'. Runs inside the cnc-admin image with CNC_ADMIN_DRY_RUN=1."""
from werkzeug.security import generate_password_hash

from cncadmin import config, system
from cncadmin.ntlm import nt_hash

site = config.default_site("10.20.0.0/16", "eth9", "eth8")
site["machines"][0]["label"] = "Haas VF-2 (Bay 1)"
site["machines"][1]["label"] = "Haas VF-4 (Bay 2)"
site["machines"][2]["label"] = "Siemens 840D Mill"
site["machines"] += [
    {"name": "fanuc1", "label": "Mill FANUC 0i-MF (Data Server)", "type": "fanuc-dataserver",
     "ip": "192.168.50.21", "password": config.gen_password()},
    {"name": "fanuc2", "label": "Lathe FANUC 31i (embedded Ethernet)", "type": "fanuc-embedded",
     "ip": "192.168.50.22", "password": config.gen_password()},
    {"name": "fanuc3", "label": "Old Mill FANUC 0-M (RS-232 via Moxa)", "type": "fanuc-rs232", "ip": "192.168.50.40",
     "password": "", "serial": {**config.SERIAL_DEFAULTS, "transport": "tcp", "send_delay": 3.0}},
    {"name": "fadal1", "label": "Fadal VMC (RS-232 USB adapter)", "type": "serial-other", "ip": "", "password": "",
     "serial": {**config.SERIAL_DEFAULTS, "device": "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K3XYZ-if00-port0"}},
    {"name": "fanuc5", "label": "Mill FANUC 0i-MF (FOCAS, simulated)", "type": "fanuc-focas", "ip": "192.168.50.31",
     "password": "", "focas": dict(config.FOCAS_DEFAULTS)},
]
site["office_users"] = [{"name": n, "nt_hash": nt_hash("Office-pass-1")} for n in ("programmer", "jsmith")]
config.save(site)
config.set_admin_password_hash(generate_password_hash("demo-password"))
system.apply_site(site)

prog = ("%\r\nO0100(DEMO PART - POCKET)\r\nG90G54G17G40G80\r\nT1M06\r\nS2500M03\r\n"
        + "".join(f"N{i}G01X{i * 0.5:.3f}Y{(i % 20) * 0.75:.3f}F300.\r\n" for i in range(1, 160)) + "M30\r\n%\r\n")
files = {"fanuc3/O0100.NC": prog, "fadal1/O0200.NC": "%\r\nO0200\r\nG00X0Y0\r\nM30\r\n%\r\n",
         "fanuc1/O1000.NC": "%\r\nO1000\r\nG00X0Y0\r\nM30\r\n%\r\n",
         "fanuc5/O2050.NC": "%\r\nO2050(BRACKET OP1 REV B)\r\nG90G54G00X0Y0\r\nS2200M03\r\nM30\r\n%\r\n",
         "fanuc5/O3000.NC": "%\r\nO3000(NEW PART)\r\nG90G54\r\nM30\r\n%\r\n"}
for rel, text in files.items():
    system.write_shared_file(f"{system.SRV_DIR}/{rel}", text.encode())
print("demo site created")
