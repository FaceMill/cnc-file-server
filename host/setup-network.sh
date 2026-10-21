#!/bin/bash
# Prepares NetworkManager connections (Raspberry Pi OS Bookworm or Trixie) with the names
# cnc-admin manages:
#   office-net  OFFICE_IF (eth0, or wlan0 on a Wi-Fi test bench); the active connection is
#               renamed, so its DHCP/static settings (and Wi-Fi password) are kept.
#               OFFICE_MODE=hotspot: left to host/setup-hotspot.sh
#   cnc-net     the CNC network, static, no gateway:
#               one port   (CNC_IF=eth1):        on that port
#               several    (CNC_IF="eth1 eth2"): bridge br-cnc over all of them, so the Pi
#                                                acts as the switch (port order doesn't matter)
# Afterwards, change addresses in the admin web page, not here. Safe to run again.
set -euo pipefail

OFFICE_IF=${OFFICE_IF:-eth0}
CNC_IF=${CNC_IF:-eth1}
CNC_ADDR=${CNC_ADDR:-192.168.50.1/24}
read -r -a CNC_PORTS <<<"$CNC_IF"
[ ${#CNC_PORTS[@]} -gt 0 ] || { echo "CNC_IF is empty." >&2; exit 1; }
for p in "$OFFICE_IF" "${CNC_PORTS[@]}"; do
    [[ $p =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || { echo "Not an interface name: '$p'" >&2; exit 1; }
done
for p in "${CNC_PORTS[@]}"; do
    [ "$p" != "$OFFICE_IF" ] || { echo "$p can't be both the office and a CNC port." >&2; exit 1; }
done

# Office side. OFFICE_MODE=hotspot: host/setup-hotspot.sh makes OFFICE_IF the access point
# at the end of the install (the Wi-Fi is still needed for downloads until then).
if [ "${OFFICE_MODE:-client}" != hotspot ]; then
    # Rename the connection currently active on the office port (no reconnect, safe over SSH).
    current=$(nmcli -g GENERAL.CONNECTION device show "$OFFICE_IF" 2>/dev/null || true)
    if [ -n "$current" ] && [ "$current" != office-net ]; then
        nmcli connection modify "$current" connection.id office-net
    fi
    [ "$(nmcli -g GENERAL.CONNECTION device show "$OFFICE_IF" 2>/dev/null || true)" = office-net ] || {
        echo "No active connection on $OFFICE_IF; connect it to the office network first (check OFFICE_IF)." >&2; exit 1; }
fi

# CNC ports: NetworkManager must never set them up by itself (it creates a DHCP "Wired
# connection N" for any port without a profile, e.g. while ours are being replaced).
mkdir -p /etc/NetworkManager/conf.d
specs=$(printf 'interface-name:%s,' "${CNC_PORTS[@]}")
printf '# Written by host/setup-network.sh: CNC ports are configured only as cnc-net.\n[main]\nno-auto-default+=%s\n' \
    "${specs%,}" > /etc/NetworkManager/conf.d/90-cnc-ports.conf
nmcli general reload conf

# Remove what it already set up on them, and our own earlier profiles; then create ours,
# with a higher priority than anything else that might match.
for p in "${CNC_PORTS[@]}"; do
    active=$(nmcli -g GENERAL.CONNECTION device show "$p" 2>/dev/null || true)
    if [ -n "$active" ] && [ "$active" != office-net ]; then
        nmcli connection delete "$active" >/dev/null 2>&1 || true
    fi
done
nmcli -g NAME connection show | while IFS= read -r c; do
    case "$c" in cnc-net|cnc-port-*) nmcli connection delete "$c" >/dev/null ;; esac
done

CNC_IP_SETTINGS=(ipv4.method manual ipv4.addresses "$CNC_ADDR" ipv4.never-default yes
                 ipv4.ignore-auto-dns yes ipv6.method disabled
                 connection.autoconnect yes connection.autoconnect-priority 100)
if [ ${#CNC_PORTS[@]} -eq 1 ]; then
    nmcli connection add type ethernet ifname "${CNC_PORTS[0]}" con-name cnc-net "${CNC_IP_SETTINGS[@]}" >/dev/null
else
    # No spanning tree: nothing else bridges this network, and STP would hold ports
    # closed for ~30 s after a cable is plugged in.
    nmcli connection add type bridge ifname br-cnc con-name cnc-net bridge.stp no "${CNC_IP_SETTINGS[@]}" >/dev/null
    for p in "${CNC_PORTS[@]}"; do
        nmcli connection add type ethernet ifname "$p" con-name "cnc-port-$p" master br-cnc slave-type bridge \
            connection.autoconnect yes connection.autoconnect-priority 100 >/dev/null
    done
fi
nmcli connection up cnc-net >/dev/null || echo "cnc-net will come up when a CNC port has a link."
for p in "${CNC_PORTS[@]}"; do
    [ ${#CNC_PORTS[@]} -eq 1 ] || nmcli connection up "cnc-port-$p" >/dev/null 2>&1 \
        || echo "cnc-port-$p will come up when $p is plugged in."
done

# Never route between office and CNC networks.
cat > /etc/sysctl.d/90-cnc-no-forward.conf <<'EOF'
net.ipv4.ip_forward = 0
net.ipv6.conf.all.forwarding = 0
# Drop packets whose source address doesn't belong on the interface they arrived on.
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
EOF
sysctl --system >/dev/null

nmcli -f NAME,DEVICE,STATE connection show
