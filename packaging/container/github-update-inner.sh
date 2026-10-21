#!/bin/bash
# In-container half of packaging/github-update-test.sh: install FROM from the
# real GitHub release, self-update to TO, roll back, update again through the
# timer's unit, and confirm a second run is a no-op. Needs FROM, TO, REPO, and
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
newest_backup() { find /var/lib/vakr/backups -name 'pre-update-*.tar.gz' | sort | tail -1; }

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

section "vakr self-update --yes ($FROM -> $TO)"
vakr self-update --yes >/tmp/update.log 2>&1
grep -iE '^vakr self-update:|new configuration options|^  [a-z_]+ \([0-9]+\):' /tmp/update.log
check "now running $TO" equals "$(vakr --version)" "vakr $TO"
check "update state records success" grep -q '"result": "success"' /var/lib/vakr/update-state.json
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
    # A rollback never restores telemetry.db (E.14), so the older release must
    # still accept it: exit 2 would mean a schema newer than it supports.
    vakr db status >/tmp/db.log 2>&1
    check "the older release accepts both databases" test $? -ne 2
fi

section "vakr-update.service (the timer's unit) updates to $TO again"
check "the update service runs" systemctl start vakr-update.service
check "running $TO again" equals "$(vakr --version)" "vakr $TO"
# Only this run's entries: everything after a cursor taken just before it.
cursor=$(journalctl -u vakr-update.service -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')
check "a second run succeeds" systemctl start vakr-update.service
second=$(journalctl -u vakr-update.service --after-cursor "$cursor" --no-pager -o cat)
grep '^vakr self-update:' <<<"$second"
check "the second run is a no-op" grep -Eq 'up-to-date|up to date' <<<"$second"
check "the second run installs nothing" bash -c '! grep -q update.install.start <<<"$1"' _ "$second"

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
