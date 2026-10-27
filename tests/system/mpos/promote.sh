# MPOS-T05 of tests/system/mpos/run.sh: losing the primary (vakr node promote and
# demote, ADR-024 decisions 23 to 30, Appendix E.39). Sourced after lib.sh and
# phases.sh. Runs only with MPOS_FEATURES naming promote, roles, and replication.
#
#  1. With east answering, west refuses to promote.
#  2. Cut east from west; promote west; edit metadata on west (the new primary)
#     and on east (the old primary, still accepting edits while cut off).
#  3. Heal: both report two primaries as CRITICAL node health, and every device
#     stays Active on both (never target state, MPOS-006).
#  4. Demote east: it backs up, replaces its metadata with west's, reports its own
#     partition edit, and the two digests converge; the conflict clears.
# shellcheck shell=bash
# shellcheck disable=SC2154  # window: from run.sh

NEEDS_PROMOTE="needs MPOS_FEATURES=roles,replication,promote"

shown() { indent >&3; }  # through fd 3: check() discards stdout and stderr
refuses_while_east_answers() {
    local out
    if out=$(on west vakr node promote --yes 2>&1); then
        shown <<<"$out"
        return 1
    fi
    shown <<<"$out"
    grep -q "still answers" <<<"$out" && grep -q -- "--force" <<<"$out"
}
promote_west() {
    local out
    out=$(on west vakr node promote --yes 2>&1) || { shown <<<"$out"; return 1; }
    shown <<<"$out"
    grep -q "newest metadata pull" <<<"$out" && grep -q "promoted: node.role = primary" <<<"$out"
}
role_is() {
    # role_is NODE ROLE: vakr node status says so.
    on "$1" vakr node status --json | python3 -c \
        'import json, sys; sys.exit(json.load(sys.stdin)["role"] != sys.argv[1])' "$2"
}
edit_inventory() {
    # edit_inventory NODE TEXT: a comment on every device, applied on that node.
    on_sh "$1" "sed -i 's/^interval = .*/&\ncomment = \"$2\"/' /etc/vakr/inventory.toml && \
        vakr inventory apply >/dev/null"
}
health_says() {
    # health_says NODE TEXT: vakr check health's output contains TEXT (any exit status).
    local out
    out=$(on "$1" vakr check health 2>&1)
    grep -qi -- "$2" <<<"$out"
}
health_clear_of() { ! health_says "$1" "$2"; }
demote_east() {
    local out
    out=$(on east vakr node demote --yes 2>&1) || { shown <<<"$out"; return 1; }
    shown <<<"$out"
    grep -q "demoted: node.role = peer" <<<"$out"
}
digest_of() { on "$1" runuser -u vakr -- "${PROBE[@]}" digest; }
converged() {
    local a b
    a=$(digest_of east) && b=$(digest_of west) && [ -n "$a" ] && [ "$a" = "$b" ]
}
resync_reported() {
    # East's report lists its replaced devices and its own partition edit, and names a backup.
    on east cat /var/lib/vakr/replication/resync-report.json | python3 -c '
import json, sys
r = json.load(sys.stdin)
devices = [d for d in r["discarded"] if d["table"] == "device" and d["change"] == "replaced"]
ok = devices and r["edits_total"] >= 1 and r["backup"] and r["fork_at"]
sys.exit(0 if ok else 1)'
}
no_resync_pending() { ! on east test -e /var/lib/vakr/replication/resync.json; }

mpos_t05() {
    section "MPOS-T05: the primary lost: promote west, two primaries reported, demote east, converge"
    if ! { has_feature promote && has_feature roles && has_feature replication; }; then
        skip "west promoted, the conflict reported, east demoted and converged" "$NEEDS_PROMOTE"
        return 0
    fi
    exec 3>&1
    check "west refuses to promote while east (the primary) answers" refuses_while_east_answers
    check "east (the primary) cut from west (the peer)" cut_nodes
    check "west promoted with east unreachable" promote_west
    check "west's role is primary" wait_until 120 role_is west primary
    check "west (new primary) accepts a metadata edit" edit_inventory west "edited on west"
    check "east (old primary, cut off) accepts a metadata edit" edit_inventory east "edited on east"
    check "east-west healed" heal_nodes
    check "east reports two primaries as CRITICAL node health" \
        wait_until "$window" health_says east "two primaries"
    check "west reports two primaries as CRITICAL node health" \
        wait_until "$window" health_says west "two primaries"
    check "east still shows every device Active (node health, never target state)" all_active east
    check "west still shows every device Active" all_active west
    check "east demoted to a peer of west" demote_east
    check "east's metadata converges with west's (equal digests)" wait_until "$window" converged
    check "east's resync is done" wait_until 60 no_resync_pending
    check "east reports the rows it replaced, its own partition edit, and the backup" \
        resync_reported
    check "east no longer reports two primaries" \
        wait_until "$window" health_clear_of east "two primaries"
    check "west no longer reports two primaries" \
        wait_until "$window" health_clear_of west "two primaries"
    check "east keeps polling every device as a peer" wait_until "$window" all_active east
    exec 3>&-
}
