#!/bin/bash
# Prepares NetworkManager connections on Raspberry Pi OS Bookworm with the names
# cnc-admin manages:
#   office-net  eth0, DHCP (ask IT for a reservation); existing connection is renamed
#   cnc-net     eth1 (USB adapter), static, no gateway
# Afterwards, change addresses in the admin web page, not here.
set -euo pipefail

OFFICE_IF=${OFFICE_IF:-eth0}
CNC_IF=${CNC_IF:-eth1}
CNC_ADDR=${CNC_ADDR:-192.168.50.1/24}

# Rename the connection currently active on eth0 (no reconnect, safe over SSH).
current=$(nmcli -g GENERAL.CONNECTION device show "$OFFICE_IF")
if [ -n "$current" ] && [ "$current" != office-net ]; then
    nmcli connection modify "$current" connection.id office-net
fi
nmcli connection show office-net >/dev/null || {
    echo "No active connection on $OFFICE_IF; plug it into the office network first." >&2; exit 1; }

nmcli connection delete cnc-net 2>/dev/null || true
nmcli connection add type ethernet ifname "$CNC_IF" con-name cnc-net \
    ipv4.method manual ipv4.addresses "$CNC_ADDR" \
    ipv4.never-default yes ipv4.ignore-auto-dns yes \
    ipv6.method disabled \
    connection.autoconnect yes
nmcli connection up cnc-net || echo "cnc-net will come up when $CNC_IF has a link."

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
