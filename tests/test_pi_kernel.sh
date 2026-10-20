#!/bin/bash
# shellcheck disable=SC2034  # variables are used inside the eval'd check expressions
# host/pi-kernel.sh: switches a 16K-page Pi to the 4K-page kernel once, keeps a backup,
# asks for a reboot, and leaves 4K-page systems alone.
set -u
K=${K:-/project/host/pi-kernel.sh}
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
C=/tmp/config.txt
printf '[all]\ndtparam=audio=on\n[pi5]\n#kernel=kernel8.img\n' > $C; orig=$(cat $C)

CONFIG_TXT=$C PAGESIZE_OVERRIDE=4096 bash "$K" >/dev/null 2>&1; rc=$?
check "4K pages: nothing to do (exit 0), config.txt untouched" '[ $rc -eq 0 ] && [ "$(cat $C)" = "$orig" ] && [ ! -e $C.before-cnc-server ]'

out=$(CONFIG_TXT=$C PAGESIZE_OVERRIDE=16384 bash "$K" 2>&1); rc=$?
check "16K pages: switched, reboot requested (exit 2)" '[ $rc -eq 2 ] && grep -q "Reboot" <<<"$out"'
check "kernel=kernel8.img added under [all] (a commented line or [pi5] section doesn't count)" \
      '[ "$(tail -2 $C)" = "$(printf "[all]\nkernel=kernel8.img")" ]'
check "backup of the original kept" '[ "$(cat $C.before-cnc-server)" = "$orig" ]'

out=$(CONFIG_TXT=$C PAGESIZE_OVERRIDE=16384 bash "$K" 2>&1); rc=$?
check "run again before rebooting: asks for the reboot, adds nothing" \
      '[ $rc -eq 2 ] && grep -q "isn.t running yet" <<<"$out" && [ "$(grep -c "^kernel=kernel8.img" $C)" = 1 ]'

CONFIG_TXT=$C PAGESIZE_OVERRIDE=4096 bash "$K" >/dev/null 2>&1; rc=$?
check "after the reboot (4K pages): carries on" '[ $rc -eq 0 ]'

CONFIG_TXT=/tmp/nope.txt PAGESIZE_OVERRIDE=16384 bash "$K" >/dev/null 2>&1; rc=$?
check "no config.txt (not a Pi): stops with an explanation (exit 1)" '[ $rc -eq 1 ]'

[ $fails -eq 0 ] && echo "ALL PASSED" || { echo "$fails FAILED"; exit 1; }
