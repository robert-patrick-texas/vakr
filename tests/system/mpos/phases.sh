# The MPOS-T01..T04 phases of tests/system/mpos/run.sh (blueprint 27.5). Sourced
# after lib.sh, with names, cut_names, kept_names, partition, and window set.
# shellcheck shell=bash
# shellcheck disable=SC2154  # names, cut_names, kept_names, partition, window: from run.sh

NEEDS_A="needs node roles, M5 part A"
NEEDS_B="needs observation replication, M5 part B"

# --- small predicates ------------------------------------------------------------

ms_between() {
    # ms_between LATENCY POSITION LO HI: that position's median RTT is in [LO, HI] ms.
    awk -F'\t' -v p="$2" -v lo="$3" -v hi="$4" \
        '$1 == p && $3 != "none" && $3 + 0 >= lo && $3 + 0 <= hi {ok = 1} END {exit !ok}' <<<"$1"
}
one_series_each() {
    # one_series_each LATENCY: no position or collector appears twice (nothing merged or split).
    [ -n "$1" ] && [ "$(cut -f1 <<<"$1" | sort | uniq -d)" = "" ] &&
        [ "$(cut -f2 <<<"$1" | sort | uniq -d)" = "" ]
}
both_series() {
    # both_series LATENCY: east and west are separate series, each with its own RTT.
    one_series_each "$1" && ms_between "$1" east 0 15 && ms_between "$1" west 45 90
}
own_down() { own_label "$1" "$2" | grep -qi down; }
device_active() { device_line "$1" "$2" | awk -F'\t' '$2 == "active" {ok = 1} END {exit !ok}'; }
composite_is() { [ "$(composite_label "$1" "$2")" = "$3" ]; }
grew() { [ "$(poll_rows "$1")" -gt "$2" ]; }
no_duplicates() {
    local rows
    rows=$(on "$1" "${PROBE[@]}" rows) && [ -n "$rows" ] && ! awk -F'\t' '$3 != $4' <<<"$rows" | grep -q .
}
own_collector() {
    on "$1" cat /var/lib/vakr/collector/identity.json |
        grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1
}
covers() {
    # covers RECEIVER SNAPSHOT COLLECTOR: the receiver holds at least the snapshot's rows
    # of that collector, family by family (MPOS-T02: counts match the source).
    local now
    now=$(on "$1" "${PROBE[@]}" rows) || return 1
    awk -F'\t' -v c="$3" 'NR == FNR {if ($2 == c) have[$1] = $3; next}
        $2 == c {seen++; if ((have[$1] + 0) < $3) bad++} END {exit !(seen && !bad)}' \
        <(echo "$now") <(echo "$2")
}
peer_refuses_edit() {
    # MPOS-T03: an inventory edit on the peer is refused, and the refusal names the primary.
    local out
    if out=$(on_sh west "sed 's/^interval = .*/&\ncomment = \"edited on the peer\"/' \
        /etc/vakr/inventory.toml >/tmp/edited.toml && vakr inventory apply --file /tmp/edited.toml" 2>&1); then
        return 1
    fi
    grep -qi primary <<<"$out" && grep -q "$EAST_INTER" <<<"$out"
}

# --- the phases ----------------------------------------------------------------

mpos_t04() {
    section "MPOS-T04: 60 ms between positions; each position's latency stays its own (MPOS-008)"
    local device=${names[0]} east_lat west_lat
    east_lat=$(on east "${PROBE[@]}" latency "$device")
    west_lat=$(on west "${PROBE[@]}" latency "$device")
    echo "      $device latency (position, collector, median ms, probes), on east:"
    indent <<<"$east_lat"
    echo "      on west:"
    indent <<<"$west_lat"
    check "east's own median RTT to $device is under 15 ms" ms_between "$east_lat" east 0 15
    check "west's own median RTT to $device is 45 to 90 ms (its 60 ms path)" \
        ms_between "$west_lat" west 45 90
    check "east's history has one series per position" one_series_each "$east_lat"
    check "west's history has one series per position" one_series_each "$west_lat"
    gated replication "$NEEDS_B" "east shows west's series beside its own, each with its own RTT" \
        both_series "$east_lat"
    gated replication "$NEEDS_B" "west shows east's series beside its own, each with its own RTT" \
        both_series "$west_lat"
}

mpos_t01() {
    section "MPOS-T01: ${partition} s partition: east cut from west, west cut from ${#cut_names[@]} devices"
    local east_before west_before name
    east_before=$(poll_rows east)
    west_before=$(poll_rows west)
    check "east and west cut from each other" cut_nodes
    check "west cut from ${cut_names[*]}" cut_west_devices
    hold "$partition" "the partition"
    check "east still shows every device Active" all_active east
    for name in "${cut_names[@]}"; do
        check "west sees $name down from west (\"$(own_label west "$name")\")" own_down west "$name"
    done
    for name in "${kept_names[@]}"; do
        check "west still sees $name Active" device_active west "$name"
    done
    check "east kept polling through the partition" grew east "$east_before"
    check "west kept polling through the partition" grew west "$west_before"
    # The composite needs east's evidence on west, which cannot cross a cut link,
    # so heal east-west first and keep west's device cut for one window.
    check "east-west healed; west's device cut kept" heal_nodes
    if has_feature replication; then
        hold "$window" "replication of east's evidence to west"
        for name in "${cut_names[@]}"; do
            check "west's composite for $name reads \"Reachable from east only\"" \
                composite_is west "$name" "Reachable from east only"
        done
    else
        skip "west's composite for the cut devices reads \"Reachable from east only\"" "$NEEDS_B"
    fi
    check "west's device paths healed" heal_west_devices
}

mpos_t02() {
    section "MPOS-T02: connectivity restored; backfill both ways, no duplicate observations"
    local east_c west_c east_snap west_snap
    if has_feature replication; then
        east_c=$(own_collector east)
        west_c=$(own_collector west)
        east_snap=$(on east "${PROBE[@]}" rows)
        west_snap=$(on west "${PROBE[@]}" rows)
        hold "$window" "the backfill"
        check "west holds every observation east's collector had made" covers west "$east_snap" "$east_c"
        check "east holds every observation west's collector had made" covers east "$west_snap" "$west_c"
    else
        skip "west holds every observation east's collector had made" "$NEEDS_B"
        skip "east holds every observation west's collector had made" "$NEEDS_B"
    fi
    skip "the backfill runs oldest first" "written with M5 part B, from its replication cursor"
    check "east holds no duplicate observation UUIDs" no_duplicates east
    check "west holds no duplicate observation UUIDs" no_duplicates west
}

mpos_t03() {
    section "MPOS-T03: the primary unreachable; the peer polls on and refuses metadata edits"
    local before
    before=$(poll_rows west)
    check "east (the primary) cut from west (the peer)" cut_nodes
    hold "$window" "the primary's absence"
    check "west kept polling with its last-known inventory" grew west "$before"
    gated roles "$NEEDS_A" "west refuses an inventory edit, naming the primary" peer_refuses_edit
    check "east-west healed" heal_nodes
}
