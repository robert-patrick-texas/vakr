#!/bin/bash
# In-container half of packaging/github-update-test.sh: install FROM from the
# real GitHub release, self-update to TO, roll back, update again through the
# timer's unit (for a manual-update-required TO: see the timer skip it, then
# update as an operator), and confirm a second run is a no-op. Needs FROM, TO, REPO, and
# EXPECTED_FP (the release key fingerprint this checkout trusts).
# shellcheck source=packaging/container/testlib.sh
. /test/testlib.sh
set -uo pipefail

has_db_command() { vakr db --help >/dev/null 2>&1; }
recommends_installed() {
    # apt-get installs a release's new Recommends on update, as on a real host.
    local pkg
    for pkg in $(dpkg-query -W -f='${Recommends}' vakr | tr ',' '\n' | sed 's/[|(].*//'); do
        installed "$pkg" || return 1
    done
}
# Releases before root-only update storage (UPD-005) kept the archives beside the databases.
newest_backup() { find /var/lib/vakr-update/backups /var/lib/vakr/backups -name 'pre-update-*.tar.gz' 2>/dev/null | sort | tail -1; }

section "install v$FROM from GitHub (docs/operations/install.md)"
apt_get update && apt_get install --no-install-recommends curl openssh-client ca-certificates
work=$(mktemp -d) && cd "$work" || exit 1
base=https://github.com/$REPO/releases/download/v$FROM
debver=$(sed 's/-rc\./~rc./' <<<"$FROM")
curl -fsSL -O "$base/vakr_${debver}_amd64.deb" -O "$base/SHA256SUMS" -O "$base/SHA256SUMS.sig"
curl -fsSLO "https://raw.githubusercontent.com/$REPO/v$FROM/packaging/release-signers"
fp=$(grep -v '^#' release-signers | awk 'NF {print $3, $4}' | ssh-keygen -lf - | awk '{print $2}')
check "release key fingerprint is the trusted one" equals "$fp" "$EXPECTED_FP"
check "v$FROM signature verifies" bash -c 'ssh-keygen -Y verify -f release-signers \
    -I vakr-release -n vakr-release -s SHA256SUMS.sig < SHA256SUMS'
check "v$FROM checksum verifies" sha256sum --check --ignore-missing --quiet SHA256SUMS
chmod 0644 "vakr_${debver}_amd64.deb"
check "v$FROM installs" apt_get install "./vakr_${debver}_amd64.deb"
check "v$FROM is running" equals "$(vakr --version)" "vakr $FROM"

# A release whose notes carry the marker is one the timer must leave alone (UPD-005).
notes=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/tags/v$TO")
marked=no
grep -q 'manual-update-required' <<<"$notes" && marked=yes
# The timer always installs the newest release, so its checks need TO to be it.
newest=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=1" |
    sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1)
echo "v$TO manual-update-required: $marked; newest release: v$newest"

section "vakr self-update --yes ($FROM -> $TO)"
vakr self-update --yes --version "$TO" >/tmp/update.log 2>&1
grep -iE '^vakr self-update:|new configuration options|^  [a-z_]+ \([0-9]+\):' /tmp/update.log
check "now running $TO" equals "$(vakr --version)" "vakr $TO"
update_state() { if [ -e /var/lib/vakr-update/update-state.json ]; then echo /var/lib/vakr-update/update-state.json; else echo /var/lib/vakr/update-state.json; fi; }
check "update state records success" grep -q '"result": "success"' "$(update_state)"
check "both databases are at the current schema" vakr db status
check "database files are vakr-owned" \
    bash -c '! find /var/lib/vakr -maxdepth 1 -name "*.db*" ! -user vakr | grep -q .'
added=$(vakr config new-options --since "$FROM" | sed -n 's/^options added since .*: //p')
if [ "${added:-0}" -gt 0 ]; then
    check "the $added new options are listed" grep -qi "new configuration options since $FROM" /tmp/update.log
else
    # A release that adds no options prints no options summary.
    check "no new options are claimed" bash -c '! grep -qi "new configuration options" /tmp/update.log'
fi
health=$(vakr check health 2>&1)
code=$?
echo "check health (exit $code): ${health:0:200}"
check "check health is not CRITICAL" test "$code" -ne 2
check "the pre-update backup holds no telemetry" \
    bash -c '! tar -tzf "$1" | grep -q telemetry.db' _ "$(newest_backup)"
check "the collector is installed and disabled" \
    equals "$(systemctl is-enabled vakr-collector.service 2>&1)" disabled
check "the packages $TO recommends are installed" recommends_installed

section "vakr self-update --rollback --yes ($TO -> $FROM)"
vakr self-update --rollback --yes 2>&1 | grep '^vakr self-update:'
check "rolled back to $FROM" equals "$(vakr --version)" "vakr $FROM"
if has_db_command; then
    # A rollback never restores telemetry.db (E.14). An unmarked release must
    # leave it readable by the older one (exit 2 is "schema newer than I
    # support"). A manual-update-required release may not, but its marker can
    # come from a migration FROM already has (a later release kept marked for
    # nodes still before it), so either answer is allowed then; the log says which.
    vakr db status >/tmp/db.log 2>&1
    db=$?
    if [ "$marked" = yes ]; then
        echo "the older release's vakr db status exits $db (2: a schema newer than it supports)"
        check "the older release accepts the databases or refuses only a newer schema" \
            test "$db" -eq 0 -o "$db" -eq 2
    else
        check "the older release accepts both databases" test "$db" -ne 2
    fi
fi

# The whole journal's cursor: the unit's own has none before its first run.
journal_cursor() { journalctl -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p'; }
if [ "$TO" != "$newest" ]; then
    section "the timer's checks need v$TO to be the newest release (it is v$newest); updating by hand"
    vakr self-update --yes --version "$TO" 2>&1 | grep '^vakr self-update:'
    check "running $TO again" equals "$(vakr --version)" "vakr $TO"
else
    if [ "$marked" = yes ]; then
        section "vakr-update.service skips $TO (manual-update-required), an operator updates"
        cursor=$(journal_cursor)
        check "the update service runs" systemctl start vakr-update.service
        held=$(journalctl -u vakr-update.service --after-cursor "$cursor" --no-pager -o cat)
        grep '^vakr self-update:' <<<"$held"
        check "the timer skips $TO for a manual update" grep -q 'manual update' <<<"$held"
        check "still running $FROM" equals "$(vakr --version)" "vakr $FROM"
        vakr self-update --yes --version "$TO" 2>&1 | grep '^vakr self-update:'
    else
        section "vakr-update.service (the timer's unit) updates to $TO again"
        check "the update service runs" systemctl start vakr-update.service
    fi
    check "running $TO again" equals "$(vakr --version)" "vakr $TO"
    # Only this run's entries: everything after a cursor taken just before it.
    cursor=$(journal_cursor)
    check "a second run succeeds" systemctl start vakr-update.service
    second=$(journalctl -u vakr-update.service --after-cursor "$cursor" --no-pager -o cat)
    grep '^vakr self-update:' <<<"$second"
    check "the second run is a no-op" grep -Eq 'up-to-date|up to date' <<<"$second"
    check "the second run installs nothing" bash -c '! grep -q update.install.start <<<"$1"' _ "$second"
fi

section "$TO collector smoke test"
check "the collector starts on an empty inventory" \
    systemctl enable --now vakr-collector.service vakr.target
sleep 3
check "the collector is active" equals "$(systemctl is-active vakr-collector.service)" active
vakr status >/dev/null 2>&1
check "vakr status answers" test $? -ne 2
check "the collector stops cleanly" systemctl stop vakr-collector.service
check "the collector exited 0" \
    equals "$(systemctl show -p ExecMainStatus --value vakr-collector.service)" 0

finish github-update-test
