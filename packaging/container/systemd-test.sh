#!/bin/bash
# Unit behaviour under a running systemd (UPD-003, PKG-003, PKG-005), run
# with `docker exec` inside a container booted from Dockerfile.systemd.
# packaging/package-test.sh runs it.
#
# Environment: DEB_N, DEB_N1, DEBVER_N1 as for install-test.sh.
set -euo pipefail
: "${DEB_N:?}" "${DEB_N1:?}" "${DEBVER_N1:?}"
# shellcheck source=packaging/container/testlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/testlib.sh"

prop() { systemctl show --property "$1" --value "$2"; }
active_since() { prop ActiveEnterTimestampMonotonic vakr.target; }

# api_get PATH tcp|unix: GET from vakr-api with the bundled Python's http.client
# (the image has no curl); prints "<status> <body>".
cat >/tmp/api_get.py <<'PY'
import http.client, socket, sys
path, where = sys.argv[1], sys.argv[2]
class Unix(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect("/run/vakr/api.sock")
conn = Unix("localhost", timeout=5) if where == "unix" else http.client.HTTPConnection("127.0.0.1", 8080, timeout=5)
conn.request("GET", path)
answer = conn.getresponse()
print(answer.status, answer.read().decode())
PY
chmod 0644 /tmp/api_get.py
api_get() { PYTHONDONTWRITEBYTECODE=1 /opt/vakr/venv/bin/python3 /tmp/api_get.py "$@"; }

# web_get PATH: GET a web UI path over loopback TCP; prints the status, the
# headers the web checks need, and the body (ADR-035, WEB-T10).
cat >/tmp/web_get.py <<'PY'
import http.client, sys
conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=5)
conn.request("GET", sys.argv[1], headers={"Accept-Encoding": "identity"})
answer = conn.getresponse()
body = answer.read().decode("utf-8", "replace")
print("status:", answer.status)
for name in ("content-type", "cache-control", "content-security-policy", "x-frame-options",
             "x-content-type-options", "referrer-policy"):
    print(f"{name}: {answer.getheader(name)}")
print("body:", body[:20000])
PY
chmod 0644 /tmp/web_get.py
web_get() { PYTHONDONTWRITEBYTECODE=1 /opt/vakr/venv/bin/python3 /tmp/web_get.py "$@"; }
api_get_as() { local user=$1; shift; runuser -u "$user" -- env PYTHONDONTWRITEBYTECODE=1 /opt/vakr/venv/bin/python3 /tmp/api_get.py "$@" 2>&1; }

stage_debs
apt-get -qq update

section "first install starts and enables nothing (PKG-003, UPD-003)"
apt_get install "$DEB_N"
for unit in vakr.target vakr-collector.service vakr-api.service vakr-update.service \
    vakr-update.timer; do
    check "$unit loads cleanly" equals "$(prop LoadState "$unit")" loaded
done
check "units verify under the running systemd" \
    systemd-analyze verify vakr.target vakr-collector.service vakr-api.service \
    vakr-update.service vakr-update.timer
check "vakr-collector.service is disabled" \
    equals "$(systemctl is-enabled vakr-collector.service)" disabled
check "vakr-api.service is disabled" equals "$(systemctl is-enabled vakr-api.service)" disabled
check "vakr-update.timer is disabled" equals "$(systemctl is-enabled vakr-update.timer)" disabled
check "vakr-update.timer is not running" equals "$(systemctl is-active vakr-update.timer)" inactive
check "vakr.target is not running" equals "$(systemctl is-active vakr.target)" inactive

section "collector idles on an empty inventory, reloads, and stops cleanly (6.2, SVC-T01, SVC-T03)"
check "vakr-collector.service starts (READY=1 after its preflight)" \
    systemctl start vakr-collector.service
check "collector is active" equals "$(systemctl is-active vakr-collector.service)" active
check "collector runs as vakr" equals "$(prop User vakr-collector.service)" vakr
check "collector holds no raw-socket capability (ICMP-T07)" \
    bash -c '! systemctl show -p CapabilityBoundingSet --value vakr-collector.service | grep -qi raw'
check "systemctl reload vakr-collector" systemctl reload vakr-collector.service
sleep 2
check "collector is still active after reload" \
    equals "$(systemctl is-active vakr-collector.service)" active
check "systemctl stop vakr-collector" systemctl stop vakr-collector.service
check "collector exited 0" equals "$(prop ExecMainStatus vakr-collector.service)" 0
check "journal records a clean stop" \
    grep -q 'collector.stopped' <<<"$(journalctl -u vakr-collector.service --no-pager)"
check "collector registered its spool" test -d /var/lib/vakr/spool

section "operator applies an inventory as root; database files stay vakr-owned (PKG-003, META-008)"
community="ci$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
printf '[credentials.lab-v2c]\nversion = "2c"\ncommunity = "%s"\n' "$community" \
    >/etc/vakr/secrets/credentials.toml
chown root:vakr /etc/vakr/secrets/credentials.toml
chmod 0640 /etc/vakr/secrets/credentials.toml
printf '[[devices]]\nname = "lab-rtr1"\naddress = "192.0.2.1"\ncredential = "lab-v2c"\n' \
    >/etc/vakr/inventory.toml
check "vakr inventory validate" vakr inventory validate
check "vakr inventory apply (run as root)" vakr inventory apply
check "the device is in the inventory" grep -q lab-rtr1 <<<"$(vakr inventory list)"
check "metadata database files stay vakr-owned" \
    bash -c '! find /var/lib/vakr -maxdepth 1 -name "metadata.db*" ! -user vakr | grep -q .'
check "vakr status shows the device" grep -q lab-rtr1 <<<"$(vakr status --json)"
check "the community never appears in command output" bash -c \
    "! { vakr inventory list; vakr inventory plan; vakr status --json; } 2>&1 | grep -qF '$community'"

section "API serves loopback HTTP and the local socket; strangers are refused (6.2, E.12)"
check "vakr-api.service starts (READY=1 after its startup checks)" systemctl start vakr-api.service
check "API is active" equals "$(systemctl is-active vakr-api.service)" active
check "API runs as vakr" equals "$(prop User vakr-api.service)" vakr
check "API holds no capability (15.3)" \
    bash -c '! systemctl show -p CapabilityBoundingSet --value vakr-api.service | grep -qi cap_'
check "loopback /api/v1/health/live answers 200 without auth" \
    grep -q '^200 ' <<<"$(api_get /api/v1/health/live tcp)"
check "loopback data needs a role (401 without a session)" \
    grep -q '^401 ' <<<"$(api_get /api/v1/devices tcp)"

section "the web UI is served same-origin with its CSP and cache headers (ADR-035, WEB-T10)"
check "the package installs the web build and its manifest" \
    test -f /opt/vakr/share/web/index.html -a -f /opt/vakr/share/web/vakr-web.json
check "the bundled packages' licence texts are installed" \
    test -f /opt/vakr/share/doc/web-licenses/echarts/NOTICE
check "the API read the web build at start" \
    grep -q 'api.web_ready' <<<"$(journalctl -u vakr-api.service --no-pager)"
index=$(web_get /)
check "the app's index answers 200 as HTML" \
    bash -c 'grep -q "^status: 200" <<<"$1" && grep -q "^content-type: text/html; charset=utf-8" <<<"$1"' _ "$index"
check "the index revalidates (Cache-Control: no-cache)" grep -q '^cache-control: no-cache$' <<<"$index"
check "the CSP allows the bootstrap only by its SHA-256 hash" \
    grep -qE "^content-security-policy: .*script-src 'self' 'sha256-[A-Za-z0-9+/]{43}='" <<<"$index"
check "the CSP has no unsafe-inline or unsafe-eval" \
    bash -c '! grep -qE "unsafe-(inline|eval)" <<<"$(grep "^content-security-policy" <<<"$1")"' _ "$index"
check "the CSP forbids framing, base URIs, and other origins" \
    grep -qE "^content-security-policy: .*connect-src 'self'.*base-uri 'none'.*frame-ancestors 'none'" <<<"$index"
check "the E.30 headers stay (DENY, nosniff, no-referrer)" \
    bash -c 'grep -q "^x-frame-options: DENY" <<<"$1" && grep -q "^x-content-type-options: nosniff" <<<"$1" && grep -q "^referrer-policy: no-referrer" <<<"$1"' _ "$index"
asset=$(grep -oE '/_app/immutable/[^"]+\.js' <<<"$index" | head -n 1)
check "the index references a hashed immutable asset" test -n "$asset"
immutable=$(web_get "$asset")
check "the immutable asset is cached for a year" \
    grep -q '^cache-control: public, max-age=31536000, immutable$' <<<"$immutable"
check "the immutable asset is JavaScript" grep -q '^content-type: text/javascript' <<<"$immutable"
check "a deep link gets the app (single-page fallback)" \
    grep -q '^status: 200' <<<"$(web_get /devices/0190a3c4-0000-7000-8000-00000000002a)"
check "an unknown API path keeps the JSON envelope" \
    grep -q '"code":"not_found"' <<<"$(web_get /api/v1/no-such-endpoint)"
check "the socket is /run/vakr/api.sock, mode 0666, in /run/vakr 0751 (E.30)" \
    bash -c '[ "$(stat -c %a /run/vakr/api.sock)" = 666 ] && [ "$(stat -c %a /run/vakr)" = 751 ]'
check "root on the socket is admin (E.12)" \
    grep -q '"role":"admin"' <<<"$(api_get /api/v1/auth/session unix)"
check "root on the socket gets /api/v1/health/live" grep -q '^200 ' <<<"$(api_get /api/v1/health/live unix)"
check "an unprivileged account is refused on the socket (403 peer_refused)" \
    grep -q '^403 .*peer_refused' <<<"$(api_get_as nobody /api/v1/health/live unix)"
check "create a vakr-view member" \
    useradd --system --no-create-home --shell /usr/sbin/nologin --groups vakr-view vakrview
check "a vakr-view member is operator on the socket (E.30 #16)" \
    grep -q '"role":"operator"' <<<"$(api_get_as vakrview /api/v1/auth/session unix)"
check "vakr status as a vakr-view member reads through the socket (E.29 #10)" \
    grep -q '^data: vakr-api socket$' <<<"$(runuser -u vakrview -- vakr status 2>&1)"
check "a vakr-view member cannot read /etc/vakr/secrets" \
    bash -c '! runuser -u vakrview -- ls /etc/vakr/secrets >/dev/null 2>&1'
check "vakr check health asks the API over its socket" grep -q 'api: ready' <<<"$(vakr check health)"
check "the API serves beside the running collector (SQLite WAL, both as vakr)" \
    systemctl start vakr-collector.service
sleep 2
check "collectors list shows the running collector" \
    grep -q '"position"' <<<"$(api_get /api/v1/collectors unix)"
check "metadata stays vakr-owned with both services running" \
    bash -c '! find /var/lib/vakr -maxdepth 1 -name "*.db*" ! -user vakr | grep -q .'
check "the collector's metrics snapshot is 0640 vakr:vakr in /run/vakr (E.33)" \
    bash -c '[ "$(stat -c "%a %U %G" /run/vakr/collector-metrics.json)" = "640 vakr vakr" ]'
metrics=$(api_get /api/v1/metrics unix)
check "metrics over the socket report the running collector (OBS-001, E.33)" \
    grep -q '^vakr_collector_metrics_up{reason="ok"} 1$' <<<"$metrics"
check "metrics carry both processes and the inventory (OBS-T01)" \
    bash -c 'grep -q "process=\"collector\"" <<<"$1" && grep -q "process=\"api\"" <<<"$1" &&
             grep -q "^vakr_state_metrics_up{reason=\"ok\"} 1$" <<<"$1"' _ "$metrics"
check "metrics refuse an anonymous TCP caller (OBS-T02)" \
    grep -q '^401 ' <<<"$(api_get /api/v1/metrics tcp)"
systemctl stop vakr-collector.service
check "a clean collector stop removes its metrics snapshot (E.33)" \
    test ! -e /run/vakr/collector-metrics.json
check "systemctl stop vakr-api" systemctl stop vakr-api.service
check "API exited 0" equals "$(prop ExecMainStatus vakr-api.service)" 0
check "the socket is removed at stop" test ! -e /run/vakr/api.sock
check "journal records a clean API stop" \
    grep -q 'api.stopped' <<<"$(journalctl -u vakr-api.service --no-pager)"
check "the auth key exists, 0600 vakr (E.12)" \
    bash -c '[ "$(stat -c "%a %U" /var/lib/vakr/auth.key)" = "600 vakr" ]'

section "operator enables the daily update timer (UPD-003)"
check "systemctl enable --now vakr-update.timer" systemctl enable --now vakr-update.timer
check "timer is active" equals "$(systemctl is-active vakr-update.timer)" active
# systemd normalises OnCalendar=daily to this calendar expression.
check "timer runs daily" grep -qF 'OnCalendar=*-*-* 00:00:00' <<<"$(prop TimersCalendar vakr-update.timer)"
check "timer delay is randomised up to 4h" \
    equals "$(prop RandomizedDelayUSec vakr-update.timer)" 4h
check "timer is persistent" equals "$(prop Persistent vakr-update.timer)" yes
check "timer triggers vakr-update.service" \
    equals "$(prop Triggers vakr-update.timer)" vakr-update.service
check "update service is a oneshot" equals "$(prop Type vakr-update.service)" oneshot

section "upgrade restarts vakr.target only if it was running (PKG-003)"
systemctl start vakr.target
started=$(active_since)
apt_get install "$DEB_N1"
check "upgrade to $DEBVER_N1 restarted the running vakr.target" test "$(active_since)" -gt "$started"
check "timer stays enabled across the upgrade" \
    equals "$(systemctl is-enabled vakr-update.timer)" enabled
systemctl stop vakr.target
apt_get install --reinstall "$DEB_N1"
check "upgrade leaves a stopped vakr.target stopped" \
    equals "$(systemctl is-active vakr.target)" inactive

section "remove stops units; purge removes enablement links (PKG-005)"
systemctl start vakr.target vakr-collector.service vakr-api.service
apt_get remove vakr
check "remove stopped the timer" equals "$(systemctl is-active vakr-update.timer)" inactive
check "remove stopped the collector" bash -c '! systemctl is-active --quiet vakr-collector.service'
check "remove stopped the API" bash -c '! systemctl is-active --quiet vakr-api.service'
check "remove stopped vakr.target" equals "$(systemctl is-active vakr.target)" inactive
apt_get purge vakr
check "purge removed the timer's enablement link" \
    test ! -e /etc/systemd/system/timers.target.wants/vakr-update.timer

finish "systemd test"
