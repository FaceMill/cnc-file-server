#!/bin/bash
# Setup hotspot (OFFICE_MODE=hotspot in .env): the Pi's Wi-Fi becomes an access point and
# is the office side. Laptops that join get a 10.42.0.x address and reach the admin page at
# https://10.42.0.1:8443 and the office share at \\10.42.0.1\CNC. Nothing is routed onwards
# (no internet, no CNC network): the firewall drops all forwarding.
#
#   sudo ./host/setup-hotspot.sh on       start it (host/install.sh does this as its last step)
#   sudo ./host/setup-hotspot.sh off      back to the Wi-Fi network the Pi was on before
#   sudo ./host/setup-hotspot.sh status
#
# Settings from .env: OFFICE_IF (the Wi-Fi port, wlan0), HOTSPOT_SSID, HOTSPOT_PASSWORD.
# The Wi-Fi client connection that was active is kept (as office-net-client, with its
# password) so "off" can restore it. The Pi has one radio: while the hotspot is on, it is
# not on any other Wi-Fi network, so it has no internet unless a cable provides it.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_DIR"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
OFFICE_IF=${OFFICE_IF:-wlan0}
HOTSPOT_SSID=${HOTSPOT_SSID:-cnc-setup-$(hostname -s)}
HOTSPOT_ADDRESS=10.42.0.1/24
SAVED=office-net-client

exists() { nmcli connection show "$1" >/dev/null 2>&1; }
is_ap() { [ "$(nmcli -g 802-11-wireless.mode connection show "$1" 2>/dev/null)" = ap ]; }
active_on() { nmcli -g GENERAL.CONNECTION device show "$1" 2>/dev/null || true; }

case "${1:-}" in
on)
    if [ ${#HOTSPOT_SSID} -lt 1 ] || [ "$(printf %s "$HOTSPOT_SSID" | wc -c)" -gt 32 ]; then
        echo "HOTSPOT_SSID must be 1-32 characters." >&2; exit 1
    fi
    if ! [[ ${HOTSPOT_PASSWORD:-} =~ ^[[:print:]]{8,63}$ ]]; then
        echo "HOTSPOT_PASSWORD in .env must be 8-63 characters (host/install.sh creates one)." >&2; exit 1
    fi
    if [ "${HOTSPOT_TEST_ANY_DEVICE:-}" != 1 ] && [ "$(nmcli -g GENERAL.TYPE device show "$OFFICE_IF" 2>/dev/null)" != wifi ]; then
        echo "$OFFICE_IF is not a Wi-Fi port (check OFFICE_IF in .env; ip -br link lists the ports)." >&2; exit 1
    fi
    # Keep the Wi-Fi network the Pi is on now, so "off" can go back to it.
    for c in office-net "$(active_on "$OFFICE_IF")"; do
        if [ -n "$c" ] && [ "$c" != "$SAVED" ] && exists "$c" && ! is_ap "$c"; then
            ! exists "$SAVED" || nmcli connection delete "$SAVED" >/dev/null
            nmcli connection modify "$c" connection.id "$SAVED" connection.autoconnect no
            break
        fi
    done
    if exists office-net; then nmcli connection delete office-net >/dev/null; fi
    nmcli connection add type wifi ifname "$OFFICE_IF" con-name office-net ssid "$HOTSPOT_SSID" \
        802-11-wireless.mode ap 802-11-wireless.band bg \
        wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
        wifi-sec.psk "$HOTSPOT_PASSWORD" \
        ipv4.method shared ipv4.addresses "$HOTSPOT_ADDRESS" ipv6.method disabled \
        connection.autoconnect yes connection.autoconnect-priority 100 >/dev/null
    if [ "${HOTSPOT_TEST_NO_UP:-}" != 1 ] && ! nmcli connection up office-net >/dev/null; then
        echo "The hotspot didn't start. If the Wi-Fi country isn't set, set it (sudo raspi-config ->" >&2
        echo "Localisation Options -> WLAN Country) and run: sudo ./host/setup-hotspot.sh on" >&2
        echo "To go back to the previous Wi-Fi network: sudo ./host/setup-hotspot.sh off" >&2
        exit 1
    fi
    echo
    echo "Setup hotspot is on."
    echo "  Wi-Fi network:  $HOTSPOT_SSID"
    echo "  Password:       $HOTSPOT_PASSWORD"
    echo "  Admin page:     https://10.42.0.1:8443"
    echo "  Office share:   \\\\10.42.0.1\\CNC"
    ;;
off)
    if exists office-net && is_ap office-net; then nmcli connection delete office-net >/dev/null; fi
    if exists "$SAVED"; then
        nmcli connection modify "$SAVED" connection.id office-net connection.autoconnect yes
        if [ "${HOTSPOT_TEST_NO_UP:-}" != 1 ]; then
            nmcli connection up office-net >/dev/null || echo "office-net will connect when its Wi-Fi network is in range." >&2
        fi
        echo "Setup hotspot is off; back on the previous Wi-Fi network (office-net)."
    elif ! exists office-net; then
        echo "Setup hotspot is off. No previous Wi-Fi network was saved; connect with:" >&2
        echo "  sudo nmcli device wifi connect <network> password <password> name office-net" >&2
    fi
    ;;
status)
    if exists office-net && is_ap office-net; then
        echo "Setup hotspot: on ($(nmcli -g 802-11-wireless.ssid connection show office-net), $(active_on "$OFFICE_IF" | grep -qx office-net && echo active || echo not active))"
    else
        echo "Setup hotspot: off"
    fi
    ;;
*)
    echo "Usage: $0 on|off|status" >&2; exit 1 ;;
esac
