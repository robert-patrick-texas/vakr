# Shared helpers for the in-container package tests (install-test.sh,
# systemd-test.sh). Sourced, not executed. Each check prints PASS or FAIL and
# a failure is counted rather than aborting, so one run reports every problem.
# shellcheck shell=bash

export DEBIAN_FRONTEND=noninteractive
failures=0

pass() { printf 'PASS  %s\n' "$*"; }
fail() {
    printf 'FAIL  %s\n' "$*"
    failures=$((failures + 1))
}
check() {
    local what=$1
    shift
    if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi
}
section() { printf '\n== %s\n' "$*"; }
equals() { [ "$1" = "$2" ]; }
installed() { [ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" = "install ok installed" ]; }
not_installed() { ! installed "$1"; }

apt_get() {
    # apt's own progress output is noise; show it only when apt fails.
    if ! apt-get -o Dpkg::Use-Pty=0 -y "$@" >/tmp/apt.log 2>&1; then
        cat /tmp/apt.log
        return 1
    fi
}

stage_debs() {
    # apt's sandbox user must be able to read local packages.
    mkdir -p /tmp/debs
    cp "$DEB_N" "$DEB_N1" /tmp/debs/
    chmod 0644 /tmp/debs/*.deb
    DEB_N=/tmp/debs/$(basename "$DEB_N")
    DEB_N1=/tmp/debs/$(basename "$DEB_N1")
}

finish() {
    section "summary"
    if [ "$failures" -gt 0 ]; then
        echo "$1: $failures check(s) FAILED"
        exit 1
    fi
    echo "$1: all checks passed"
}
