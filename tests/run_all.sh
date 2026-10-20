#!/bin/bash
# Runs every test suite. Needs Docker (Docker Desktop on a Mac is fine) and internet
# access for the first image builds. Nothing here touches the real network or firewall:
# the admin app runs with CNC_ADMIN_DRY_RUN=1, and the full-stack test uses its own
# simulated Pi.
#
#   tests/run_all.sh                 static checks, unit/integration suites, fuzzing, browser
#   tests/run_all.sh --fullstack     ... plus the simulated-Pi network test (~5 min)
set -uo pipefail

PROJECT=$(cd "$(dirname "$0")/.." && pwd)
SCRATCH=$(mktemp -d)
# The project path may contain spaces; mount a copy of the tests from a path without any.
cp -R "$PROJECT/tests" "$SCRATCH/tests"
T="$SCRATCH/tests"
trap 'docker rm -f cnc-test-demo >/dev/null 2>&1; rm -rf "$SCRATCH"' EXIT
RESULTS=()
record() { RESULTS+=("$(printf '%-34s %s' "$1" "$2")"); echo "   -> $2"; }

suite() {  # name, extra docker args, command
    local name=$1 args=$2 cmd=$3 out
    echo "== $name"
    # shellcheck disable=SC2086  # $args holds several docker options
    out=$(docker run --rm $args sh -c "$cmd" 2>&1)
    local passed failed
    passed=$(grep -c '^PASS' <<<"$out"); failed=$(grep -c '^FAIL' <<<"$out")
    if grep -q '^ALL PASSED' <<<"$out" && [ "$failed" -eq 0 ]; then
        record "$name" "PASS ($passed checks)"
    else
        grep -E '^FAIL|Error|Traceback' <<<"$out" | head -5 | sed 's/^/   /'
        record "$name" "FAIL ($passed passed, $failed failed)"
    fi
}

echo "== Build images"
FOCAS_PLATFORM=linux/arm/v7 "$PROJECT/host/build-images.sh" admin samba ftp focas >/dev/null || exit 1   # as host/install.sh builds
# The same image for PCs (FANUC's x86-64 and 32-bit x86 builds), run under emulation here
for p in amd64 386; do docker build -q --pull --network host --platform "linux/$p" -t "local/cnc-focas:$p" "$PROJECT/focas" >/dev/null || exit 1; done

echo "== Static checks"
ruff_out=$(docker run --rm -v "$PROJECT":/src:ro python:3.12-slim sh -c \
    'pip -q install ruff==0.6.9 2>/dev/null; cd /src && ruff check --no-cache --select E9,F,B006,B008,PLE --output-format concise admin/cncadmin focas tests' 2>&1)
grep -q "All checks passed" <<<"$ruff_out" && record "ruff (Python errors)" "PASS" || { echo "$ruff_out" | tail -5; record "ruff (Python errors)" "FAIL"; }
docker run --rm -v "$PROJECT":/src:ro koalaman/shellcheck:stable -S warning /src/host/install.sh /src/host/setup-network.sh \
    /src/host/backup.sh /src/host/restore.sh /src/host/setup-backup.sh /src/tests/test_backup.sh \
    /src/samba/entrypoint.sh /src/ftp/entrypoint.sh /src/tests/run_all.sh /src/tests/fullstack.sh \
    && record "shellcheck" "PASS" || record "shellcheck" "FAIL"
docker run --rm --cap-add NET_ADMIN -v "$PROJECT/host":/h:ro local/cnc-admin:1.0 nft -c -f /h/nftables-fallback.conf \
    && record "fallback firewall (nft -c)" "PASS" || record "fallback firewall (nft -c)" "FAIL"
docker run --rm -e OFFICE_HOSTNAME=cnc.test -v "$PROJECT/caddy/Caddyfile":/etc/caddy/Caddyfile:ro caddy:2.11.7 \
    caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 && record "Caddyfile" "PASS" || record "Caddyfile" "FAIL"
mkdir -p "$SCRATCH/compose" && cp "$PROJECT/docker-compose.yml" "$SCRATCH/compose/" && cp "$PROJECT/.env.example" "$SCRATCH/compose/.env"
(cd "$SCRATCH/compose" && docker compose config -q) && record "docker-compose.yml" "PASS" || record "docker-compose.yml" "FAIL"
audit=$(docker run --rm -v "$PROJECT/admin/requirements.txt":/req.txt:ro python:3.12-slim sh -c 'pip install -q pip-audit 2>/dev/null; pip-audit -r /req.txt 2>&1')
grep -q "No known vulnerabilities" <<<"$audit" && record "pip-audit (admin deps)" "PASS" || { echo "$audit" | tail -4; record "pip-audit (admin deps)" "FAIL"; }

INIT='python -m cncadmin.cli init --office-subnet 10.20.0.0/16 --office-if eth9 --cnc-if eth8 >/dev/null'
ADMIN="--cap-add NET_ADMIN -e PYTHONUNBUFFERED=1 -e CNC_ADMIN_DRY_RUN=1 -e PYTHONPATH=/app -e CNC_UID=2000 -e CNC_GID=2000 -v $T:/tests:ro local/cnc-admin:1.0"
LO='ip addr add 192.168.50.40/32 dev lo &&'
suite "admin web app"            "$ADMIN" "$INIT && timeout 300 python /tests/test_web.py"
suite "control types / protocols" "$ADMIN" "$LO $INIT && timeout 300 python /tests/test_protocols.py"
suite "serial DNC (TCP)"         "$ADMIN" "$LO $INIT && timeout 300 python /tests/test_serial_dnc.py"
suite "serial DNC (tty, 1 MB)"   "$ADMIN" "$INIT && timeout 900 python /tests/test_serial_tty.py"
suite "FOCAS (admin side)"       "$ADMIN" "$INIT && timeout 300 python /tests/test_focas_admin.py"
suite "review fixes"             "$ADMIN" "$INIT && timeout 300 python /tests/test_review_fixes.py"
suite "recycle folders"          "$ADMIN" "$INIT && timeout 300 python /tests/test_recycle.py"
suite "second review fixes"      "$ADMIN" "$INIT && timeout 300 python /tests/test_review2_fixes.py"
suite "health checks + alert emails" "$ADMIN" "$INIT && timeout 300 python /tests/test_monitor.py"
suite "appearance + logo"        "$ADMIN" "$INIT && timeout 300 python /tests/test_appearance.py"
mkdir -p "$SCRATCH/proj" && cp -R "$PROJECT/host" "$SCRATCH/proj/" && cp -R "$T" "$SCRATCH/proj/tests"
suite "backup / restore drill"   "-v $SCRATCH/proj:/project:ro debian:bookworm-slim" \
    "apt-get -qq update >/dev/null && apt-get -qq install -y restic python3 >/dev/null 2>&1 && bash /project/tests/test_backup.sh"
for v in bookworm trixie; do
    suite "network setup (NetworkManager, $v)" "--privileged -v $SCRATCH/proj:/project:ro debian:$v" "bash /project/tests/test_setup_network.sh"
done
suite "FOCAS bridge (32-bit ARM)" "--platform linux/arm/v7 -e PYTHONPATH=/app -v $T:/tests:ro local/cnc-focas:1.0" "timeout 300 python /tests/test_focas_bridge.py"
suite "FOCAS bridge (x86-64)"    "--platform linux/amd64 -e PYTHONPATH=/app -v $T:/tests:ro local/cnc-focas:amd64" "timeout 300 python /tests/test_focas_bridge.py"
suite "FOCAS bridge (32-bit x86)" "--platform linux/386 -e PYTHONPATH=/app -v $T:/tests:ro local/cnc-focas:386" "timeout 300 python /tests/test_focas_bridge.py"
echo "== FOCAS image refuses CPUs FANUC has no library for"
if ! arm64_out=$(docker build --network host --platform linux/arm64 "$PROJECT/focas" 2>&1) \
    && grep -q "FANUC has no FOCAS library for 'arm64'" <<<"$arm64_out"; then
    record "FOCAS: arm64 build refused" "PASS"
else
    record "FOCAS: arm64 build refused" "FAIL"
fi

echo "== Fuzz: random/hostile configs -> real nft and testparm"
mkdir -p "$SCRATCH/fuzz"
docker run --rm -e PYTHONPATH=/app -v "$T":/tests:ro -v "$SCRATCH/fuzz":/out local/cnc-admin:1.0 python /tests/fuzz_render.py "$RANDOM" 1500 | head -1
nft_bad=$(docker run --rm --cap-add NET_ADMIN -v "$SCRATCH/fuzz":/out:ro local/cnc-admin:1.0 python -c "
import json, subprocess
m = json.load(open('/out/manifest.json'))
print(sum(subprocess.run(['nft','-c','-f',f'/out/{c}/nftables.conf'], capture_output=True).returncode != 0 for c in m), len(m))")
smb_bad=$(docker run --rm -v "$SCRATCH/fuzz":/out:ro --entrypoint python3 local/cnc-samba:1.0 -c "
import json, subprocess
m = json.load(open('/out/manifest.json')); bad = 0
for c in m:
    for f in ('smb-cnc.conf', 'smb-office.conf'):
        r = subprocess.run(['testparm','-s',f'/out/{c}/{f}'], capture_output=True, text=True)
        bad += r.returncode != 0 or 'guest ok = Yes' in r.stdout
print(bad, 2 * len(m))")
read -r nb nt <<<"$nft_bad"; read -r sb st <<<"$smb_bad"
[ "$nb" = 0 ] && [ "$sb" = 0 ] && record "fuzzing (nft + testparm)" "PASS ($nt nft, $st testparm)" || record "fuzzing (nft + testparm)" "FAIL ($nb nft, $sb testparm)"

echo "== Browser (Chromium via Playwright) against a demo instance"
mkdir -p "$SCRATCH/demo/config" "$SCRATCH/demo/generated" "$SCRATCH/demo/srv" "$SCRATCH/demo/tls" "$SCRATCH/shots"
docker run --rm -e CNC_ADMIN_DRY_RUN=1 -e PYTHONPATH=/app -e CNC_UID=2000 -e CNC_GID=2000 -v "$SCRATCH/demo/config":/app/config -v "$SCRATCH/demo/generated":/app/generated \
    -v "$SCRATCH/demo/srv":/srv/cnc -v "$SCRATCH/demo/tls":/tls -v "$T/demo":/demo:ro local/cnc-admin:1.0 sh -c \
    'openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=localhost -keyout /tls/key.pem -out /tls/cert.pem 2>/dev/null && python /demo/seed.py' 2>&1 | grep -v INFO | tail -1
docker run -d --name cnc-test-demo --cap-add NET_ADMIN -p 127.0.0.1:18443:8443 -e CNC_ADMIN_DRY_RUN=1 -e CNC_UID=2000 -e CNC_GID=2000 \
    -v "$SCRATCH/demo/config":/app/config -v "$SCRATCH/demo/generated":/app/generated -v "$SCRATCH/demo/srv":/srv/cnc \
    -v "$SCRATCH/demo/tls":/tls:ro -v "$T/demo":/demo:ro local/cnc-admin:1.0 sh -c \
    'ip addr add 192.168.50.40/32 dev lo && (python /demo/fake_fanuc.py &) && (python /demo/fake_focas.py &) && sleep 1 && exec gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8443 --certfile /tls/cert.pem --keyfile /tls/key.pem "cncadmin:create_app()"' >/dev/null
sleep 6
suite "browser UI + JavaScript" "--add-host host.docker.internal:host-gateway -e BASE=https://host.docker.internal:18443 -v $T:/tests:ro -v $SCRATCH/shots:/shots mcr.microsoft.com/playwright/python:v1.48.0-jammy" \
    "pip install -q playwright==1.48.0 2>/dev/null; python /tests/test_browser.py"

if [ "${1:-}" = "--fullstack" ]; then
    echo "== Full stack (simulated Pi with office + CNC networks)"
    "$PROJECT/tests/fullstack.sh" --clean >/dev/null
    fs=$("$PROJECT/tests/fullstack.sh" 2>&1); echo "$fs" | grep FAIL
    line=$(grep "^Full stack:" <<<"$fs")
    if grep -q " 0 failed" <<<"$line"; then record "full stack (simulated Pi)" "PASS (${line#Full stack: })"; else record "full stack (simulated Pi)" "FAIL (${line#Full stack: })"; fi
    "$PROJECT/tests/fullstack.sh" --clean >/dev/null
fi

echo; echo "================ SUMMARY ================"
printf '%s\n' "${RESULTS[@]}"
! printf '%s\n' "${RESULTS[@]}" | grep -q "FAIL"
