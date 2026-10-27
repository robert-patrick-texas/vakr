#!/bin/bash
# `make mpos-test`: the two-position system tests MPOS-T01..T05 (blueprint 27.5,
# ADR-024). Opt-in, never part of `make check`. Needs docker with privileged
# containers, uv, curl, and outbound HTTPS (Ubuntu archive, PyPI for snmpsim).
#
#  1. Builds the .deb (or takes DEB), writes a simulated IOS XE fleet, and
#     starts snmpsim on one address per device (vakr-mpos-sim).
#  2. Boots two systemd ubuntu:26.04 nodes, east and west, on an inter-position
#     network and a device network; installs the package; applies the same
#     inventory; starts vakr-collector and vakr-api.
#  3. Adds 30 ms netem each way between the nodes (60 ms RTT) and 60 ms on
#     west's path to the devices, then runs MPOS-T04, T01, T02, T03, and T05
#     (losing the primary: promote, two primaries, demote; MPOS_FEATURES=promote).
#
# Checks that need M5 parts A (node roles, replication credential) or B
# (observation replication) run only when MPOS_FEATURES names them, and print
# SKIP with the reason otherwise. See docs/development/two-position-tests.md.
#
# Environment:
#   DEB                     an already-built package (default: build one)
#   MPOS_QUICK              1: 30 s polls, 120 s partitions (default 0: 60 s, 1800 s)
#   MPOS_PARTITION_SECONDS  MPOS-T01's partition (default 1800, quick 120)
#   MPOS_HOLD_SECONDS       T01's composite and T03's windows (default 300, quick 120)
#   MPOS_DEVICES            simulated devices, 2 to 200 (default 8)
#   MPOS_FEATURES           comma list: roles, replication, promote (default none)
#   MPOS_KEEP               1 keeps the containers after the run, for debugging
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../../.." || exit 2
# shellcheck source=packaging/container/testlib.sh
. packaging/container/testlib.sh
# shellcheck source=tests/system/mpos/lib.sh
. tests/system/mpos/lib.sh

if [ "${MPOS_QUICK:-0}" = 1 ]; then interval=30s; partition=120; window=120
else interval=60s; partition=1800; window=300; fi
partition=${MPOS_PARTITION_SECONDS:-$partition}
window=${MPOS_HOLD_SECONDS:-$window}
devices=${MPOS_DEVICES:-8}
work=dist/mpos-test

cleanup() {
    if [ "$failures" -gt 0 ]; then collect_logs; fi
    if [ "${MPOS_KEEP:-0}" = 1 ]; then echo "mpos: containers kept (MPOS_KEEP=1)"; else remove_all; fi
}

section "setup: package, fleet, containers"
remove_all
mkdir -p "$work"
deb=${DEB:-}
if [ -z "$deb" ]; then
    packaging/build-deb.sh "$work" || exit 2
    deb=$(find "$work" -maxdepth 1 -name 'vakr_*_amd64.deb' | sort | tail -1)
fi
[ -f "$deb" ] || { echo "mpos: no package at '${deb}'"; exit 2; }
cp "$deb" "$work/vakr.deb"
rm -rf dist/mpos-test/fleet
uv run --quiet python -m tests.system.mpos.simdata "$work/fleet" \
    --devices "$devices" --prefix "$DEV_PREFIX" --interval "$interval" || exit 2
rm -rf dist/mpos-test/wheels
mkdir -p "$work/wheels"
while read -r url digest; do
    wheel=$work/wheels/${url##*/}
    curl -fsSL -o "$wheel" "$url" && echo "$digest  $wheel" | sha256sum --check --quiet || exit 2
done <"$work/fleet/wheels.txt"
mapfile -t names < <(cut -f1 "$work/fleet/devices.tsv")
mapfile -t ips < <(cut -f2 "$work/fleet/devices.tsv")
DEVICE_COUNT=${#names[@]}
half=$((DEVICE_COUNT / 2))
CUT_IPS=("${ips[@]:0:half}")
cut_names=("${names[@]:0:half}")
kept_names=("${names[@]:half}")
export DEVICE_COUNT

trap cleanup EXIT
docker build --quiet --tag vakr-systemd-test - <packaging/container/Dockerfile.systemd >/dev/null
docker network create --subnet "${INTER_PREFIX}0/24" vakr-mpos-inter >/dev/null
docker network create --subnet "${DEV_PREFIX}0/24" vakr-mpos-dev >/dev/null
mounts=(--volume "$PWD/$work:/work:ro" --volume "$PWD/tests/system/mpos:/mpos:ro")

docker run --detach --name vakr-mpos-sim --privileged --network vakr-mpos-dev --ip "$SIM_DEV" \
    "${mounts[@]}" ubuntu:26.04 sleep infinity >/dev/null
endpoints=$(printf -- '--agent-udpv4-endpoint=%s:161 ' "${ips[@]}")
on_sh sim "export DEBIAN_FRONTEND=noninteractive; apt-get -qq update && \
    apt-get -qq install -y --no-install-recommends iproute2 >/dev/null && \
    for ip in ${ips[*]}; do ip addr add \$ip/24 dev eth0; done && \
    dpkg-deb -x /work/vakr.deb / && mkdir -p /sim/lib /sim/cache && cp -r /work/fleet/sim/data /sim/ && \
    for w in /work/wheels/*.whl; do /opt/vakr/venv/bin/python -m zipfile -e \$w /sim/lib; done && \
    chown -R nobody:nogroup /sim" ||
    { fail "the simulator container is set up"; exit 1; }
docker exec --detach vakr-mpos-sim bash -c "cd /sim && PYTHONPATH=/sim/lib \
    /opt/vakr/venv/bin/python -c 'import sys; from snmpsim.commands.responder import main; sys.exit(main())' \
    --data-dir=/sim/data --cache-dir=/sim/cache $endpoints \
    --process-user=nobody --process-group=nogroup --log-level=error >/sim/snmpsim.log 2>&1"
# The collectors back discovery off after a failure (60 s, doubling), so snmpsim
# must listen on every address before they start.
sim_listening() { [ "$(on sim ss -Hlun 'sport = :161' | grep -c .)" -ge "$DEVICE_COUNT" ]; }
check "snmpsim listens on all $DEVICE_COUNT device addresses" wait_until 120 sim_listening

start_node() {
    local node=$1 inter=$2 dev=$3 name
    name=$(container_of "$node")
    docker run --detach --name "$name" --hostname "$node" --privileged --cgroupns=private \
        --tmpfs /run --tmpfs /run/lock --network vakr-mpos-inter --ip "$inter" \
        "${mounts[@]}" vakr-systemd-test >/dev/null &&
        docker network connect --ip "$dev" vakr-mpos-dev "$name" && wait_systemd "$node" &&
        on_sh "$node" "export DEBIAN_FRONTEND=noninteractive; apt-get -qq update && \
            cp /work/vakr.deb /tmp/ && chmod 0644 /tmp/vakr.deb && \
            apt-get -qq install -y --no-install-recommends nftables iproute2 /tmp/vakr.deb >/tmp/apt.log 2>&1" &&
        on "$node" install -D -m 0644 "/work/$node-position.toml" /etc/vakr/conf.d/40-mpos-position.toml &&
        on "$node" install -m 0640 -o root -g vakr /work/fleet/credentials.toml \
            /etc/vakr/secrets/credentials.toml &&
        on "$node" install -m 0644 /work/fleet/inventory.toml /etc/vakr/inventory.toml &&
        # With roles, west is a peer: it starts empty and copies east's inventory.
        if has_feature roles && [ "$node" = west ]; then return 0; fi &&
        on "$node" vakr inventory apply >/dev/null
}
for node in east west; do
    printf '[node]\nposition = "%s"\n' "$node" >"$work/$node-position.toml"
done
for node in east west; do
    if [ "$node" = east ]; then start_node east "$EAST_INTER" "$EAST_DEV"
    else start_node west "$WEST_INTER" "$WEST_DEV"; fi
    check "$node: package installed and the fleet applied" test $? -eq 0
done
if has_feature roles; then check "roles: east primary, west peer, tokens exchanged" configure_roles
else skip "node roles and replication tokens" "needs node roles, M5 part A"; fi
for node in east west; do
    check "$node: vakr-collector and vakr-api start" \
        on "$node" systemctl enable --now vakr-api.service vakr-collector.service vakr.target
done
inter_delays() { delay east "$INTER_PREFIX" 30 && delay west "$INTER_PREFIX" 30; }
check "60 ms RTT between the positions (netem 30 ms each way)" inter_delays
check "west's path to the devices delayed 60 ms" delay west "$DEV_PREFIX" 60
[ "$failures" -eq 0 ] || exit 1

section "warm-up: both nodes poll every device"
for node in east west; do
    check "$node shows all $DEVICE_COUNT devices Active" wait_until 600 all_active "$node"
done

# shellcheck source=tests/system/mpos/phases.sh
. tests/system/mpos/phases.sh
# shellcheck source=tests/system/mpos/promote.sh
. tests/system/mpos/promote.sh
mpos_t04
mpos_t01
mpos_t02
mpos_t03
mpos_t05
finish mpos-test
