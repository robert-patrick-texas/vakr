#!/bin/bash
# `make resilience-test`: the resilience system tests of MVP-ACC-06 (blueprint
# 27.6, Appendix E.36). Opt-in, never part of `make check`. Needs docker with
# privileged containers, uv, curl, git, ssh-keygen, and outbound HTTPS (Ubuntu
# archive, PyPI, and GitHub for S0).
#
#  1. Builds the .deb (or takes DEB), writes a simulated IOS XE fleet, and
#     starts snmpsim on one address per device (vakr-acc06-sim).
#  2. Boots a systemd ubuntu:26.04 node (vakr-acc06-node), installs the
#     package (with S0: the release before it, from GitHub), runs `vakr setup`
#     non-interactively, stores the credentials, applies the inventory (every
#     device at 30 s), and enables vakr-collector, vakr-api, and vakr.target
#     as the setup checklist says.
#  3. Runs the scenarios: a self-update to the package while collecting
#     (MVP-ACC-07, update.sh), a service restart, a host reboot, the telemetry
#     store unavailable, and an abrupt power loss; each checks
#     `vakr check health` and the status views at every stage.
#  4. Accounts for every poll the schedule expected (accounting.py): each is
#     stored, or falls where the collector was not running, or inside the
#     E.8 window of an abrupt stop that recorded a telemetry-loss incident.
#     Anything else is silent loss, and the run fails.
#
# Environment:
#   DEB                an already-built package (default: build one)
#   RESIL_DEVICES      simulated devices, 2 to 50 (default 8)
#   RESIL_OUTAGE       seconds the telemetry store stays unavailable (default 180)
#   RESIL_SCENARIOS    comma list of update, restart, reboot, storage, power (default all)
#   RESIL_FROM         the release S0 updates from (default: the newest published
#                      release before the package)
#   RESIL_KEEP         1 keeps the containers after the run, for debugging
#   RESIL_NET_PREFIX   the device network's first three octets (default 172.29.86.)
# See docs/development/resilience-tests.md.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../../.." || exit 2
# shellcheck source=packaging/container/testlib.sh
. packaging/container/testlib.sh
# shellcheck source=tests/system/resilience/lib.sh
. tests/system/resilience/lib.sh
# shellcheck source=tests/system/resilience/update.sh
. tests/system/resilience/update.sh

devices=${RESIL_DEVICES:-8}
outage=${RESIL_OUTAGE:-180}
scenarios=${RESIL_SCENARIOS:-update,restart,reboot,storage,power}
wants() { [[ ",$scenarios," == *",$1,"* ]]; }

cleanup() {
    if [ "$failures" -gt 0 ]; then collect_logs; fi
    if [ "${RESIL_KEEP:-0}" = 1 ]; then echo "resilience: containers kept (RESIL_KEEP=1)"; else remove_all; fi
}

section "setup: package, fleet, containers"
remove_all
mkdir -p "$WORK"
deb=${DEB:-}
if [ -z "$deb" ]; then
    packaging/build-deb.sh "$WORK" || exit 2
    deb=$(find "$WORK" -maxdepth 1 -name 'vakr_*_amd64.deb' | sort | tail -1)
fi
[ -f "$deb" ] || { echo "resilience: no package at '${deb}'"; exit 2; }
installed_deb=/work/vakr.deb
if wants update; then
    prepare_update "$deb" || exit 2
    installed_deb=/work/from/$FROM_DEB
fi
[ "$(realpath "$deb")" = "$(realpath "$WORK/vakr.deb" 2>/dev/null)" ] || cp "$deb" "$WORK/vakr.deb"
rm -rf "$WORK/fleet" "$WORK/wheels" "$WORK/logs" "$EVENTS" "$WORK/powercut.txt" "$WORK/polls.tsv" \
    "$WORK/incidents.jsonl" "$WORK/update-journal.json" "$WORK/update-runs.tsv"
uv run --quiet python -m tests.system.mpos.simdata "$WORK/fleet" \
    --devices "$devices" --prefix "$NET_PREFIX" --interval 30s || exit 2
mkdir -p "$WORK/wheels"
while read -r url digest; do
    wheel=$WORK/wheels/${url##*/}
    curl -fsSL -o "$wheel" "$url" && echo "$digest  $wheel" | sha256sum --check --quiet || exit 2
done <"$WORK/fleet/wheels.txt"
mapfile -t ips < <(cut -f2 "$WORK/fleet/devices.tsv")
DEVICE_COUNT=${#ips[@]}
export DEVICE_COUNT

trap cleanup EXIT
docker build --quiet --tag vakr-systemd-test - <packaging/container/Dockerfile.systemd >/dev/null
docker network create --subnet "${NET_PREFIX}0/24" "$NETWORK" >/dev/null ||
    { fail "network $NETWORK (${NET_PREFIX}0/24) is created"; exit 1; }
mounts=(--volume "$PWD/$WORK:/work:ro" --volume "$PWD/tests/system/resilience:/acc06:ro")

docker run --detach --name "$SIM" --network "$NETWORK" --ip "$SIM_IP" --cap-add NET_ADMIN \
    "${mounts[@]}" ubuntu:26.04 sleep infinity >/dev/null
endpoints=$(printf -- '--agent-udpv4-endpoint=%s:161 ' "${ips[@]}")
on_sim "export DEBIAN_FRONTEND=noninteractive; apt-get -qq update && \
    apt-get -qq install -y --no-install-recommends iproute2 >/dev/null && \
    for ip in ${ips[*]}; do ip addr add \$ip/24 dev eth0; done && \
    dpkg-deb -x /work/vakr.deb / && mkdir -p /sim/lib /sim/cache && cp -r /work/fleet/sim/data /sim/ && \
    for w in /work/wheels/*.whl; do $PY -m zipfile -e \$w /sim/lib; done && \
    chown -R nobody:nogroup /sim" ||
    { fail "the simulator container is set up"; exit 1; }
docker exec --detach "$SIM" bash -c "cd /sim && PYTHONPATH=/sim/lib \
    $PY -c 'import sys; from snmpsim.commands.responder import main; sys.exit(main())' \
    --data-dir=/sim/data --cache-dir=/sim/cache $endpoints \
    --process-user=nobody --process-group=nogroup --log-level=error >/sim/snmpsim.log 2>&1"
sim_listening() { [ "$(docker exec "$SIM" ss -Hlun 'sport = :161' | grep -c .)" -ge "$DEVICE_COUNT" ]; }
check "snmpsim listens on all $DEVICE_COUNT device addresses" wait_until 120 sim_listening

# Boot, install, and first-run setup the way docs/operations/install.md describes.
docker run --detach --name "$NODE" --hostname acc06 --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock --network "$NETWORK" --ip "$NODE_IP" \
    "${mounts[@]}" vakr-systemd-test >/dev/null
check "the node boots systemd" wait_systemd
check "the package installs (${installed_deb##*/})" on_sh "export DEBIAN_FRONTEND=noninteractive; \
    apt-get -qq update && cp $installed_deb /tmp/vakr.deb && chmod 0644 /tmp/vakr.deb && \
    apt-get -qq install -y --no-install-recommends /tmp/vakr.deb >/tmp/apt.log 2>&1"
# The password is made here, piped in, and never printed or written to a file.
password="acc06-$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
check "vakr setup creates the first admin non-interactively (SEC-005)" bash -c \
    "printf '%s\n%s\n' '$password' '$password' | docker exec -i $NODE vakr setup --username acc06-admin"
unset password
check "the credentials are stored 0640 root:vakr" \
    on install -m 0640 -o root -g vakr /work/fleet/credentials.toml /etc/vakr/secrets/credentials.toml
check "vakr credentials check" on vakr credentials check
check "the inventory is applied" on_sh "install -m 0644 /work/fleet/inventory.toml /etc/vakr/inventory.toml \
    && vakr inventory apply"
check "vakr-collector, vakr-api, and vakr.target are enabled and started" \
    on systemctl enable --now vakr-collector.service vakr-api.service vakr.target
[ "$failures" -eq 0 ] || exit 1
check "the first collector run is logged" record_start "install"

section "warm-up: every device polled"
check "all $DEVICE_COUNT devices Active" wait_until 420 all_active
event window_start "$(now_us)" "after warm-up"

# shellcheck source=tests/system/resilience/scenarios.sh
. tests/system/resilience/scenarios.sh
baseline
wants update && scenario_update
wants restart && scenario_restart
wants reboot && scenario_reboot
wants storage && scenario_storage "$outage"
wants power && scenario_power
accounting
finish resilience-test
