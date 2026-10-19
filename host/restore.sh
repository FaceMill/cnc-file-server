#!/bin/bash
# Restore from the restic backup made by host/backup.sh.
#
#   sudo host/restore.sh list                      show backups (snapshots)
#   sudo host/restore.sh file <path> [snapshot]    restore one file or folder next to the
#                                                  original as <name>.restored-<date>
#                                                  e.g. file /srv/cnc/haas1/O1234.NC
#   sudo host/restore.sh all [snapshot]            disaster recovery: put back every
#                                                  program and the whole configuration,
#                                                  then regenerate configs and restart
# snapshot defaults to "latest".
set -euo pipefail

PROJECT_DIR=${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
DATA_DIR=${DATA_DIR:-/srv/cnc}
ENV_FILE=${CNC_BACKUP_ENV:-/etc/cnc-backup.env}
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi
: "${RESTIC_REPOSITORY:?No backup destination: create $ENV_FILE (see host/cnc-backup.env.example)}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE

cmd=${1:-}
case "$cmd" in
list)
    restic snapshots --tag cnc
    ;;
file)
    path=${2:?usage: restore.sh file <path> [snapshot]}
    snap=${3:-latest}
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    restic restore "$snap" --tag cnc --target "$stage" --include "$path"
    [ -e "$stage$path" ] || { echo "$path is not in snapshot $snap" >&2; exit 1; }
    dest="$path.restored-$(date +%Y%m%d-%H%M%S)"
    cp -a "$stage$path" "$dest"
    echo "Restored to $dest"
    ;;
all)
    snap=${2:-latest}
    stage_parent=${STAGE_DIR:-/var/tmp}
    # Space check before anything is stopped: the restore is staged on this disk.
    need=$(restic stats "$snap" --tag cnc --mode restore-size --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["total_size"])')
    have=$(( $(df -Pk "$stage_parent" | awk 'NR==2 {print $4}') * 1024 ))
    if [ "$have" -lt $(( need + need / 10 )) ]; then
        echo "Not enough free space in $stage_parent: need about $(( need / 1048576 )) MB, have $(( have / 1048576 )) MB." >&2
        echo "Free some space or set STAGE_DIR to a bigger disk." >&2
        exit 1
    fi
    stage=$(mktemp -d "$stage_parent/cnc-restore.XXXXXX")
    trap 'rm -rf "$stage"' EXIT
    echo "Restoring snapshot $snap into $stage ..."
    restic restore "$snap" --tag cnc --target "$stage"
    [ -d "$stage$DATA_DIR" ] && [ -d "$stage$PROJECT_DIR/config" ] || { echo "Snapshot doesn't contain $DATA_DIR and $PROJECT_DIR/config" >&2; exit 1; }
    if [ "${SKIP_DOCKER:-}" != 1 ]; then (cd "$PROJECT_DIR" && docker compose down) || true; fi
    # Replace, don't merge: the current data is kept aside (not deleted) so nothing newer is lost.
    stamp=$(date +%Y%m%d-%H%M%S)
    for d in "$DATA_DIR" "$PROJECT_DIR/config"; do
        if [ -e "$d" ]; then mv "$d" "$d.before-restore-$stamp"; echo "Current $d kept as $d.before-restore-$stamp"; fi
        mkdir -p "$(dirname "$d")"
        mv "$stage$d" "$d"            # a rename on the same disk: no extra space needed
    done
    if [ -f "$stage$PROJECT_DIR/.env" ] && [ ! -f "$PROJECT_DIR/.env" ]; then mv "$stage$PROJECT_DIR/.env" "$PROJECT_DIR/.env"; fi
    # Runtime state that must not come back from a backup.
    rm -f "$PROJECT_DIR/config/pending-office.json" "$PROJECT_DIR/config/alert-state.json"
    if [ "${SKIP_DOCKER:-}" != 1 ]; then
        cd "$PROJECT_DIR"
        docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli render
        nft -f /etc/nftables.conf
        docker compose up -d
    fi
    echo "Restore complete from snapshot $snap."
    echo "When everything works, delete the *.before-restore-$stamp folders to free the space."
    ;;
*)
    sed -n '2,13p' "$0"; exit 1
    ;;
esac
