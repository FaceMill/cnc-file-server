#!/bin/bash
# Backup/restore drill, run inside a Debian container with restic (see run_all.sh):
# back up programs + config, wipe them, restore, and compare every file.
set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "PASS $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL $1"; }
chk() { local d=$1; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }

export PROJECT_DIR=/opt/cnc-server DATA_DIR=/srv/cnc CNC_BACKUP_ENV=/etc/cnc-backup.env
cp -a /project /opt/cnc-server
H=/opt/cnc-server/host
status() { python3 -c "import json; print(json.load(open('$PROJECT_DIR/config/backup-status.json'))['$1'])"; }

# Sample data: programs, received, recycle, odd names, ownership and modes
mkdir -p /srv/cnc/haas1/.recycle /srv/cnc/dnc1/received "/srv/cnc/haas2/sub dir" /opt/cnc-server/config
printf '%%\nO1234\nM30\n%%\n' > /srv/cnc/haas1/O1234.NC
head -c 2000000 /dev/urandom > /srv/cnc/dnc1/received/BIG.nc
echo deleted > /srv/cnc/haas1/.recycle/OLD.NC
echo "spaces & ünïcødé" > "/srv/cnc/haas2/sub dir/naïve file.NC"
echo '{"workgroup": "WORKGROUP"}' > /opt/cnc-server/config/site.json
echo secret > /opt/cnc-server/config/secret_key; chmod 600 /opt/cnc-server/config/secret_key
echo OFFICE_HOSTNAME=x > /opt/cnc-server/.env
chown -R 2000:2000 /srv/cnc; chmod 0770 /srv/cnc/haas1
snapshot() { (cd / && find srv/cnc opt/cnc-server/config opt/cnc-server/.env ! -name 'backup-status.json' -printf '%p %U:%G %m\n' | sort; \
              find srv/cnc opt/cnc-server/config -type f ! -name 'backup-status.json' -exec sha256sum {} + | sort); }

# Not configured -> status says so, exit 0
rm -f /etc/cnc-backup.env
"$H/backup.sh" 2>/dev/null; chk "unconfigured backup exits cleanly" [ $? -eq 0 ]
chk "status: not_configured" [ "$(status result)" = not_configured ]

# Configured, but the "backup disk" isn't mounted: must fail, and must NOT create a repository
head -c 24 /dev/urandom | base64 > /etc/cnc-backup.pass
printf 'RESTIC_REPOSITORY=/backup/repo\nRESTIC_PASSWORD_FILE=/etc/cnc-backup.pass\n' > /etc/cnc-backup.env
"$H/backup.sh" >/dev/null 2>&1; chk "unmounted backup disk: backup fails" [ $? -ne 0 ]
chk "unmounted backup disk: nothing created on the Pi's disk" [ ! -e /backup/repo ]
chk "status: failed, says the disk isn't mounted" sh -c "[ \"$(status result)\" = failed ] && grep -q 'not mounted' $PROJECT_DIR/config/backup-status.json"

# As host/setup-backup.sh does: create the repository and its marker
mkdir -p /backup/repo && RESTIC_REPOSITORY=/backup/repo RESTIC_PASSWORD_FILE=/etc/cnc-backup.pass restic init >/dev/null 2>&1 \
    && touch /backup/repo/.cnc-backup-repository
FORCE_CHECK=1 "$H/backup.sh" >/tmp/b1.log 2>&1; chk "first backup succeeds" [ $? -eq 0 ]
chk "status: ok, with repository check" sh -c "[ \"$(status result)\" = ok ] && grep -q 'check passed' $PROJECT_DIR/config/backup-status.json"
before=$(snapshot)
echo changed > /srv/cnc/haas1/O1234.NC
"$H/backup.sh" >/dev/null 2>&1; chk "second backup succeeds" [ $? -eq 0 ]
chk "two snapshots kept" sh -c "[ \$(RESTIC_REPOSITORY=/backup/repo RESTIC_PASSWORD_FILE=/etc/cnc-backup.pass restic snapshots --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))') -eq 2 ]"

# Single-file restore from the first snapshot
first=$(RESTIC_REPOSITORY=/backup/repo RESTIC_PASSWORD_FILE=/etc/cnc-backup.pass restic snapshots --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["short_id"])')
"$H/restore.sh" file /srv/cnc/haas1/O1234.NC "$first" >/dev/null
chk "single file restored next to the original (old version)" sh -c "grep -q O1234 /srv/cnc/haas1/O1234.NC.restored-* && grep -q changed /srv/cnc/haas1/O1234.NC"
rm -f /srv/cnc/haas1/O1234.NC.restored-*

# Restore over live data: newer work is kept aside, not merged or lost
echo "newer than the backup" > /srv/cnc/haas1/NEWER.NC
echo '{"old": {}, "new": {}, "deadline": 1}' > /opt/cnc-server/config/pending-office.json
SKIP_DOCKER=1 "$H/restore.sh" all "$first" >/tmp/r0.log 2>&1; chk "restore over live data runs" [ $? -eq 0 ]
chk "newer live file kept in the before-restore folder" sh -c 'grep -q newer /srv/cnc.before-restore-*/haas1/NEWER.NC'
chk "restored tree is the snapshot, not a merge" [ ! -e /srv/cnc/haas1/NEWER.NC ]
chk "stale pending network change not restored" [ ! -e /opt/cnc-server/config/pending-office.json ]

# Disaster: lose everything, restore the first snapshot
rm -rf /srv/cnc /opt/cnc-server/config /opt/cnc-server/.env /srv/cnc.before-restore-* /opt/cnc-server/config.before-restore-*
SKIP_DOCKER=1 "$H/restore.sh" all "$first" >/tmp/r.log 2>&1; chk "full restore onto an empty disk runs" [ $? -eq 0 ]
after=$(snapshot)
if [ "$before" = "$after" ]; then ok "every file, owner and mode identical after restore"
else bad "restore differs"; diff <(echo "$before") <(echo "$after") | head; fi

# Failure is reported (wrong password)
echo wrong > /etc/cnc-backup.pass
"$H/backup.sh" >/dev/null 2>&1; chk "backup with a wrong password fails" [ $? -ne 0 ]
chk "status: failed, with a reason" sh -c "[ \"$(status result)\" = failed ] && grep -q 'Could not open' $PROJECT_DIR/config/backup-status.json"

# Status file stays valid JSON whatever the error text (tab, quotes, backslash, unicode)
printf 'RESTIC_REPOSITORY=%q\nRESTIC_PASSWORD_FILE=/etc/cnc-backup.pass\n' "$(printf '/backup/x\tq"uo\\te ünï')" > /etc/cnc-backup.env
"$H/backup.sh" >/dev/null 2>&1
chk "status JSON valid with odd characters in the error" python3 -c "import json; d=json.load(open('$PROJECT_DIR/config/backup-status.json')); assert d['result'] == 'failed' and 'ünï' in d['message']"
# A damaged settings file is an error, not "not configured"
printf 'RESTIC_REPOSITORY="unterminated\n' > /etc/cnc-backup.env
"$H/backup.sh" >/dev/null 2>&1
chk "damaged settings file reported as a failure" sh -c "[ \"$(status result)\" = failed ] && grep -q 'settings file' $PROJECT_DIR/config/backup-status.json"

# Env file written by setup-backup's printf %q: spaces and $ are data, not shell
repo='/backup/my repo $HOME;touch /tmp/PWNED'
printf 'RESTIC_REPOSITORY=%q\nRESTIC_PASSWORD_FILE=%q\n' "$repo" /etc/cnc-backup.pass > /etc/cnc-backup.env
"$H/backup.sh" >/dev/null 2>&1
chk "quoted destination isn't executed as a command" [ ! -e /tmp/PWNED ]
chk "quoted destination read back exactly" grep -q 'not mounted or repository missing at /backup/my repo \$HOME;touch /tmp/PWNED' "$PROJECT_DIR/config/backup-status.json"

# Host health snapshot works without Pi tools, and survives a broken probe
HEALTH_FILE=/tmp/h.json python3 "$H/health.py"
chk "health.py writes valid JSON off-Pi" python3 -c "import json; d=json.load(open('/tmp/h.json')); assert d['disks'] and 'time' in d"
printf '#!/bin/sh\necho throttled=0xZZ\n' > /usr/local/bin/vcgencmd && chmod +x /usr/local/bin/vcgencmd
HEALTH_FILE=/tmp/h2.json python3 "$H/health.py"
chk "a broken probe is reported, the rest of the snapshot kept" python3 -c "import json; d=json.load(open('/tmp/h2.json')); assert d['throttled'] is None and d['disks'] and any('throttled' in e for e in d['errors'])"

echo "backup drill: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "ALL PASSED"
