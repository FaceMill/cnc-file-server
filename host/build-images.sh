#!/bin/bash
# Builds this project's images with plain "docker build", then pulls the others (Caddy).
#
#   sudo ./host/build-images.sh                  all images (install, monthly updates)
#   sudo ./host/build-images.sh focas            just one: admin, samba, ftp or focas
#
# Not "docker compose build": builds need the host network (host/install.sh turns Docker's
# own NAT off), and newer docker compose builds through "buildx bake", which refuses
# network=host without an --allow=network.host grant that compose can't pass.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_DIR"

# FOCAS bridge platform: from the environment (host/install.sh) or .env, else a Pi's.
if [ -z "${FOCAS_PLATFORM:-}" ] && [ -f .env ]; then
    FOCAS_PLATFORM=$(sed -n 's/^FOCAS_PLATFORM=//p' .env | tail -1 | tr -d '"')
fi
FOCAS_PLATFORM=${FOCAS_PLATFORM:-linux/arm/v7}

targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=(admin samba ftp focas)
for t in "${targets[@]}"; do
    echo "== Building local/cnc-$t:1.0"
    case "$t" in
        admin|samba|ftp) docker build --pull --network host -t "local/cnc-$t:1.0" "$t" ;;
        focas) docker build --pull --network host --platform "$FOCAS_PLATFORM" -t local/cnc-focas:1.0 focas ;;
        *) echo "Unknown image '$t' (admin, samba, ftp or focas)." >&2; exit 1 ;;
    esac
done

if [ $# -eq 0 ]; then
    echo "== Pulling Caddy"
    docker compose pull --ignore-buildable --quiet
fi
