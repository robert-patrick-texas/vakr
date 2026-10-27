# Helpers for tests/system/resilience/run.sh, the resilience system tests
# (MVP-ACC-06, blueprint 27.6, Appendix E.36). Sourced after
# packaging/container/testlib.sh, whose pass/fail/check/section/finish this
# file reuses.
#
# Every container and network is named with the prefix vakr-acc06-, so cleanup
# never needs a variable:
#   vakr-acc06-node   the systemd-booted vakr node (vakr-collector, vakr-api)
#   vakr-acc06-sim    snmpsim answering for every simulated device
#   vakr-acc06-net    the device network, 172.29.86.0/24 (mpos uses .70 and .71)
# shellcheck shell=bash

# shellcheck disable=SC2034  # these are run.sh's and scenarios.sh's too
NET_PREFIX=${RESIL_NET_PREFIX:-172.29.86.}
NODE_IP=${NET_PREFIX}20
SIM_IP=${NET_PREFIX}2
NODE=vakr-acc06-node
SIM=vakr-acc06-sim
NETWORK=vakr-acc06-net
PY=/opt/vakr/venv/bin/python
WORK=dist/resilience-test
EVENTS=$WORK/events.tsv

skip() { printf 'SKIP  %s (%s)\n' "$1" "$2"; }
note() { printf '      %s\n' "$*"; }
indent() { while IFS= read -r line; do printf '        %s\n' "$line"; done; }
now_us() { date +%s%6N; }

on() { docker exec "$NODE" "$@"; }
on_sh() { docker exec "$NODE" bash -c "$1"; }
on_sim() { docker exec "$SIM" bash -c "$1"; }
probe() { docker exec "$NODE" "$PY" /acc06/probe.py "$@"; }
# The telemetry and spool reads run as vakr, so nothing they open becomes root's.
probe_vakr() { docker exec "$NODE" runuser -u vakr -- "$PY" /acc06/probe.py "$@"; }

remove_all() {
    docker rm --force "$NODE" "$SIM" >/dev/null 2>&1
    docker network rm "$NETWORK" >/dev/null 2>&1
    return 0
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$NODE" 2>/dev/null)" = true ]; }

wait_systemd() {
    local state=""
    for _ in $(seq 120); do
        state=$(on systemctl is-system-running 2>/dev/null || true)
        case $state in running | degraded) return 0 ;; esac
        sleep 1
    done
    echo "resilience: systemd did not come up (state: ${state:-none})"
    return 1
}

wait_until() {
    # wait_until SECONDS CMD...: retry CMD every 5 s until it succeeds or time runs out.
    local deadline=$((SECONDS + $1))
    shift
    until "$@" >/dev/null 2>&1; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 5
    done
}

hold() {
    # hold SECONDS WHAT: wait, saying so once a minute.
    local left=$1
    echo "resilience: holding $2 for $left s"
    while [ "$left" -gt 0 ]; do
        sleep "$((left < 60 ? left : 60))"
        left=$((left - 60))
        [ "$left" -gt 0 ] && echo "resilience: $2: $left s left"
    done
    return 0
}

# --- the run log the accounting reads (accounting.py) ---------------------------

event() {
    # event KIND ARGS...: append one tab-separated line to the run log.
    local IFS=$'\t'
    echo "$*" >>"$EVENTS"
}
collector_up() { [ "$(on systemctl is-active vakr-collector.service 2>/dev/null)" = active ]; }
api_up() { [ "$(on systemctl is-active vakr-api.service 2>/dev/null)" = active ]; }
invocation() { on systemctl show --property InvocationID --value vakr-collector.service 2>/dev/null; }
record_start() {
    # record_start LABEL [OLD_INVOCATION]: wait for a new collector run, then log when it
    # became ready (READY=1, systemd's ActiveEnterTimestamp) as a run start.
    local label=$1 old=${2:-} started
    wait_until 180 collector_up || return 1
    if [ -n "$old" ]; then
        wait_until 60 bash -c "[ \"\$(docker exec $NODE systemctl show -P InvocationID \
            vakr-collector.service)\" != '$old' ]" || return 1
    fi
    started=$(probe started) || return 1
    event run_start "$started" "$label"
}
record_end() { event run_end "$(now_us)" "$1" "$2"; }

# --- what the node reports -----------------------------------------------------

status_lines() { probe status; }
all_active() {
    # all_active: every simulated device is listed and Active.
    local lines
    lines=$(status_lines) || return 1
    [ "$(grep -c . <<<"$lines")" -eq "$DEVICE_COUNT" ] && ! awk -F'\t' '$2 != "active"' <<<"$lines" | grep -q .
}
none_down() {
    # none_down: no device, and no position of a device, is labelled Down (invariant 2).
    local lines
    lines=$(status_lines) || return 1
    [ -n "$lines" ] && ! grep -qi 'down' <<<"$lines"
}
all_unknown_not_down() {
    # all_unknown_not_down: every device reads "Unknown — ..." (E.2 row 3), none Down.
    local lines
    lines=$(status_lines) || return 1
    none_down && ! awk -F'\t' '$3 !~ /^Unknown/' <<<"$lines" | grep -q .
}
health_line() { on vakr check health 2>/dev/null; }
check_part() {
    # check_part NAME: that check's line of `vakr doctor`, "[STATUS  ] name: summary". The
    # single Nagios line of `vakr check health` cannot be split: summaries contain ';'.
    on vakr doctor 2>/dev/null | grep -E "^\[[A-Z]+ *\] $1: " | head -1
}
health_status() { on vakr check health >/dev/null 2>&1; echo $?; }
spool_says() {
    # spool_says TEXT: the Nagios line of `vakr check health` and the spool check say TEXT.
    health_line | grep -qF -- "$1" && check_part spool | grep -qF -- "$1"
}
spool_quiet() {
    # spool_quiet: the spool check is at most a recent-incident warning, nothing waits long.
    local part
    part=$(check_part spool) && [ -n "$part" ] || return 1
    ! grep -qE 'not accepting writes|ingestion behind|CRITICAL|exhausted' <<<"$part"
}

# --- failure logs --------------------------------------------------------------

collect_logs() {
    local dir=$WORK/logs
    mkdir -p "$dir"
    if running; then
        on journalctl -b --no-pager >"$dir/node-journal.txt" 2>&1
        on vakr status --json >"$dir/node-status.json" 2>&1
        on vakr check health >"$dir/node-health.txt" 2>&1
        probe_vakr incidents >"$dir/node-incidents.jsonl" 2>&1
    fi
    docker logs "$SIM" >"$dir/sim.log" 2>&1
    echo "resilience: logs in $dir"
}
