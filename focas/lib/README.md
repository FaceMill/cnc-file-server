# FANUC FOCAS library

The FOCAS bridge needs FANUC's FOCAS2 library for Linux (`libfwlib32`). FANUC builds it for
32-bit ARM, 32-bit x86 and x86-64, but **not 64-bit ARM**. The image is built for whichever
of these suits the host (`FOCAS_PLATFORM` in `.env`, set by `host/install.sh`):

| Host | Platform | Library |
|---|---|---|
| Raspberry Pi (64-bit OS) | `linux/arm/v7` (32-bit container, runs natively on a Pi) | `libfwlib32-linux-armv7.so.1.0.5` |
| x86-64 PC or VM | `linux/amd64` | `libfwlib32-linux-x64.so.1.0.5` |
| 32-bit x86 | `linux/386` | `libfwlib32-linux-x86.so.1.0.5` |

- **Preferred:** use a copy licensed from FANUC (the FOCAS2 library / developer kit). Put the
  build for your platform here as `libfwlib32.so` and rebuild: `sudo ./host/build-images.sh focas && sudo docker compose up -d focas-bridge`.
  The build stops if the file is for a different CPU.
- **Otherwise** the build downloads the matching file from github.com/Ladder99/fanuc-driver at
  a pinned commit and checks its SHA-256.

`host/install.sh` checks that the library loads after building. A 32-bit ARM container needs a
kernel with 32-bit support: a Pi has it, some other 64-bit ARM servers don't.

The library is FANUC's proprietary software. The Apache 2.0 license of fanuc-driver covers
Ladder99's own code, not necessarily the FANUC binaries it redistributes. Confirm that your
use is licensed (check with FANUC or your FANUC distributor) before running it in production.

`libfwlib32.so` placed here is ignored by git.
