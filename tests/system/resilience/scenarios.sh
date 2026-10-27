# The MVP-ACC-06 scenarios of tests/system/resilience/run.sh (blueprint 27.6,
# Appendix E.36). Sourced after lib.sh, with DEVICE_COUNT set. Each scenario
# logs the collector's runs to the run log (record_end, record_start) so the
# final accounting can tell where it was running, and checks what
# `vakr check health` and the status views say at each stage.
# shellcheck shell=bash

INTERVAL_S=30
DBLOCK=acc06-dblock

# --- small helpers -------------------------------------------------------------

device_ids() { status_lines | cut -f5; }
sleep_until_us() {
    # sleep_until_us EPOCH_US: sleep until then (at once if it has passed).
    local s
    s=$(awk -v t="$1" -v n="$(now_us)" 'BEGIN { d = (t - n) / 1e6; if (d < 0) d = 0; printf "%.3f", d }')
    sleep "$s"
}
mid_poll() {
    # mid_poll: wait until a device's poll is under way (its slot + 0.3 s; ICMP takes ~0.8 s).
    local next
    # shellcheck disable=SC2046  # one argument per device ID
    next=$(probe_vakr next-slot $(device_ids)) || return 0
    sleep_until_us "$(awk -v t="$next" 'BEGIN { printf "%d", t + 300000 }')"
}
incident_count() { probe_vakr incidents | grep -c . || true; }
unclean_since() {
    # unclean_since EPOCH_US: an unclean_stop incident was detected after then.
    probe_vakr incidents | awk -v t="$1" '/"kind": "unclean_stop"/ {
        if (match($0, /"detected_us": [0-9]+/) && substr($0, RSTART + 15, RLENGTH - 15) + 0 >= t) ok = 1 }
        END { exit !ok }'
}
spool_field() {
    # spool_field N: field N of `probe.py spool` (1 durable_seq, 2 acked_seq, 3 available).
    probe_vakr spool | head -1 | cut -f"$1"
}
durable_grows() {
    # durable_grows SEQ: records keep reaching the spool past SEQ.
    [ "$(spool_field 1)" -gt "$1" ]
}
drained() {
    # drained: storage accepts writes and nothing older than two ingest intervals waits.
    local line
    line=$(probe_vakr spool | head -1) || return 1
    awk -F'\t' '$3 == "True" && ($4 == "None" || $4 + 0 < 20) {ok = 1} END {exit !ok}' <<<"$line"
}
health_is() { [ "$(health_status)" = "$1" ]; }
behind() {
    # behind: E.24's "ingestion behind": spool_oldest_s above six ingest intervals (60 s);
    # the summary names only the first problem and counts the rest ("+1 more").
    health_line | grep -q '(+[1-9] more)' &&
        health_line | grep -oE 'spool_oldest_s=[0-9.]+' | awk -F= '$2 + 0 > 60 {ok = 1} END {exit !ok}'
}
api_mentions() {
    # api_mentions PATH TEXT: GET PATH on the socket answers 2xx with TEXT in the body.
    probe api "$1" | grep -qF -- "$2"
}
last_start() { awk -F'\t' '$1 == "run_start" { t = $2 } END { print t }' "$EVENTS"; }
resumed() {
    # resumed: every device has a stored poll scheduled after the newest run started, so
    # collection really resumed (a status can still read Active from older data).
    local polls
    # shellcheck disable=SC2046  # one argument per device ID
    polls=$(probe_vakr polls "$(last_start)" "$(now_us)" $(device_ids)) || return 1
    [ "$(awk -F'\t' '$1 == "poll" { print $2 }' <<<"$polls" | sort -u | grep -c .)" -eq "$DEVICE_COUNT" ]
}
came_back_by_itself() {
    # came_back_by_itself: after a boot, systemd started the services (nothing here did).
    wait_systemd && wait_until 120 collector_up && wait_until 60 api_up
}
steady() {
    # steady WHAT: the end state every scenario must reach again.
    check "$1: every device has a poll stored since the collector came back" wait_until 120 resumed
    check "$1: every device Active again" wait_until 180 all_active
    check "$1: no device is Down (invariant 2)" none_down
    check "$1: vakr check health is OK again (exit 0)" wait_until 60 health_is 0
}

# --- the scenarios ---------------------------------------------------------------

baseline() {
    section "baseline: steady collection before any fault"
    check "vakr check health is OK (exit 0, OPS-001)" health_is 0
    check "the spool check reports nothing waiting long" spool_quiet
    check "no telemetry-loss incident yet" test "$(incident_count)" -eq 0
    check "no device is Down" none_down
    check "the collectors view reports the collector healthy" \
        api_mentions /api/v1/collectors '"value":"healthy"'
}

scenario_restart() {
    section "S1: service restart mid-collection (MVP-ACC-06, SVC-T01, SVC-T03, SVC-T04)"
    local old before
    old=$(invocation)
    before=$(incident_count)
    mid_poll
    record_end clean "service restart"
    check "systemctl restart vakr-collector vakr-api while a poll runs" \
        on systemctl restart vakr-collector.service vakr-api.service
    check "a new collector run starts" record_start "service restart" "$old"
    check "the stopped run logged a clean stop (16.4)" on_sh \
        "journalctl _SYSTEMD_INVOCATION_ID=$old --no-pager | grep -q collector.stopped"
    check "a clean stop records no telemetry-loss incident" test "$(incident_count)" -eq "$before"
    check "vakr-api is back and answers on its socket" wait_until 60 api_mentions /api/v1/health/ready ready
    steady "S1"
}

scenario_reboot() {
    section "S2: host reboot; the services come back by themselves (MVP-ACC-06, PKG-003)"
    local before
    before=$(incident_count)
    mid_poll
    record_end clean "host reboot"
    on systemctl reboot >/dev/null 2>&1
    # A container's systemd exits when it reboots; starting it again is the power coming back.
    check "the node shuts down cleanly within 120 s" timeout 130 docker wait "$NODE"
    check "the node starts again" docker start "$NODE"
    check "systemd starts vakr-collector and vakr-api by itself (enabled, vakr.target)" \
        came_back_by_itself
    check "the collector run after the boot is logged" record_start "boot after reboot"
    check "a clean reboot records no telemetry-loss incident" test "$(incident_count)" -eq "$before"
    steady "S2"
}

scenario_storage() {
    section "S3: telemetry store unavailable for $1 s (MVP-ACC-06, STORE-014, SPOOL-T03, SPOOL-T04)"
    local outage=$1 old seq
    check "a writer holds the telemetry database's lock (writes now fail)" \
        on systemd-run --unit "$DBLOCK" --property User=vakr "$PY" /acc06/faults.py lock \
        /var/lib/vakr/telemetry.db
    local started=$SECONDS
    # Five attempts with backoff take about 40 s before a cycle gives up and reports.
    check "check health: the spool reports the database not accepting writes" \
        wait_until 90 spool_says "not accepting writes"
    check "check health is WARNING (exit 1), not CRITICAL: storage trouble is vakr's own" health_is 1
    check "no device is Down while storage is unavailable (invariant 2)" none_down
    seq=$(spool_field 1)
    check "polling continues into the spool (STORE-014)" wait_until 60 durable_grows "$seq"
    old=$(invocation)
    record_end clean "restart during the outage"
    check "the collector restarts while the database is locked" on systemctl restart vakr-collector.service
    check "the restarted collector runs (its preflight only reads the schema)" \
        record_start "restart during the outage" "$old"
    seq=$(spool_field 1)
    check "the restarted collector keeps polling into the spool" wait_until 60 durable_grows "$seq"
    hold "$((outage - (SECONDS - started) > 0 ? outage - (SECONDS - started) : 0))" "the outage"
    check "check health: ingestion behind (oldest record past 60 s, a second spool problem)" \
        behind
    check "no device is Down at the end of the outage" none_down
    check "every device reads Unknown — not Down — once its data is stale (E.2 row 3)" \
        all_unknown_not_down
    check "the database is available again" on systemctl stop "$DBLOCK"
    check "the backlog drains (SPOOL-T04)" wait_until 180 drained
    check "check health: the spool no longer reports trouble" wait_until 60 spool_quiet
    steady "S3"
}

scenario_power() {
    section "S4: abrupt power loss (MVP-ACC-06, E.8, STORE-007, SPOOL-T06, SPOOL-T08)"
    local old kill
    old=$(invocation)
    mid_poll
    kill=$(now_us)
    record_end abrupt "SIGKILL"
    check "SIGKILL the collector mid-poll" on systemctl kill --signal=SIGKILL vakr-collector.service
    check "systemd restarts it by itself (Restart=on-failure, 10 s)" \
        record_start "restart after SIGKILL" "$old"
    check "recovery recorded an unclean_stop telemetry-loss incident (STORE-007)" unclean_since "$kill"
    check "check health warns of the incident for 24 h (E.24)" \
        wait_until 60 spool_says "telemetry-loss incident"
    check "the overview's telemetry-loss feed lists it (E.28)" \
        api_mentions /api/v1/overview unclean_stop
    steady_after_loss "S4a"
    mid_poll
    kill=$(now_us)
    record_end abrupt "power loss"
    check "docker kill the whole node mid-poll (power loss)" docker kill "$NODE"
    check "the node powers on again" docker start "$NODE"
    check "systemd starts vakr-collector and vakr-api by itself" came_back_by_itself
    check "the collector run after the power loss is logged" record_start "boot after power loss"
    check "recovery recorded an unclean_stop incident for the power loss" unclean_since "$kill"
    steady_after_loss "S4b"
    power_cut_page_cache
}

power_cut_page_cache() {
    # S4c: a power cut that also loses the page cache, which killing a container cannot
    # do (E.36 decision 2): freeze the collector just after an append, remove what no
    # fsync may have covered yet and tear the last write (faults.py powercut), then kill.
    local kill spool
    spool=$(on_sh 'ls -d /var/lib/vakr/spool/*/ | head -1')
    check "S4c: freeze the collector just after a spool append (SIGSTOP)" on_sh "timeout 90 bash -c '
        f=\$(ls ${spool}seg-*.spool | tail -1); s=\$(stat -c %s \$f)
        while [ \$(stat -c %s \$f) = \$s ]; do sleep 0.02; done
        systemctl kill --signal=SIGSTOP vakr-collector.service'"
    kill=$(now_us)
    record_end abrupt "power loss, page cache lost"
    check "S4c: drop the unsynced records and tear the last write" \
        bash -c "docker exec $NODE $PY /acc06/faults.py powercut $spool >$WORK/powercut.txt"
    note "simulated cut: $(head -1 "$WORK/powercut.txt"), $(grep -c '^poll' "$WORK/powercut.txt") poll record(s)"
    check "S4c: the cut removed at least one record" grep -q '^dropped [1-9]' "$WORK/powercut.txt"
    check "S4c: docker kill the frozen node" docker kill "$NODE"
    check "S4c: the node powers on again" docker start "$NODE"
    check "S4c: systemd starts vakr-collector and vakr-api by itself" came_back_by_itself
    check "S4c: the collector run after the cut is logged" record_start "boot after the cut"
    check "S4c: recovery recorded an unclean_stop incident" unclean_since "$kill"
    check "S4c: recovery truncated the torn write and recorded torn_tail (SPOOL-T02)" \
        bash -c "docker exec $NODE runuser -u vakr -- $PY /acc06/probe.py incidents | grep -q torn_tail"
    steady_after_loss "S4c"
}

steady_after_loss() {
    # steady_after_loss WHAT: as steady, but check health warns of the incident (E.24).
    check "$1: every device has a poll stored since the collector came back" wait_until 120 resumed
    check "$1: every device Active again" wait_until 180 all_active
    check "$1: no device is Down (invariant 2)" none_down
    check "$1: vakr check health is WARNING for the incident only (exit 1)" \
        bash -c "[ \"\$(docker exec $NODE vakr check health >/dev/null 2>&1; echo \$?)\" = 1 ]"
}

# --- the accounting ----------------------------------------------------------------

accounting() {
    section "accounting: every expected poll stored or explained (MVP-ACC-06, E.36)"
    local end start report
    # Let two intervals pass after the last recovery, so the window covers it, and leave
    # out the newest slots, which may still be in flight or in the spool.
    hold "$((2 * INTERVAL_S + 45))" "slots after the last recovery"
    end=$(awk -v n="$(now_us)" 'BEGIN { printf "%d", n - 45000000 }')
    event window_end "$end" "accounting"
    start=$(awk -F'\t' '$1 == "window_start" { print $2 }' "$EVENTS" | head -1)
    sleep 15
    check "the stored polls are read" bash -c "docker exec $NODE runuser -u vakr -- $PY /acc06/probe.py \
        polls $((start / 1000000 * 1000000 - 60000000)) $(now_us) $(device_ids | tr '\n' ' ') \
        >$WORK/polls.tsv"
    check "the incidents are read" bash -c "docker exec $NODE runuser -u vakr -- $PY /acc06/probe.py \
        incidents >$WORK/incidents.jsonl"
    local dropped=()
    [ -s "$WORK/powercut.txt" ] && dropped=(--dropped "$WORK/powercut.txt")
    report=$(uv run --quiet python -m tests.system.resilience.accounting --events "$EVENTS" \
        --polls "$WORK/polls.tsv" --incidents "$WORK/incidents.jsonl" --interval "$INTERVAL_S" \
        "${dropped[@]}")
    local rc=$?
    indent <<<"$report"
    check "no silent loss: every expected poll is stored or explained" test "$rc" -eq 0
}
