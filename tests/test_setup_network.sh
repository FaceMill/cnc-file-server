#!/bin/bash
# shellcheck disable=SC2034  # variables are used inside the eval'd check expressions
# host/setup-network.sh against real NetworkManager (run in a privileged Debian container:
# tests/run_all.sh does this for Bookworm and Trixie). Virtual cables (veth) stand in for
# the ports: with udev running, NetworkManager treats them like freshly plugged-in USB
# adapters, including its own automatic DHCP "Wired connection" on each.
#   wlan0  office (an existing, active connection as Raspberry Pi Imager would leave it)
#   eth1, eth2  CNC ports
set -u
SETUP=${SETUP:-/project/host/setup-network.sh}
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

apt-get -qq update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y network-manager dbus iproute2 udev procps >/dev/null 2>&1
mkdir -p /run/dbus /etc/NetworkManager/conf.d
# Leave the container's own network (eth0) and the far ends of the cables alone.
# The office stand-in is a wired veth; a real Wi-Fi port never gets an automatic wired connection.
printf '[keyfile]\nunmanaged-devices=interface-name:eth0;interface-name:peer*\n[main]\nno-auto-default+=interface-name:wlan0\n' \
    > /etc/NetworkManager/conf.d/test.conf
dbus-daemon --system --fork
# NetworkManager's own udev rule leaves veths unmanaged (they usually belong to containers);
# real Wi-Fi and USB adapters are managed, so treat the stand-ins the same way.
mkdir -p /etc/udev/rules.d
echo 'SUBSYSTEM=="net", ENV{INTERFACE}=="wlan0|eth1|eth2", ENV{NM_UNMANAGED}="0"' > /etc/udev/rules.d/99-test-ports.rules
/usr/lib/systemd/systemd-udevd --daemon 2>/dev/null || /lib/systemd/systemd-udevd --daemon
for p in wlan0 eth1 eth2; do ip link add "$p" type veth peer name "peer$p"; ip link set "peer$p" up; done
udevadm trigger --subsystem-match=net --action=add; udevadm settle
start_nm() { NetworkManager --no-daemon >>/tmp/nm.log 2>&1 & sleep 5; }
start_nm
nmcli connection add type ethernet ifname wlan0 con-name "preconfigured" ipv4.method manual \
    ipv4.addresses 10.20.0.10/16 ipv4.gateway 10.20.0.1 >/dev/null
nmcli connection up preconfigured >/dev/null
sleep 2
echo "NetworkManager $(nmcli --version | awk '{print $NF}')"

addr() { ip -4 -br addr show "$1" 2>/dev/null | awk '{print $3}'; }
master() { ip -o link show "$1" | grep -o 'master [^ ]*' | cut -d' ' -f2; }
profiles() { nmcli -g NAME connection show | grep -vx lo | sort | tr '\n' ' '; }  # lo: NetworkManager >= 1.42's own

# --- one CNC port ---------------------------------------------------------------
auto_eth1=$(nmcli -g GENERAL.CONNECTION device show eth1)
check "NetworkManager started its own DHCP connection on the new ports" '[ -n "$auto_eth1" ]'
OFFICE_IF=wlan0 CNC_IF=eth1 bash "$SETUP" >/tmp/run1.log 2>&1; rc=$?
sleep 3
check "single port: script succeeds" '[ $rc -eq 0 ]'
check "office connection renamed office-net, still up with its address" \
      '[ "$(nmcli -g GENERAL.CONNECTION device show wlan0)" = office-net ] && [ "$(addr wlan0)" = 10.20.0.10/16 ]'
check "cnc-net on eth1 with 192.168.50.1/24" '[ "$(nmcli -g GENERAL.CONNECTION device show eth1)" = cnc-net ] && [ "$(addr eth1)" = 192.168.50.1/24 ]'
check "cnc-net has no gateway and outranks automatic connections" \
      '[ "$(nmcli -g ipv4.never-default,connection.autoconnect-priority connection show cnc-net)" = "$(printf "yes\n100")" ]'
check "NetworkManager's automatic connection on eth1 removed" '! nmcli -g NAME connection show | grep -qx "$auto_eth1"'
check "NetworkManager told never to auto-configure the CNC port" 'grep -qx "no-auto-default+=interface-name:eth1" /etc/NetworkManager/conf.d/90-cnc-ports.conf'
check "forwarding off" '[ "$(sysctl -n net.ipv4.ip_forward)" = 0 ]'

# --- two CNC ports, bridged ------------------------------------------------------------
OFFICE_IF=wlan0 CNC_IF="eth1 eth2" bash "$SETUP" >/tmp/run2.log 2>&1; rc=$?
sleep 4
check "bridge: script succeeds" '[ $rc -eq 0 ]'
check "br-cnc has 192.168.50.1/24" '[ "$(addr br-cnc)" = 192.168.50.1/24 ]'
check "eth1 and eth2 are ports of br-cnc, without addresses of their own" \
      '[ "$(master eth1)" = br-cnc ] && [ "$(master eth2)" = br-cnc ] && [ -z "$(addr eth1)" ] && [ -z "$(addr eth2)" ]'
check "spanning tree off (ports forward at once)" '[ "$(cat /sys/class/net/br-cnc/bridge/stp_state)" = 0 ]'
check "profiles: office-net, cnc-net, one per port; nothing automatic left" \
      '[ "$(profiles)" = "cnc-net cnc-port-eth1 cnc-port-eth2 office-net " ]'
check "office untouched" '[ "$(addr wlan0)" = 10.20.0.10/16 ]'

OFFICE_IF=wlan0 CNC_IF="eth1 eth2" bash "$SETUP" >/tmp/run3.log 2>&1; rc=$?
sleep 4
check "running it again gives the same result" \
      '[ $rc -eq 0 ] && [ "$(profiles)" = "cnc-net cnc-port-eth1 cnc-port-eth2 office-net " ] && [ "$(master eth2)" = br-cnc ] && [ "$(addr br-cnc)" = 192.168.50.1/24 ]'

check "both bridge ports excluded from auto-configuration" 'grep -qx "no-auto-default+=interface-name:eth1,interface-name:eth2" /etc/NetworkManager/conf.d/90-cnc-ports.conf'
ip link set eth2 down; sleep 2; ip link set eth2 up; sleep 5
check "unplugging and replugging a port: it rejoins the bridge, no automatic connection" \
      '[ "$(master eth2)" = br-cnc ] && [ "$(profiles)" = "cnc-net cnc-port-eth1 cnc-port-eth2 office-net " ]'

# --- what the admin page does to change the CNC address (system.nm_set_cnc) -------------
nmcli connection modify cnc-net ipv4.addresses 192.168.60.1/24 && nmcli connection up cnc-net >/dev/null
sleep 4
check "admin page CNC address change applies to the bridge; ports stay attached" \
      '[ "$(addr br-cnc)" = 192.168.60.1/24 ] && [ "$(master eth1)" = br-cnc ] && [ "$(master eth2)" = br-cnc ]'

# --- reboot: NetworkManager starts again from its saved profiles -------------------------
# As after a power cycle: no bridge, ports and office down without addresses, /run emptied.
pkill NetworkManager; sleep 2
ip link del br-cnc 2>/dev/null
for p in wlan0 eth1 eth2; do ip addr flush dev "$p"; ip link set "$p" down; ip link set "$p" up; done
rm -rf /run/NetworkManager
start_nm; sleep 6
check "after a restart: bridge, ports and address come back" \
      '[ "$(addr br-cnc)" = 192.168.60.1/24 ] && [ "$(master eth1)" = br-cnc ] && [ "$(master eth2)" = br-cnc ]'
check "after a restart: office back, no automatic connections recreated" \
      '[ "$(addr wlan0)" = 10.20.0.10/16 ] && ! profiles | grep -q "Wired connection"'

# --- setup hotspot (OFFICE_MODE=hotspot) -------------------------------------------------
# A container has no Wi-Fi radio: the profiles are real, activating the access point isn't
# tested here (HOTSPOT_TEST_ANY_DEVICE / HOTSPOT_TEST_NO_UP).
HOTSPOT=${HOTSPOT:-/project/host/setup-hotspot.sh}
H() { OFFICE_IF=wlan0 HOTSPOT_SSID=cnc-setup-test HOTSPOT_PASSWORD="$1" HOTSPOT_TEST_NO_UP=1 HOTSPOT_TEST_ANY_DEVICE="${2:-1}" bash "$HOTSPOT" "$3"; }
check "hotspot refuses a port that isn't Wi-Fi" '! H Secret-pass-1 0 on >/dev/null 2>&1'
check "hotspot refuses a password under 8 characters" '! H short 1 on >/dev/null 2>&1'
H Secret-pass-1 1 on >/tmp/hs-on.log 2>&1; rc=$?
check "hotspot on: succeeds" '[ $rc -eq 0 ] && grep -q "Wi-Fi network:  cnc-setup-test" /tmp/hs-on.log'
check "office-net is now an access point: SSID, WPA2, shared (DHCP), 10.42.0.1/24, priority 100" \
      '[ "$(nmcli -g 802-11-wireless.mode,802-11-wireless.ssid,802-11-wireless-security.key-mgmt,ipv4.method,ipv4.addresses,connection.autoconnect-priority connection show office-net)" = "$(printf "ap\ncnc-setup-test\nwpa-psk\nshared\n10.42.0.1/24\n100")" ]'
check "hotspot password stored in its profile" '[ "$(nmcli -s -g 802-11-wireless-security.psk connection show office-net)" = Secret-pass-1 ]'
check "previous Wi-Fi kept as office-net-client, not auto-connecting, settings intact" \
      '[ "$(nmcli -g connection.autoconnect,ipv4.addresses connection show office-net-client)" = "$(printf "no\n10.20.0.10/16")" ]'
check "hotspot status reports on" 'H Secret-pass-1 1 status | grep -q "Setup hotspot: on (cnc-setup-test"'
OFFICE_IF=wlan0 OFFICE_MODE=hotspot CNC_IF="eth1 eth2" bash "$SETUP" >/tmp/run-hs.log 2>&1; rc=$?
check "network setup in hotspot mode leaves the office side to the hotspot" \
      '[ $rc -eq 0 ] && nmcli connection show office-net-client >/dev/null && [ "$(nmcli -g 802-11-wireless.mode connection show office-net)" = ap ] && [ "$(master eth1)" = br-cnc ]'
H Secret-pass-1 1 on >/dev/null 2>&1
check "hotspot on again: no duplicates, client copy kept" '[ "$(nmcli -g NAME connection show | grep -c "^office-net")" = 2 ]'
H Secret-pass-1 1 off >/tmp/hs-off.log 2>&1; rc=$?
check "hotspot off: the previous Wi-Fi is office-net again, auto-connecting, settings intact" \
      '[ $rc -eq 0 ] && [ "$(nmcli -g 802-11-wireless.mode connection show office-net 2>/dev/null)" != ap ] && [ "$(nmcli -g connection.autoconnect,ipv4.addresses connection show office-net)" = "$(printf "yes\n10.20.0.10/16")" ] && ! nmcli connection show office-net-client >/dev/null 2>&1'
check "hotspot off when it's already off: harmless" 'H Secret-pass-1 1 off >/dev/null 2>&1 && nmcli connection show office-net >/dev/null'
check "hotspot status reports off" 'H Secret-pass-1 1 status | grep -q "Setup hotspot: off"'

# --- bad input ----------------------------------------------------------------------
check "refuses a CNC port that is the office port" '! OFFICE_IF=wlan0 CNC_IF="eth1 wlan0" bash "$SETUP" >/dev/null 2>&1'
check "refuses an interface name with shell characters" '! OFFICE_IF=wlan0 CNC_IF="eth1;reboot" bash "$SETUP" >/dev/null 2>&1'
check "refuses an office port with no connection" '! OFFICE_IF=eth9 CNC_IF=eth1 bash "$SETUP" >/dev/null 2>&1'

[ $fails -eq 0 ] && echo "ALL PASSED" || { echo "$fails FAILED"; tail -20 /tmp/run2.log; exit 1; }
