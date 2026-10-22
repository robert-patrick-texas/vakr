#!/bin/bash
# Install, remove, reinstall, upgrade, and purge the vakr .deb in a fresh
# ubuntu:26.04 container (PKG-002..PKG-005, CFGM-005, UPD-003, VER-001).
# packaging/package-test.sh runs it; it changes the system, so never run it
# on a real host.
#
# Environment:
#   DEB_N, DEB_N1         package files for release N and test release N+1
#   DEBVER_N, DEBVER_N1   their dpkg versions
#   PEP_N, PEP_N1         the `vakr --version` each must report (PEP 440 form)
#   PYTHON_MINOR          the bundled interpreter's version (.python-version)
set -euo pipefail
: "${DEB_N:?}" "${DEB_N1:?}" "${DEBVER_N:?}" "${DEBVER_N1:?}" "${PEP_N:?}" "${PEP_N1:?}"
: "${PYTHON_MINOR:?}"
# shellcheck source=packaging/container/testlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/testlib.sh"

CONFIG=/etc/vakr/config.toml

owner_mode() { [ "$(stat -c '%a %U %G' "$1")" = "$2" ]; }
config_sum() { sha256sum "$CONFIG" | cut -d' ' -f1; }

unexpected_paths() {
    # Every packaged file lives under /opt/vakr except the OS integration
    # points PKG-002 allows plus Debian's copyright, changelog, and overrides.
    dpkg-query -L vakr | while IFS= read -r path; do
        if [ -d "$path" ] && [ ! -L "$path" ]; then continue; fi
        case $path in
            /opt/vakr/* | /usr/bin/vakr | /usr/lib/systemd/system/vakr*) ;;
            /usr/lib/sysusers.d/vakr.conf | /usr/lib/tmpfiles.d/vakr.conf) ;;
            /usr/share/doc/vakr/copyright | /usr/share/doc/vakr/changelog.gz) ;;
            /usr/share/lintian/overrides/vakr) ;;
            *) echo "$path" ;;
        esac
    done
}

unowned_files() {
    # Anything under /opt/vakr that dpkg does not own was written at run time
    # (for example bytecode by root) and would block a clean removal.
    comm -23 <(find /opt/vakr | sort) <(dpkg-query -L vakr | sort)
}

check_installed_release() {
    local debver=$1 pep=$2
    check "dpkg reports version $debver" equals "$(dpkg-query -W -f='${Version}' vakr)" "$debver"
    check "vakr --version prints 'vakr $pep' (VER-001)" equals "$(vakr --version)" "vakr $pep"
    check "dpkg --verify finds no changed files" equals "$(dpkg --verify vakr)" ""
    check "nothing unowned was written under /opt/vakr" equals "$(unowned_files)" ""
}

check_layout() {
    check "files outside /opt/vakr are only OS integration points (PKG-002)" \
        equals "$(unexpected_paths)" ""
    check "/usr/bin/vakr links to /opt/vakr/venv/bin/vakr" \
        equals "$(readlink /usr/bin/vakr)" /opt/vakr/venv/bin/vakr
    check "venv runs the bundled Python $PYTHON_MINOR, not the system one" \
        /opt/vakr/venv/bin/python3 -c 'import sys
assert "%d.%d" % sys.version_info[:2] == sys.argv[1]
assert sys.base_prefix == "/opt/vakr/python"' "$PYTHON_MINOR"
    check "no dpkg conffiles (CFGM-005)" equals "$(dpkg-query -W -f='${Conffiles}' vakr)" ""
    local dep
    for dep in ca-certificates openssh-client openssl systemd; do
        check "depends on $dep" grep -qw "$dep" <<<"$(dpkg-query -W -f='${Depends}' vakr)"
    done
    check "recommended snmp is installed" installed snmp
    check "release signers file is installed" test -s /opt/vakr/share/release-signers
    check "ssh-keygen is available for signature checks" command -v ssh-keygen
}

check_accounts_and_dirs() {
    check "user vakr: home /var/lib/vakr, shell nologin (PKG-003)" \
        equals "$(getent passwd vakr | cut -d: -f6,7)" "/var/lib/vakr:/usr/sbin/nologin"
    check "group vakr exists" getent group vakr
    check "group vakr-admin exists" getent group vakr-admin
    check "group vakr-view exists (E.12)" getent group vakr-view
    check "/run/vakr is 0751 vakr:vakr" owner_mode /run/vakr "751 vakr vakr"
    check "/var/lib/vakr is 0750 vakr:vakr" owner_mode /var/lib/vakr "750 vakr vakr"
    check "/var/lib/vakr/backups is 0700 vakr:vakr" owner_mode /var/lib/vakr/backups "700 vakr vakr"
    check "/var/lib/vakr-update is 0755 root:root (UPD-005)" owner_mode /var/lib/vakr-update "755 root root"
    check "/var/lib/vakr-update/backups is 0700 root:root" \
        owner_mode /var/lib/vakr-update/backups "700 root root"
    check "/var/cache/vakr is 0750 root:vakr" owner_mode /var/cache/vakr "750 root vakr"
    check "/etc/vakr is 0755 root:vakr" owner_mode /etc/vakr "755 root vakr"
    check "/etc/vakr/secrets is 0750 root:vakr" owner_mode /etc/vakr/secrets "750 root vakr"
    check "/etc/vakr/tls/ca.d is 0755 root:root" owner_mode /etc/vakr/tls/ca.d "755 root root"
    check "postinst created $CONFIG as 0640 root:vakr" owner_mode "$CONFIG" "640 root vakr"
    check "vakr-api.service is not enabled (PKG-003)" \
        test ! -e /etc/systemd/system/vakr.target.wants/vakr-api.service
    check "vakr-update.timer is not enabled (UPD-003)" \
        test ! -e /etc/systemd/system/timers.target.wants/vakr-update.timer
    check "vakr.target is not enabled before vakr setup (PKG-003)" \
        test ! -e /etc/systemd/system/multi-user.target.wants/vakr.target
}

check_commands() {
    local health doctor health_rc=0 doctor_rc=0
    health=$(vakr check health) || health_rc=$?
    check "vakr check health runs (exit $health_rc: $health)" \
        test "$health_rc" -le 3 -a "${health:0:5}" = "VAKR "
    doctor=$(vakr doctor) || doctor_rc=$?
    check "vakr doctor runs (exit $doctor_rc)" test "$doctor_rc" -le 3 -a -n "$doctor"
    printf '%s\n' "$doctor" | sed 's/^/      /'
}

stage_debs
apt-get -qq update

section "install release N ($DEBVER_N)"
apt_get install "$DEB_N"
check "package is installed" installed vakr
check_layout
check_accounts_and_dirs
check_commands
check_installed_release "$DEBVER_N" "$PEP_N"

section "remove keeps configuration and data (PKG-005)"
echo "# operator edit made by the package test" >>"$CONFIG"
before=$(config_sum)
touch /var/lib/vakr/history.sentinel
apt_get remove vakr
check "package is no longer installed" not_installed vakr
check "/opt/vakr is gone" test ! -e /opt/vakr
check "/usr/bin/vakr is gone" test ! -e /usr/bin/vakr
check "$CONFIG is kept unchanged" equals "$(config_sum)" "$before"
check "/var/lib/vakr data is kept" test -e /var/lib/vakr/history.sentinel
check "user vakr is kept" getent passwd vakr

section "reinstall release N"
apt_get install "$DEB_N"
check "reinstall leaves the operator's config untouched" equals "$(config_sum)" "$before"
check_installed_release "$DEBVER_N" "$PEP_N"

section "upgrade N ($DEBVER_N) -> N+1 ($DEBVER_N1)"
# A 0.2.x self-update leaves the backups directory root-owned (its own mkdir);
# the next package's postinst must give it back before `vakr db migrate`.
chown root:root /var/lib/vakr/backups
apt_get install "$DEB_N1"
check "the upgrade gives /var/lib/vakr/backups back to vakr" \
    owner_mode /var/lib/vakr/backups "700 vakr vakr"
check "vakr db backup writes there after the upgrade" vakr db backup
check "upgrade keeps the operator's config byte for byte (CFGM-005)" \
    equals "$(config_sum)" "$before"
check_installed_release "$DEBVER_N1" "$PEP_N1"

section "purge removes configuration, keeps telemetry history (PKG-005)"
purge_log=$(apt-get -o Dpkg::Use-Pty=0 -y purge vakr 2>&1)
check "purge prints the kept-history notice" \
    grep -q "kept telemetry history in /var/lib/vakr" <<<"$purge_log"
printf '%s\n' "$purge_log" | grep '^vakr:' | sed 's/^/      /'
check "/etc/vakr is removed" test ! -e /etc/vakr
check "/var/cache/vakr is removed" test ! -e /var/cache/vakr
check "/var/lib/vakr-update is removed (its archives hold configuration)" test ! -e /var/lib/vakr-update
check "/var/lib/vakr history is kept" test -e /var/lib/vakr/history.sentinel
check "dpkg no longer lists vakr" not_installed vakr
check "user vakr is kept so kept files have an owner" getent passwd vakr

finish "install test"
