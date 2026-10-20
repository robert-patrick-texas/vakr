#!/bin/bash
# `make github-update-test`: after a release is published, prove that a host on
# the previous release updates to it from the real GitHub releases (UPD-001..008,
# blueprint 28.7). It needs docker, git, ssh-keygen, and outbound HTTPS.
#
# In a privileged ubuntu:26.04 container booted with systemd it:
#   1. installs FROM from GitHub, checking the release key fingerprint, the
#      SSH signature, and the checksum, as docs/operations/install.md says;
#   2. runs `vakr self-update --yes` to TO and checks the result: version,
#      update record, databases current and vakr-owned, new options listed,
#      health not CRITICAL, no telemetry in the pre-update backup;
#   3. rolls back to FROM, and checks the older release accepts the databases;
#   4. lets vakr-update.service (the timer's unit) update to TO again, then
#      checks a second run changes nothing;
#   5. starts the collector on an empty inventory and stops it cleanly.
#
# Environment:
#   TO    release to update to (default: VERSION)
#   FROM  release to start from (default: the newest published tag before TO)
#   REPO  GitHub repository (default: tools/release/release.toml's repo)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
TO=${TO:-$(cat VERSION)}
REPO=${REPO:-$(sed -n 's/^repo = "\(.*\)"$/\1/p' tools/release/release.toml)}
if [ -z "${FROM:-}" ]; then
    # vakr's own version order, so 0.2.0-rc.1 sorts before 0.2.0 (VER-002).
    FROM=$(git ls-remote --tags --refs "https://github.com/$REPO.git" 'v*' |
        sed 's|.*refs/tags/v||' |
        uv run --quiet python -c '
import sys
from vakr.lifecycle.update.versions import Version
to = Version.parse(sys.argv[1])
older = [Version.parse(t) for t in sys.stdin.read().split() if Version.parse(t) < to]
print(max(older).semver() if older else "")' "$TO")
fi
[ -n "$FROM" ] || { echo "github-update-test: no published release before $TO; set FROM" >&2; exit 2; }
# The trusted key is the one this checkout ships; its fingerprint must match the
# key that signed both releases.
EXPECTED_FP=$(grep -v '^#' packaging/release-signers | awk 'NF {print $3, $4}' |
    ssh-keygen -lf - | awk '{print $2}' | head -1)
echo "github-update-test: $REPO $FROM -> $TO (release key $EXPECTED_FP)"

name=vakr-github-update-$$
docker build --quiet --tag vakr-systemd-test - <packaging/container/Dockerfile.systemd >/dev/null
cleanup() { docker rm --force "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --detach --name "$name" --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock -v "$PWD/packaging/container:/test:ro" \
    vakr-systemd-test >/dev/null
for _ in $(seq 60); do
    state=$(docker exec "$name" systemctl is-system-running 2>/dev/null || true)
    case $state in running | degraded) break ;; esac
    sleep 1
done
echo "github-update-test: container systemd state: $state"
docker exec -e FROM="$FROM" -e TO="$TO" -e REPO="$REPO" -e EXPECTED_FP="$EXPECTED_FP" \
    "$name" bash /test/github-update-inner.sh
