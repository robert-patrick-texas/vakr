# Helpers for tests/system/mpos/run.sh, the two-position system tests
# (MPOS-T01..T04, blueprint 27.5). Sourced after packaging/container/testlib.sh,
# whose pass/fail/check/section/finish this file reuses.
#
# Containers and networks have fixed names, so cleanup never needs a variable:
#   vakr-mpos-east, vakr-mpos-west  systemd-booted nodes, on both networks
#   vakr-mpos-sim                   snmpsim answering for every simulated device
#   vakr-mpos-inter                 the inter-position network, 172.29.70.0/24
#   vakr-mpos-dev                   the device network, 172.29.71.0/24
# shellcheck shell=bash

# shellcheck disable=SC2034  # these are run.sh's and phases.sh's too
INTER_PREFIX=172.29.70.
DEV_PREFIX=172.29.71.
EAST_INTER=${INTER_PREFIX}20
WEST_INTER=${INTER_PREFIX}30
EAST_DEV=${DEV_PREFIX}20
WEST_DEV=${DEV_PREFIX}30
SIM_DEV=${DEV_PREFIX}2
PROBE=(/opt/vakr/venv/bin/python /mpos/probe.py)

skip() { printf 'SKIP  %s (%s)\n' "$1" "$2"; }
indent() { while IFS= read -r line; do printf '        %s\n' "$line"; done; }
has_feature() { [[ ",${MPOS_FEATURES:-}," == *",$1,"* ]]; }
gated() {
    # gated FEATURE WHY WHAT CMD...: run the check only when FEATURE is enabled.
    local feature=$1 why=$2 what=$3
    shift 3
    if has_feature "$feature"; then check "$what" "$@"; else skip "$what" "$why"; fi
}

container_of() {
    case $1 in
        east) echo vakr-mpos-east ;;
        west) echo vakr-mpos-west ;;
        sim) echo vakr-mpos-sim ;;
        *) return 1 ;;
    esac
}
on() {
    # on NODE CMD...: run a command in a container.
    local name
    name=$(container_of "$1") || return 2
    shift
    docker exec "$name" "$@"
}
on_sh() { on "$1" bash -c "$2"; }
on_in() {
    # on_in NODE CMD...: like on, with this shell's stdin passed to the command.
    local name
    name=$(container_of "$1") || return 2
    shift
    docker exec --interactive "$name" "$@"
}

remove_all() {
    docker rm --force vakr-mpos-east vakr-mpos-west vakr-mpos-sim >/dev/null 2>&1
    docker network rm vakr-mpos-inter vakr-mpos-dev >/dev/null 2>&1
    return 0
}

wait_systemd() {
    local state=""
    for _ in $(seq 90); do
        state=$(on "$1" systemctl is-system-running 2>/dev/null || true)
        case $state in running | degraded) return 0 ;; esac
        sleep 1
    done
    echo "mpos: $1: systemd did not come up (state: ${state:-none})"
    return 1
}

iface_for() {
    # iface_for NODE PREFIX: the interface holding the node's address in that network.
    on_sh "$1" "ip -o -4 addr show | awk '\$4 ~ /^${2//./\\.}/ {print \$2; exit}'"
}

wait_until() {
    # wait_until SECONDS CMD...: retry CMD every 10 s until it succeeds or time runs out.
    local deadline=$((SECONDS + $1))
    shift
    until "$@" >/dev/null 2>&1; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 10
    done
}

hold() {
    # hold SECONDS WHAT: wait, saying so once a minute.
    local left=$1
    echo "mpos: holding $2 for $left s"
    while [ "$left" -gt 0 ]; do
        sleep "$((left < 60 ? left : 60))"
        left=$((left - 60))
        [ "$left" -gt 0 ] && echo "mpos: $2: $left s left"
    done
    return 0
}

# --- what a node reports -------------------------------------------------------

status_of() { on "$1" "${PROBE[@]}" status; }
all_active() {
    # all_active NODE: every simulated device is listed and Active.
    local lines
    lines=$(status_of "$1") || return 1
    [ "$(grep -c . <<<"$lines")" -eq "$DEVICE_COUNT" ] && ! awk -F'\t' '$2 != "active"' <<<"$lines" | grep -q .
}
device_line() { status_of "$1" | awk -F'\t' -v n="$2" '$1 == n'; }
own_label() {
    # own_label NODE DEVICE: that node's own position's label for a device.
    device_line "$1" "$2" | cut -f4 | tr ';' '\n' | sed -n "s/^$1=//p"
}
composite_label() { device_line "$1" "$2" | cut -f3; }
poll_rows() {
    # poll_rows NODE: obs_poll rows of the node's own collector (its hostname).
    local own
    own=$(on "$1" "${PROBE[@]}" rows) || return 1
    awk -F'\t' '$1 == "obs_poll" {sum += $3} END {print sum + 0}' <<<"$own"
}

# --- partitions and delay ------------------------------------------------------

nft_drop() {
    # nft_drop NODE TABLE ADDR...: drop every packet arriving from these addresses.
    # Only the input hook: requests still leave and nothing comes back, so the
    # collector sees timeouts, as across a real partition. Dropping in the output
    # hook makes sends fail locally, which vakr rightly reports as its own
    # collector degraded (OBS-003) for every device, not as the cut ones down.
    local node=$1 table=$2
    shift 2
    local set
    set=$(IFS=,; echo "$*")
    on_sh "$node" "nft add table inet $table && \
        nft 'add chain inet $table in { type filter hook input priority 0; policy accept; }' && \
        nft add rule inet $table in ip saddr '{ $set }' drop"
}
nft_clear() { on_sh "$1" "nft delete table inet $2 2>/dev/null || true"; }

cut_nodes() {
    nft_drop east mpos_nodes "$WEST_INTER" "$WEST_DEV" && nft_drop west mpos_nodes "$EAST_INTER" "$EAST_DEV"
}
heal_nodes() { nft_clear east mpos_nodes && nft_clear west mpos_nodes; }
cut_west_devices() { nft_drop west mpos_devices "${CUT_IPS[@]}"; }
heal_west_devices() { nft_clear west mpos_devices; }

delay() {
    # delay NODE PREFIX MS: netem delay on the node's interface in that network.
    local dev
    dev=$(iface_for "$1" "$2") && [ -n "$dev" ] || return 1
    on "$1" tc qdisc replace dev "$dev" root netem delay "${3}ms"
}

# --- roles and replication (M5 parts A and B) ----------------------------------

configure_roles() {
    # The contract part A builds to (ADR-024): east is the metadata primary and
    # west the peer; each node pulls the other with a replication token that the
    # node it pulls from issued, kept in /etc/vakr/secrets/replication.token.
    # Adjust here when part A's operator guide lands (how vakr-api is reached
    # over HTTPS between nodes, and the token command's exact output).
    printf '[node]\nrole = "primary"\nposition = "east"\n' >dist/mpos-test/east-role.toml
    printf '[node]\nrole = "peer"\nposition = "west"\nprimary_url = "https://%s"\n' "$EAST_INTER" \
        >dist/mpos-test/west-role.toml
    on east install -D -m 0644 /work/east-role.toml /etc/vakr/conf.d/50-mpos-role.toml &&
        on west install -D -m 0644 /work/west-role.toml /etc/vakr/conf.d/50-mpos-role.toml || return 1
    issue_token east west && issue_token west east
}
issue_token() {
    # issue_token ISSUER HOLDER: a token from ISSUER, installed on HOLDER, never printed.
    local token
    token=$(on "$1" vakr replication token create --label "mpos-$2" | grep -o 'vkr_[A-Za-z0-9_-]*' | head -1)
    [ -n "$token" ] || return 1
    printf '%s\n' "$token" |
        on_in "$2" install -m 0640 -o root -g vakr /dev/stdin /etc/vakr/secrets/replication.token
}

# --- failure logs --------------------------------------------------------------

collect_logs() {
    local dir=dist/mpos-test/logs
    mkdir -p "$dir"
    for node in east west; do
        on "$node" journalctl -b --no-pager >"$dir/$node-journal.txt" 2>&1
        on "$node" vakr status --json >"$dir/$node-status.json" 2>&1
        on "$node" "${PROBE[@]}" rows >"$dir/$node-rows.tsv" 2>&1
        on "$node" nft list ruleset >"$dir/$node-nft.txt" 2>&1
    done
    on sim cat /sim/snmpsim.log >"$dir/sim-snmpsim.log" 2>&1
    echo "mpos: logs in $dir"
}
