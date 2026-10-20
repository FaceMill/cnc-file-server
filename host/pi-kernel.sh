#!/bin/bash
# The FOCAS bridge runs FANUC's 32-bit ARM library (FANUC has no 64-bit ARM build). A Pi 5's
# default kernel uses 16K memory pages and can't run 32-bit ARM programs; Raspberry Pi's
# standard 4K-page kernel (kernel8.img) can. Called by host/install.sh on ARM hosts.
#
# Exit 0: 4K pages, nothing to do.
# Exit 2: the 4K-page kernel is set in config.txt (now or earlier); reboot, then re-run.
# Exit 1: can't fix it here.
set -euo pipefail

CONFIG_TXT=${CONFIG_TXT:-/boot/firmware/config.txt}
page=${PAGESIZE_OVERRIDE:-$(getconf PAGESIZE)}
[ "$page" = 4096 ] && exit 0

if grep -qE '^[[:space:]]*kernel=kernel8\.img[[:space:]]*$' "$CONFIG_TXT" 2>/dev/null; then
    echo "The 4K-page kernel is set in $CONFIG_TXT but isn't running yet." >&2
    echo "Reboot (sudo reboot), then run the installer again." >&2
    exit 2
fi
if [ ! -f "$CONFIG_TXT" ]; then
    echo "This kernel uses $page-byte memory pages, and FANUC's 32-bit ARM FOCAS library needs 4096." >&2
    echo "$CONFIG_TXT doesn't exist, so it can't be switched here; boot a 4K-page kernel." >&2
    exit 1
fi

cp "$CONFIG_TXT" "$CONFIG_TXT.before-cnc-server"
printf '\n# cnc-server: 4K-page kernel, needed by FANUC'\''s 32-bit ARM FOCAS library\n[all]\nkernel=kernel8.img\n' >> "$CONFIG_TXT"
echo "This Pi runs a kernel with $page-byte memory pages, which can't run FANUC's 32-bit ARM" >&2
echo "FOCAS library. Switched to Raspberry Pi's standard 4K-page kernel in $CONFIG_TXT" >&2
echo "(backup: $CONFIG_TXT.before-cnc-server)." >&2
echo "Reboot (sudo reboot), then run the installer again." >&2
exit 2
