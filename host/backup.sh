#!/bin/bash
# Nightly backup of machine programs and configuration with restic
# (run by cnc-backup.timer; safe to run by hand: sudo /opt/cnc-server/host/backup.sh).
#
# Destination and password come from /etc/cnc-backup.env (see host/cnc-backup.env.example;
# host/setup-backup.sh creates it). The result is written to config/backup-status.json,
# which the admin page shows and alerts on.
set -uo pipefail

PROJECT_DIR=${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
DATA_DIR=${DATA_DIR:-/srv/cnc}
ENV_FILE=${CNC_BACKUP_ENV:-/etc/cnc-backup.env}
STATUS_FILE=${STATUS_FILE:-$PROJECT_DIR/config/backup-status.json}
# The last 3 runs are always kept too, so a manual backup before a change survives that night's run.
KEEP=(--keep-last 3 --keep-daily 14 --keep-weekly 8 --keep-monthly 12)

MARKER=.cnc-backup-repository   # on local destinations: proves the backup disk is mounted

status() {  # result, message -- JSON written by Python so any message text stays valid
    mkdir -p "$(dirname "$STATUS_FILE")"
    python3 -c 'import json, sys, time; json.dump({"time": int(time.time()), "result": sys.argv[1], "message": sys.argv[2]}, open(sys.argv[3], "w"))' \
        "$1" "$2" "$STATUS_FILE.tmp"
    chmod 0644 "$STATUS_FILE.tmp" && mv "$STATUS_FILE.tmp" "$STATUS_FILE"
}

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    if ! . "$ENV_FILE"; then
        set +a
        status failed "The backup settings file $ENV_FILE has an error (re-run host/setup-backup.sh)"
        exit 1
    fi
    set +a
fi
if [ -z "${RESTIC_REPOSITORY:-}" ]; then
    status not_configured "No backup destination set (run host/setup-backup.sh)"
    echo "Backups are not configured: run host/setup-backup.sh" >&2
    exit 0
fi
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE

log=$(mktemp); trap 'rm -f "$log"' EXIT
fail() { status failed "$1: $(tail -1 "$log" | tr -d '\r' | cut -c1-200)"; cat "$log" >&2; exit 1; }

# Never create a repository here (host/setup-backup.sh does that): if a backup disk isn't
# mounted, "init" would silently start backing up onto the Pi's own disk.
case "$RESTIC_REPOSITORY" in
    /*) if [ ! -f "$RESTIC_REPOSITORY/$MARKER" ]; then
            echo "missing $RESTIC_REPOSITORY/$MARKER" > "$log"
            fail "Backup disk not mounted or repository missing at $RESTIC_REPOSITORY"
        fi ;;
esac
restic cat config >/dev/null 2>"$log" || fail "Could not open the backup repository"

paths=("$DATA_DIR" "$PROJECT_DIR/config")
[ -f "$PROJECT_DIR/.env" ] && paths+=("$PROJECT_DIR/.env")
restic backup --tag cnc --exclude '*.tmp' --exclude "$STATUS_FILE" "${paths[@]}" >"$log" 2>&1 || fail "Backup failed"
summary=$(grep -E '^(Added to the repository|snapshot .* saved)' "$log" | tr '\n' ' ')

restic forget --tag cnc "${KEEP[@]}" --prune >"$log" 2>&1 || fail "Removing old backups failed"
# Weekly integrity check of the repository (Sundays).
if [ "$(date +%u)" = 7 ] || [ "${FORCE_CHECK:-}" = 1 ]; then
    restic check >"$log" 2>&1 || fail "Repository check FAILED"
    summary="$summary; repository check passed"
fi

status ok "$summary"
echo "Backup OK: $summary"
