# Tests

Everything runs in Docker (Docker Desktop on a Mac is fine). Nothing touches the real
network or firewall: the admin app runs in dry-run mode, and the full-stack test builds
its own simulated Pi.

```bash
tests/run_all.sh               # static checks, all suites, fuzzing, browser (~15 min)
tests/run_all.sh --fullstack   # ... plus the simulated-Pi network test (~5 more min)
tests/fullstack.sh             # only the simulated Pi; leaves it running to poke at
tests/fullstack.sh --clean     # remove the simulated Pi
```

| File | What it tests |
|---|---|
| `fullstack.sh` (+ `fs_admin.py`) | Docker-in-Docker "Pi" with eth0 on an office network and eth1 on a CNC network, the real compose stack and generated firewall, driven from simulated office PCs, CNC machines and a rogue device |
| `test_web.py` | Admin web app: login, CSRF, machines, users, validation, network pages, office change with rollback |
| `test_protocols.py` | Every control type: setup sheet, generated Samba/FTP/firewall config, validation |
| `test_serial_dnc.py` | Serial DNC against a simulated FANUC on a TCP serial device server |
| `test_serial_tty.py` | Serial DNC over a real tty (the USB adapter code path), 1 MB drip-feed, concurrency |
| `test_focas_admin.py` | FOCAS pages and actions against a simulated bridge |
| `test_focas_bridge.py` | The 32-bit FOCAS bridge with FANUC's real library |
| `test_review_fixes.py` | One regression check per fix from the security/correctness review |
| `fuzz_render.py` | Random and hostile configs; accepted ones must pass `nft -c` and `testparm` |
| `test_browser.py` | Chromium (Playwright): pages, JavaScript, upload/delete |
| `demo/` | Demo site seed plus simulated FANUC (serial) and FOCAS bridge |

Not covered (needs real hardware): NetworkManager changes on the Pi, the Pi 5's 32-bit
container support for the FOCAS library, real CNC controls, real USB serial adapters.
