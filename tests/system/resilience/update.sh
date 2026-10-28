# S0 of tests/system/resilience/run.sh: self-update a node while it collects
# (MVP-ACC-07, UPD-002, UPD-006, Appendix E.36). Sourced after lib.sh, before
# the setup, which calls prepare_update; scenario_update uses the run log and
# the steady-state checks of scenarios.sh, sourced before it runs.
#
# The node starts on FROM, the newest published release before the package
# under test, installed from GitHub with its signature checked against this
# checkout's release key. It then updates to the package under test with
# `vakr self-update --file`, the path an operator takes with a release they
# copied over, while the collector and the API run against the fleet. The
# real release key cannot sign a package that is not published yet, so the
# harness signs it with a throwaway key made for the run, and trusts that key
# beside the release key on the FROM node only. The package under test ships
# the release key alone, so the throwaway key is gone once the update lands.
# shellcheck shell=bash

CANDIDATE=$WORK/candidate
TEST_SIGNER="vakr-resilience-test"

semver_of() {
    # semver_of DEBVER: the version as VERSION writes it (1.4.0~rc.1 -> 1.4.0-rc.1).
    echo "${1//\~rc./-rc.}"
}

previous_release() {
    # previous_release REPO TO: the newest published release before TO, in vakr's
    # own version order (VER-002), as packaging/github-update-test.sh picks FROM.
    git ls-remote --tags --refs "https://github.com/$1.git" 'v*' | sed 's|.*refs/tags/v||' |
        uv run --quiet python -c '
import sys
from vakr.lifecycle.update.versions import Version
to = Version.parse(sys.argv[1])
older = [Version.parse(t) for t in sys.stdin.read().split() if Version.parse(t) < to]
print(max(older).semver() if older else "")' "$2"
}

prepare_update() {
    # prepare_update DEB: download and verify FROM into $WORK/from, and stage the
    # package under test, signed by a throwaway key, in $WORK/candidate. Sets
    # FROM_VERSION, TO_VERSION, FROM_DEB, and CANDIDATE_FILE.
    local deb=$1 repo debver base signer
    debver=$(basename "$deb" | sed -n 's/^vakr_\(.*\)_amd64\.deb$/\1/p')
    [ -n "$debver" ] || { echo "resilience: $deb is not named vakr_<version>_amd64.deb"; return 1; }
    TO_VERSION=$(semver_of "$debver")
    repo=$(sed -n 's/^repo = "\(.*\)"$/\1/p' tools/release/release.toml)
    FROM_VERSION=${RESIL_FROM:-$(previous_release "$repo" "$TO_VERSION")}
    [ -n "$FROM_VERSION" ] || { echo "resilience: no release before $TO_VERSION; set RESIL_FROM"; return 1; }
    echo "resilience: S0 updates $FROM_VERSION -> $TO_VERSION ($repo)"
    rm -rf "$WORK/from" "$CANDIDATE" && mkdir -p "$WORK/from" "$CANDIDATE"
    FROM_DEB=vakr_${FROM_VERSION//-rc./\~rc.}_amd64.deb
    base=https://github.com/$repo/releases/download/v$FROM_VERSION
    (cd "$WORK/from" && curl -fsSL -O "$base/$FROM_DEB" -O "$base/SHA256SUMS" -O "$base/SHA256SUMS.sig") ||
        { echo "resilience: v$FROM_VERSION could not be downloaded"; return 1; }
    ssh-keygen -Y verify -f packaging/release-signers -I vakr-release -n vakr-release \
        -s "$WORK/from/SHA256SUMS.sig" <"$WORK/from/SHA256SUMS" >/dev/null ||
        { echo "resilience: v$FROM_VERSION is not signed by this checkout's release key"; return 1; }
    (cd "$WORK/from" && sha256sum --check --ignore-missing --quiet SHA256SUMS) || return 1
    CANDIDATE_FILE=vakr_${debver}_amd64.deb
    cp "$deb" "$CANDIDATE/$CANDIDATE_FILE"
    (cd "$CANDIDATE" && sha256sum "$CANDIDATE_FILE" >SHA256SUMS)
    # The private half lives outside $WORK, which the containers mount, and only
    # until the signature is made.
    signer=$(mktemp -d)
    ssh-keygen -q -t ed25519 -N '' -C "$TEST_SIGNER" -f "$signer/key" &&
        ssh-keygen -q -Y sign -f "$signer/key" -n vakr-release "$CANDIDATE/SHA256SUMS" &&
        { echo "# $TEST_SIGNER: the throwaway key that signed this run's package under test"
          echo "vakr-release namespaces=\"vakr-release\" $(cut -d' ' -f1,2 "$signer/key.pub")"
        } >"$CANDIDATE/allowed-signers"
    local made=$?
    rm -rf "$signer"
    return "$made"
}

update_runs() {
    # update_runs CURSOR: log the collector's runs since CURSOR from PID 1's journal
    # (journal.py); fails when the collector crashed or failed in that span.
    on journalctl --after-cursor "$1" -o json --no-pager _PID=1 UNIT=vakr-collector.service \
        >"$WORK/update-journal.json" || return 1
    uv run --quiet python -m tests.system.resilience.journal --label self-update \
        "$WORK/update-journal.json" >"$WORK/update-runs.tsv"
    local rc=$?
    cat "$WORK/update-runs.tsv" >>"$EVENTS"
    return "$rc"
}

scenario_update() {
    section "S0: self-update while collecting, $FROM_VERSION -> $TO_VERSION (MVP-ACC-07, UPD-002, UPD-006)"
    local old before cursor started took restarts
    check "the node runs $FROM_VERSION" bash -c \
        "[ \"\$(docker exec $NODE vakr --version)\" = 'vakr $FROM_VERSION' ]"
    old=$(invocation)
    before=$(incident_count)
    cursor=$(on journalctl -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')
    check "trust the run's throwaway signer beside the release key (FROM node only)" \
        on_sh "cat /work/candidate/allowed-signers >>/opt/vakr/share/release-signers"
    mid_poll
    started=$SECONDS
    check "vakr self-update installs $TO_VERSION while the collector and the API run" on_sh \
        "vakr self-update --yes --file /work/candidate/$CANDIDATE_FILE \
        --sums /work/candidate/SHA256SUMS --sig /work/candidate/SHA256SUMS.sig >/tmp/self-update.log 2>&1"
    took=$((SECONDS - started))
    on cat /tmp/self-update.log | grep -E '^vakr self-update:|rollback|CRITICAL' | indent
    note "self-update took $took s"
    check "now running $TO_VERSION" bash -c "[ \"\$(docker exec $NODE vakr --version)\" = 'vakr $TO_VERSION' ]"
    check "the update record says success: no automatic rollback (UPD-006)" \
        on grep -q '"result": "success"' /var/lib/vakr-update/update-state.json
    check "both databases are at the current schema" on vakr db status
    check "the package's release signers replaced the throwaway key" \
        on_sh "! grep -q $TEST_SIGNER /opt/vakr/share/release-signers"
    check "no collector run crashed or failed during the update" update_runs "$cursor"
    restarts=$(grep -c '^run_start' "$WORK/update-runs.tsv")
    note "the update restarted the collector $restarts time(s)"
    check "the update restarted the collector" test "$restarts" -ge 1
    check "the $FROM_VERSION run logged a clean stop (16.4)" on_sh \
        "journalctl _SYSTEMD_INVOCATION_ID=$old --no-pager | grep -q collector.stopped"
    check "a clean update records no telemetry-loss incident" test "$(incident_count)" -eq "$before"
    check "vakr-api is back and answers on its socket" wait_until 60 api_mentions /api/v1/health/ready ready
    steady "S0"
}
