# FANUC FOCAS library

The FOCAS bridge needs FANUC's FOCAS2 library for 32-bit ARM Linux (`libfwlib32`).

- **Preferred:** use a copy licensed from FANUC (the FOCAS2 library / developer kit). Put the
  32-bit ARM Linux build here as `libfwlib32.so` and rebuild: `docker compose build focas-bridge`.
- **Otherwise** the build downloads `libfwlib32-linux-armv7.so.1.0.5` from
  github.com/Ladder99/fanuc-driver at a pinned commit and checks its SHA-256.

The library is FANUC's proprietary software. The Apache 2.0 license of fanuc-driver covers
Ladder99's own code, not necessarily the FANUC binaries it redistributes. Confirm that your
use is licensed (check with FANUC or your FANUC distributor) before running it in production.

`libfwlib32.so` placed here is ignored by git.
