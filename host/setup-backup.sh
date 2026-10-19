#!/bin/bash
# Configure nightly backups: asks for the destination, creates a random repository
# password, initialises the repository and runs a first backup.
#   sudo /opt/cnc-server/host/setup-backup.sh
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=/etc/cnc-backup.env
PASS_FILE=/etc/cnc-backup.pass

cat <<'EOF'
Backup destination (a restic repository), for example:
  /mnt/backup/cnc                         USB disk or mounted network share
  sftp:backup@fileserver:/backups/cnc     SSH/SFTP server (set up SSH keys for root first)
  rest:https://backup.example.com/cnc     restic REST server
EOF
read -r -p "Destination: " repo
[ -n "$repo" ] || { echo "No destination given." >&2; exit 1; }

# A local path that resolves to the Pi's own root disk is usually an unmounted backup disk.
case "$repo" in
/*)
    probe=$repo; while [ ! -e "$probe" ]; do probe=$(dirname "$probe"); done
    if [ "$(findmnt -n -o TARGET -T "$probe")" = "/" ]; then
        echo "WARNING: $repo is on the Pi's own disk, not a separate backup disk (is it mounted?)."
        read -r -p "Back up to the Pi's own disk anyway? [y/N] " yn
        [ "$yn" = y ] || [ "$yn" = Y ] || exit 1
    fi ;;
esac

if [ ! -f "$PASS_FILE" ]; then
    umask 077
    head -c 24 /dev/urandom | base64 > "$PASS_FILE"
fi
# Quoted with %q: backup.sh sources this file, so spaces or $ must not be interpreted.
printf 'RESTIC_REPOSITORY=%q\nRESTIC_PASSWORD_FILE=%q\n' "$repo" "$PASS_FILE" > "$ENV_FILE"
chmod 0600 "$ENV_FILE" "$PASS_FILE"

export RESTIC_REPOSITORY="$repo" RESTIC_PASSWORD_FILE="$PASS_FILE"
if ! restic cat config >/dev/null 2>&1; then
    case "$repo" in /*) mkdir -p "$repo" ;; esac
    restic init
fi
# Marker that backup.sh checks for on local destinations (catches an unmounted disk).
case "$repo" in /*) touch "$repo/.cnc-backup-repository" ;; esac

echo
echo "Repository password (stored in $PASS_FILE). WRITE IT DOWN and keep it off the Pi:"
echo "without it the backups can't be restored if the Pi is lost."
echo
echo "    $(cat "$PASS_FILE")"
echo
FORCE_CHECK=1 "$PROJECT_DIR/host/backup.sh"
systemctl enable --now cnc-backup.timer
echo "Nightly backups enabled (cnc-backup.timer)."
