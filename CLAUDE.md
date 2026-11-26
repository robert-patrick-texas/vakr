# vakr — instructions for Claude and other agents

`tool-blueprint.md` is authoritative. Read only the sections relevant to the task; the whole file is too large to load every time.

## Where we are

- The current state, next steps, open decisions and follow-ups are in `STATUS.md` when it exists (a local file, not committed). This section holds the standing direction.
- The current goal is the MVP (blueprint §27). Follow the milestone order in §27.8, not the phase order in §20 (ADR-022).
- MVP scope means the Compact profile (SQLite/SQLite), SNMPv2c, Cisco IOS XE, and 100 devices / ~1,600 interfaces. Anything in §27.4's "Deferred" list is out of scope unless the owner says otherwise.
- Default values come from Appendix E. Do not invent thresholds; if one is missing, add it to Appendix E in the same change.
- Breaking changes are allowed before enterprise readiness (ADR-031). Config migrations (§28.4) and data migrations are still required.

## Non-negotiables (from blueprint §4.3, §17, and §25)

- Missing data is never zero. Collector or storage trouble is never reported as target-down. `ifIndex` is never identity.
- No SQL outside adapter modules. No database-driver types outside adapters. No blocking calls on the event loop.
- Every queue, retry, query, export, and concurrency limit is bounded.
- Secrets never appear in logs, exceptions, reprs, fixtures, bundles, or test data.
- Files under `src/` stay at or below 300 lines and functions at or below 50 lines.
- Every module needs a Google-style docstring: purpose, network context, dependencies, safety, and an example.
- Every function needs a Google-style docstring with Args, Returns, and Raises sections. Write "None" for an empty section.
- Comments explain *why*, not *what*.
- Cite requirement and test IDs (for example ICMP-004, RATE-T03) in tests, commits, and work reports. Update `docs/traceability.md` in the same change.
- Never silently resolve a blueprint contradiction. Record it as an ADR or issue, state the choice, and keep going.

## Project mechanics

- **Version:** the root `VERSION` file is the only place the version appears (VER-001). Never hard-code it anywhere else.
- **Toolchain:** use the latest stable Python (3.15), Go (1.27), uv, ruff, and ty. Run Python through `uv`.
- **Dependencies:** adopt every new release, however recently it was published. Hold one back only after it fails vakr's tests, as an exception in `tools/check-updates/update-exceptions.toml` (ADR-036, TOOLS-004). Never defer for any other reason.
- **Portable tools** under `tools/` and `src/vakr/lifecycle/` import nothing from vakr and take project values from arguments or config (§29).
- **Documentation:** every `.md` file is published to GitHub Pages by `tools/md2html`. Keep internal links valid; CI runs `--check`.
- **Never commit** `_site/`, `inbox/`, field bundles, or anything resembling credentials.
- **Production** runs at a separate site. When production data is needed, extend the field kit (§28.5) instead of asking the operator for manual steps.

## Releases

- **Never mark a release pre-release**, and never give a version an `-rc` suffix, unless the owner asks. `tools/release/release.toml` keeps `prerelease = "never"`; don't pass `--prerelease` to `gh release`. Local test builds (`VERSION_OVERRIDE`) use the next patch version, as `packaging/package-test.sh` does, and go outside `dist/` (for example `dist/s0-fix/`), which holds release copies.
- **Release notes:** a summary of one to three sentences, then `###` New, Changed, Fixed and Additional Notes in that order, leaving out empty ones. Write the CHANGELOG section in that layout; the preflight stops a release that breaks it. `tools/release` appends the lines-of-code and versions tables: never write them by hand, and don't regenerate them for 0.6.2 or earlier releases. A dependency held back after a failed test is named under Additional Notes.
- **The owner signs and publishes** with `uv run tools/release/release.py`; agents never sign. While the owner may run it, keep this checkout on `main` with a clean tree, or the preflight stops.
- **After each release**, run `make github-update-test` (FROM defaults to the previous release).

## Gotchas

- `check-version` scans only git-tracked files: `git add` new files before `make check`, or CI finds what the local run missed. A version literal equal to the current `VERSION`, even in a comment, also trips it; use other example versions.
- `cmd | tail -1 && git commit` commits even when `cmd` fails, because the pipe's status is `tail`'s. Run a check on its own, or with `set -o pipefail`.
- Never wait with `pgrep -f '<text>'` when the text is in the waiter's own command line: it matches itself and never exits. Wait on a result file, or run the command in the background.
- A `sqlite3` `with` block doesn't close the connection; use `contextlib.closing`.
- Two `conftest.py` files share the `pythonpath`: give helper modules unique names.
- Strict config validation must not block recovery commands (`strict=False`).
- Testing secrets in development needs root:root, directory 0700, file 0600, then `sudo env VAKR_ROOT=... .venv/bin/vakr ...`. Strict mode can't be turned off for services.
- In a systemd-booted container `/tmp` is a tmpfs, so `docker cp` from `/tmp` fails; copy from another directory.
- Parallel agents each need their own Playwright port and container name, and their migration and Appendix E numbers assigned up front.
- Reproduce a flaky test before naming its cause.
- Transient CI failures (npm audit "Service Unavailable", an apt mirror exit 100): read the log for code errors, then `gh run rerun <id> --failed`.
- Dependabot's uv updates fail because it doesn't support Python 3.15; use `make updates`.
- `netperfmon`, the blueprint's old working name, means vakr; treat any remaining use as stale.

## Agent work report (blueprint §20.1)

When finishing a task, report:

- the requirement IDs addressed;
- the files changed;
- the tests added and their exact results;
- the documentation and traceability updates made;
- any known limitations;
- confirmation that no `src/` file exceeds 300 lines and that no secrets were added.
