#!/bin/bash
# Update to the latest code: pull, rebuild the images, regenerate the server configs and
# firewall from config/site.json, and restart everything. Settings, users, passwords and
# programs are kept. Run it when no machine is transferring: services restart (about a minute).
#
#   sudo ./host/update.sh
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_DIR"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

git pull --ff-only
./host/build-images.sh
docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli render
nft -c -f /etc/nftables.conf
nft -f /etc/nftables.conf
docker compose up -d --force-recreate --remove-orphans
echo
echo "Updated to $(git log -1 --format='%h %s')."
