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

stage_debs
apt-get -qq update

section "first install starts and enables nothing (PKG-003, UPD-003)"
apt_get install "$DEB_N"
for unit in vakr.target vakr-update.service vakr-update.timer; do
    check "$unit loads cleanly" equals "$(prop LoadState "$unit")" loaded
done
check "units verify under the running systemd" \
    systemd-analyze verify vakr.target vakr-update.service vakr-update.timer
check "vakr-update.timer is disabled" equals "$(systemctl is-enabled vakr-update.timer)" disabled
check "vakr-update.timer is not running" equals "$(systemctl is-active vakr-update.timer)" inactive
check "vakr.target is not running" equals "$(systemctl is-active vakr.target)" inactive

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
systemctl start vakr.target
apt_get remove vakr
check "remove stopped the timer" equals "$(systemctl is-active vakr-update.timer)" inactive
check "remove stopped vakr.target" equals "$(systemctl is-active vakr.target)" inactive
apt_get purge vakr
check "purge removed the timer's enablement link" \
    test ! -e /etc/systemd/system/timers.target.wants/vakr-update.timer

finish "systemd test"
