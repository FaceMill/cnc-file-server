#!/bin/bash
# Full-stack test: simulates the Pi as a Docker-in-Docker container with two networks
# (eth0 = office 10.20.0.0/16, eth1 = CNC 192.168.50.0/24), installs this project the way
# host/install.sh does, loads the real generated firewall, and drives it from simulated
# office PCs and CNC machines. Needs Docker (Docker Desktop is fine) and internet access.
#
#   tests/fullstack.sh            build images, run, leave the simulation running
#   tests/fullstack.sh --clean    remove the simulation containers and networks
set -uo pipefail

PROJECT=$(cd "$(dirname "$0")/.." && pwd)
HERE="$PROJECT/tests"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1"; }
t()   { local d=$1; shift; if "$@" >/tmp/fs.out 2>&1; then ok "$d"; else bad "$d :: $(tail -1 /tmp/fs.out)"; fi; }
n()   { local d=$1; shift; if "$@" >/tmp/fs.out 2>&1; then bad "$d (unexpectedly succeeded)"; else ok "$d"; fi; }
port(){ docker exec "$1" nc -z -w 3 "$2" "$3"; }
pi()  { docker exec pi sh -c "$1"; }

clean() {
    docker rm -f pi office-pc haas1 rogue fanuc1 devserver >/dev/null 2>&1
    docker network rm pi-office pi-cnc >/dev/null 2>&1
}
if [ "${1:-}" = "--clean" ]; then clean; echo "cleaned"; exit 0; fi

echo "== Build images"
for i in admin samba ftp; do docker build -q --network host -t "local/cnc-$i:1.0" "$PROJECT/$i" >/dev/null || exit 1; done
docker build -q --network host --platform linux/arm/v7 -t local/cnc-focas:1.0 "$PROJECT/focas" >/dev/null || exit 1
docker pull -q caddy:2.8.4 >/dev/null
docker build -q -t local/netclient - >/dev/null <<'EOF' || exit 1
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends smbclient python3 curl iproute2 netcat-openbsd ca-certificates && rm -rf /var/lib/apt/lists/*
EOF

echo "== Simulated Pi (eth0 office, eth1 CNC)"
clean
docker network create --subnet 10.20.0.0/16 --gateway 10.20.0.1 pi-office >/dev/null
docker network create --subnet 192.168.50.0/24 --gateway 192.168.50.254 pi-cnc >/dev/null
docker run -d --name pi --hostname cnc-files --privileged --network pi-office --ip 10.20.0.10 \
    docker:27-dind --iptables=false --ip6tables=false --ip-forward=false >/dev/null
docker network connect --ip 192.168.50.1 pi-cnc pi
for _ in $(seq 1 30); do pi 'docker info' >/dev/null 2>&1 && break; sleep 2; done
pi 'apk add -q nftables >/dev/null'
docker save local/cnc-admin:1.0 local/cnc-samba:1.0 local/cnc-ftp:1.0 local/cnc-focas:1.0 caddy:2.8.4 | docker exec -i pi docker load >/dev/null
pi 'mkdir -p /opt/cnc-server'
tar -C "$PROJECT" --exclude=.vscode --exclude=tests --exclude=config --exclude=generated --exclude=data -cf - . | docker exec -i pi tar -C /opt/cnc-server -xf -

echo "== Install (as host/install.sh, minus apt/systemd/NetworkManager)"
pi '
set -e; cd /opt/cnc-server
cp .env.example .env && sed -i "s/^OFFICE_HOSTNAME=.*/OFFICE_HOSTNAME=cnc-files.test/" .env
addgroup -g 2000 cncfiles; adduser -D -H -u 2000 -G cncfiles -s /sbin/nologin cncfiles
mkdir -p /srv/cnc config generated data/caddy data/portainer
chown 2000:2000 /srv/cnc; chmod 0770 /srv/cnc; chmod 0700 config generated
sysctl -qw net.ipv4.ip_forward=0
docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli init --office-subnet 10.20.0.0/16 >/dev/null
docker compose run --rm --no-deps cnc-admin python -m cncadmin.cli render >/dev/null
docker compose run --rm --no-deps cnc-admin python -c "from werkzeug.security import generate_password_hash as g; from cncadmin import config; config.set_admin_password_hash(g(\"fullstack-pass-1\"))"
sed "s|@PROJECT_DIR@|/opt/cnc-server|" host/nftables.conf > /etc/nftables.conf
nft -c -f /etc/nftables.conf && nft -f /etc/nftables.conf
nft -c -f host/nftables-fallback.conf
docker compose up -d --no-build >/dev/null 2>&1
' || { echo "install failed"; exit 1; }
sleep 10
running=$(pi 'docker ps --format "{{.Names}}"' | sort | tr '\n' ' ')
[ "$running" = "caddy cnc-admin focas-bridge ftp-cnc samba-cnc samba-office " ] && ok "default stack: $running" || bad "unexpected containers: $running"

echo "== Clients"
docker run -d --name office-pc --cap-add NET_ADMIN --network pi-office --ip 10.20.5.5 --add-host cnc-files.test:10.20.0.10 \
    -v "$HERE/fs_admin.py:/fs_admin.py:ro" local/netclient sleep 7200 >/dev/null
for c in "haas1 192.168.50.11" "rogue 192.168.50.99" "fanuc1 192.168.50.21"; do
    set -- $c; docker run -d --name "$1" --cap-add NET_ADMIN --network pi-cnc --ip "$2" local/netclient sleep 7200 >/dev/null
done
docker run -d --name devserver --network pi-cnc --ip 192.168.50.40 local/netclient python3 -c "
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', 4001)); s.listen(5)
while True:
    c, _ = s.accept(); time.sleep(2)
    c.sendall(b'\x00\x12%\r\nO4444(PUNCHED VIA DEVICE SERVER)\r\nM30\r\n%\x14'); time.sleep(600)
" >/dev/null

A="docker exec office-pc python3 /fs_admin.py"
echo "== Admin page over HTTPS (Caddy)"
t "login by hostname" sh -c "$A login password=fullstack-pass-1 | grep -q '\[\]'"
t "admin page answers by IP too (confirm after an IP change)" sh -c "docker exec office-pc curl -sk https://10.20.0.10:8443/login | grep -q 'CNC File Server'"
t "https://<pi>/ redirects to :8443" sh -c "docker exec office-pc curl -sk -o /dev/null -w '%{redirect_url}' --resolve cnc-files.test:443:10.20.0.10 https://cnc-files.test/ | grep -q ':8443/'"
t "add office user" sh -c "$A post path=/users/add name=programmer password=Office-pass-1 repeat=Office-pass-1 | grep -q 'User added'"
t "add FTP machine" sh -c "$A post path=/machines/new name=fanuc1 label=FANUC type=fanuc-dataserver ip=192.168.50.21 password=FANUCPW1 | grep -q added"
t "add serial machine (device server)" sh -c "$A post path=/machines/new name=dnc1 label=RS232 type=fanuc-rs232 ip=192.168.50.40 password= serial_transport=tcp serial_tcp_port=4001 serial_enabled=on serial_baud=9600 serial_data_bits=7 serial_parity=E serial_stop_bits=2 serial_flow=xonxoff serial_send_delay=1 serial_line_delay_ms=0 serial_receive_timeout=1 serial_pause_timeout=30 serial_excluded_ext= | grep -q added"
t "add FOCAS machine" sh -c "$A post path=/machines/new name=focas1 label=FOCAS type=fanuc-focas ip=192.168.50.31 password= focas_port=8193 focas_timeout=3 | grep -q added"
H1=$($A password name=haas1)
OFF="smbclient //10.20.0.10/CNC -U programmer%Office-pass-1 -m SMB3 --client-protection=encrypt"

echo "== Office PC 10.20.5.5"
t "SMB3 encrypted upload into haas1 folder" docker exec office-pc sh -c "printf '%%\nO1001\nM30\n%%\n' > /tmp/O1001.NC && $OFF -c 'cd haas1; put /tmp/O1001.NC O1001.NC'"
n "SMB1 refused on office side" docker exec office-pc smbclient //10.20.0.10/CNC -U programmer%Office-pass-1 -m NT1 --option='client min protocol=NT1' -c ls
n "machine account refused on office share" docker exec office-pc smbclient //10.20.0.10/CNC -U "haas1%$H1" -c ls
for p in 8090 8095 8080 9443 2019 139 21; do n "office -> Pi port $p closed" port office-pc 10.20.0.10 "$p"; done
docker exec office-pc ip route add 192.168.50.0/24 via 10.20.0.10 2>/dev/null
docker exec -d haas1 nc -l -p 5555
n "office routed via Pi -> CNC machine (no forwarding)" port office-pc 192.168.50.11 5555
n "office -> Pi's CNC-side SMB1 port (firewall checks destination)" port office-pc 192.168.50.1 445

echo "== CNC machine haas1 192.168.50.11"
t "SMB1 NTLMv1 login sees the office upload" sh -c "docker exec haas1 smbclient //192.168.50.1/haas1 -U 'haas1%$H1' -m NT1 --option='client min protocol=NT1' --option='client ntlmv2 auth=no' --option='client use spnego=no' -c ls | grep -q O1001.NC"
t "Haas saves back; office sees it" sh -c "docker exec haas1 sh -c \"printf 'X\n' > /tmp/O2002.NC && smbclient //192.168.50.1/haas1 -U 'haas1%$H1' -m NT1 --option='client min protocol=NT1' -c 'put /tmp/O2002.NC O2002.NC'\" && docker exec office-pc $OFF -c 'cd haas1; ls' | grep -q O2002.NC"
n "symlink creation over SMB1 refused" docker exec haas1 sh -c "smbclient //192.168.50.1/haas1 -U 'haas1%$H1' -m NT1 --option='client min protocol=NT1' -c 'symlink /etc/passwd evil' 2>&1 | grep -qv NT_STATUS"
for p in 8443 443 22 9090 8095 21; do n "haas1 -> Pi port $p closed" port haas1 192.168.50.1 "$p"; done
n "haas1 -> another machine's share" docker exec haas1 smbclient //192.168.50.1/haas2 -U "haas1%$H1" -m NT1 --option='client min protocol=NT1' -c ls
docker exec haas1 ip route add 10.20.0.0/16 via 192.168.50.1 2>/dev/null
n "haas1 routed via Pi -> office (no forwarding)" port haas1 10.20.5.5 22
n "haas1 -> Pi's office-side SMB port (firewall checks destination)" port haas1 10.20.0.10 445

echo "== Rogue device 192.168.50.99"
n "SMB blocked" port rogue 192.168.50.1 445
n "FTP blocked" port rogue 192.168.50.1 21

echo "== FANUC FTP 192.168.50.21"
t "passive + active upload/download through the firewall" docker exec fanuc1 python3 -c "
import ftplib, io
for pasv in (True, False):
    f = ftplib.FTP('192.168.50.1', timeout=10); f.login('fanuc1', 'FANUCPW1'); f.set_pasv(pasv)
    f.storbinary('STOR O300%d.NC' % pasv, io.BytesIO(b'%\r\nO3001\r\nM30\r\n%\r\n')); assert 'O300%d.NC' % pasv in f.nlst(); f.quit()"
t "office sees FTP uploads" sh -c "docker exec office-pc $OFF -c 'cd fanuc1; ls' | grep -q O3001.NC"

echo "== Serial DNC via device server 192.168.50.40"
pi 'docker restart cnc-admin >/dev/null'; sleep 10
t "punched program saved and visible to the office" sh -c "docker exec office-pc $OFF -c 'cd dnc1/received; ls' | grep -q O4444_"
t "office can delete a received program" sh -c "docker exec office-pc $OFF -c 'cd dnc1/received; del O4444_*' && ! docker exec office-pc $OFF -c 'cd dnc1/received; ls' | grep -q O4444_"

echo "== FOCAS (admin -> 32-bit bridge with token)"
t "bridge reports unreachable CNC" sh -c "$A get path=/machines/focas1/focas/status | grep -q 'cannot connect'"
n "bridge refuses requests without the token" pi 'wget -qO- "http://127.0.0.1:8095/status?ip=192.168.50.31"'

echo "== Resilience"
pi 'docker restart samba-cnc >/dev/null'; sleep 4
t "machine logs in after samba-cnc restart" docker exec haas1 smbclient //192.168.50.1/haas1 -U "haas1%$H1" -m NT1 --option='client min protocol=NT1' -c ls
t "fallback firewall loads if the generated one is broken" pi 'cp /opt/cnc-server/generated/nftables.conf /tmp/nft.bak && echo garbage > /opt/cnc-server/generated/nftables.conf && { nft -f /etc/nftables.conf 2>/dev/null || nft -f /opt/cnc-server/host/nftables-fallback.conf; } && nft list ruleset | grep -q "tcp dport 22 accept"; rc=$?; cp /tmp/nft.bak /opt/cnc-server/generated/nftables.conf && nft -f /etc/nftables.conf; exit $rc'

echo
echo "Full stack: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
