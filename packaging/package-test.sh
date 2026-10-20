#!/bin/bash
# `make package-test`: prove the .deb installs, upgrades, and removes cleanly
# (PKG-002..PKG-005, CFGM-005, UPD-003, VER-001). It needs docker, git, and uv.
#
#  1. Static checks of the packaging files (packaging/tests, pytest).
#  2. Build release N from VERSION and a test release N+1 (next patch
#     version, via VERSION_OVERRIDE) into dist/package-test/.
#  3. In a fresh ubuntu:26.04 container: install N, check files, users,
#     directories, config, and commands; remove; reinstall; upgrade to N+1
#     keeping the operator's config; purge (packaging/container/install-test.sh).
#  4. In an ubuntu:26.04 container booted with systemd: the timer is shipped
#     disabled and can be enabled, and an upgrade restarts vakr.target only
#     if it was running (packaging/container/systemd-test.sh). Set
#     PACKAGE_TEST_SYSTEMD=0 where privileged containers are not allowed.
#
# Environment:
#   PACKAGE_TEST_DEB_N    use this already-built release-N package instead of
#                         building one (CI passes the `make deb` output)
#   PACKAGE_TEST_SYSTEMD  1 (default) runs step 4; 0 skips it
#   PACKAGE_TEST_REPRO    1 rebuilds release N and requires a byte-identical
#                         package (PKG-004, QG-010); default 0, CI sets 1
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
work=dist/package-test
version_as() { uv run --quiet tools/release/release.py --print-version "$1" --version "$2"; }

version_n=$(cat VERSION)
version_n1=$(version_as next-patch "$version_n")
debver_n=$(version_as deb "$version_n")
debver_n1=$(version_as deb "$version_n1")

echo "package-test: static checks of packaging files"
uv run pytest -p no:cacheprovider -q packaging/tests

rm -rf "$work"
mkdir -p "$work/n" "$work/n1"
if [ -n "${PACKAGE_TEST_DEB_N:-}" ]; then
    cp "$PACKAGE_TEST_DEB_N" "$work/n/"
else
    packaging/build-deb.sh "$work/n"
fi
VERSION_OVERRIDE=$version_n1 packaging/build-deb.sh "$work/n1"

deb_n=vakr_${debver_n}_amd64.deb
deb_n1=vakr_${debver_n1}_amd64.deb

if [ "${PACKAGE_TEST_REPRO:-0}" = 1 ]; then
    echo "package-test: rebuilding release N to check reproducibility"
    VAKR_UV_CACHE='' packaging/build-deb.sh "$work/repro"
    if ! cmp "$work/n/$deb_n" "$work/repro/$deb_n"; then
        echo "package-test: FAIL: two builds of one commit differ (PKG-004)"
        exit 1
    fi
    echo "package-test: PASS: rebuild is byte-identical ($(sha256sum <"$work/n/$deb_n" | cut -c1-16)...)"
fi
test_env=(
    --env DEB_N="/debs/n/$deb_n" --env DEB_N1="/debs/n1/$deb_n1"
    --env DEBVER_N="$debver_n" --env DEBVER_N1="$debver_n1"
    --env PEP_N="$(version_as pep440 "$version_n")" --env PEP_N1="$(version_as pep440 "$version_n1")"
)
mounts=(--volume "$PWD/$work:/debs:ro" --volume "$PWD/packaging/container:/test:ro")

echo "package-test: install, remove, reinstall, upgrade, purge in ubuntu:26.04"
docker run --rm "${mounts[@]}" "${test_env[@]}" ubuntu:26.04 bash /test/install-test.sh

run_systemd_test() {
    local image=vakr-systemd-test name=vakr-systemd-test-$$ state=""
    docker build --quiet --tag "$image" - <packaging/container/Dockerfile.systemd >/dev/null
    # systemd as PID 1 needs a privileged container with its own cgroup tree.
    docker run --detach --name "$name" --privileged --cgroupns=private \
        --tmpfs /run --tmpfs /run/lock "${mounts[@]}" "$image" >/dev/null
    trap 'docker rm --force "$name" >/dev/null 2>&1 || true' EXIT
    for _ in $(seq 60); do
        state=$(docker exec "$name" systemctl is-system-running 2>/dev/null || true)
        case $state in running | degraded) break ;; esac
        sleep 1
    done
    echo "package-test: container systemd state: $state"
    docker exec "${test_env[@]}" "$name" bash /test/systemd-test.sh
}

if [ "${PACKAGE_TEST_SYSTEMD:-1}" = 1 ]; then
    echo "package-test: unit behaviour under a running systemd"
    run_systemd_test
fi
echo "package-test: passed"
