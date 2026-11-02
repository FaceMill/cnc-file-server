#!/bin/bash
# Reset the admin page password (user 'admin'). Needs a login on the Pi itself (keyboard,
# SSH or Pi Connect) with sudo: that is the proof of who you are, so there is no reset link
# on the web page. Every browser that was logged in is logged out.
#
#   sudo ./host/reset-admin-password.sh             type a new password (at least 10 characters)
#   sudo ./host/reset-admin-password.sh --generate  set a random password and print it
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_DIR"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 $*" >&2
    exit 1
fi
case "${1:-}" in
    ""|--generate) ;;
    *) echo "Usage: sudo $0 [--generate]" >&2; exit 1 ;;
esac

docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli set-admin-password "$@"
echo "After 5 wrong passwords the login page locks that computer out for 5 minutes; wait it out if it still refuses."
