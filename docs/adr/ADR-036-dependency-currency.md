# ADR-036: Dependency currency: newest releases, holds only after failed tests

| Field | Value |
|---|---|
| Status | Accepted 2026-10-27 (owner's direction: always check for the newest versions, even ones published the same day; hold a version back only when it fails vakr's tests, and revisit it at the dependency's next release). The details below are mine and open to owner review. |
| Date | 2026-10-27 |
| Supersedes | The "apply or defer with a reason" rule of TOOLS-002 and blueprint 28.7 step 1 |
| Decides | Which versions of its toolchain and dependencies vakr ships on, when an update may wait, how a hold is recorded and revisited, and when the release checks |
| Requirements | Blueprint 16.1 (toolchain currency), 28.7, 28.8 (TOOLS-001, TOOLS-002, TOOLS-004, TOOLS-005; TOOLS-T06 to TOOLS-T10) |

## Context

TOOLS-002 asked each release to run `tools/check-updates` and either apply each update or defer it with a reason in the changelog. Any reason would do, and in practice the reasons varied: an update was not yet installable, another package did not support it yet, or a policy kept it on an older line. Several things also delayed new releases without anyone choosing to:

* **Dependabot** applies a 3-day cooldown to version updates when `cooldown` is not configured. A release is not proposed until 3 days after it is published. The TypeScript 7 pull request waited on this.
* **npm** has `min-release-age` (null by default) and **uv** has `exclude-newer` (unset by default). A setting in a developer's `~/.npmrc` or `uv.toml`, or `UV_EXCLUDE_NEWER`, would hide recent releases from installs and from the lockfile check.
* **The check ran once**, during release preparation. A release cut hours later, after CI, did not look again, so a dependency published in between was missed.
* **Builder-image pins** were not checked at all. `packaging/Dockerfile` pins uv (which also picks the bundled CPython patch release), Go (which builds nfpm), and nfpm itself. Only the developer's installed `uv`, `go`, and `nfpm` were compared, and nfpm showed as `unknown` on every host without it.

On 2026-10-27 the report showed 17 updates available, including uv 0.13.6 and CPython 3.15.2, both published within the previous day, and TypeScript 7.0.2.

## Decision

1. **vakr ships on the newest release of everything** check-updates reports: toolchains, PyPI and npm packages, actions, builder-image pins, and tool pins. A release's age is never a reason to wait. One published the same day is applied like any other. "Newest" keeps the meanings the project already uses: stable releases only, Node from LTS lines only (ADR-035), and `@types/node` from the major line of `web/.node-version`. Type definitions for a Node release the build does not run would let code type-check against APIs that fail at runtime. This last point is my reading of the direction; it replaces Dependabot's `@types/node` major-version ignore as the only place the rule lived.
2. **The only reason to stay behind is a failed vakr test** (`make check`, `make package-test`, or another vakr suite). A release that cannot be installed or built at all also counts, such as a CPython patch release that uv offers no download for yet. Then vakr stays on the last-known-good version and records an **exception** in `tools/check-updates/update-exceptions.toml`. The exception names the report item, the failing version, what failed, and the date. The release's changelog lists every hold under "Held dependencies". For PyPI packages, a `NAME<VERSION` entry in `[tool.uv] constraint-dependencies` keeps `uv lock --upgrade` from undoing the hold; npm versions are already exact.
3. **Holds are revisited automatically.** check-updates shows a held item as `held` only while the failing version is still the newest. When the dependency publishes again, the row turns `update available` with a note to retest, and the release gate fails until someone tests it. The exception is then removed (pass) or moved to the new version (fail). An exception that no longer matches the report is an error: nothing pending, a version newer than the latest, or an unknown item.
4. **The release checks when it starts.** `tools/release` runs `check-updates --gate` in preflight (`update_check` in `release.toml`). The gate fails on an available update, on an item that could not be checked (`unknown`), and on a stale exception. `held` rows and `reference` rows (items vakr does not pin, such as `python-build-standalone` and `gitleaks`) pass.
5. **No tool may impose a minimum release age.** Every Dependabot entry sets `cooldown: exclude: ["*"]`. `web/.npmrc` sets `min-release-age=0`, which outranks a developer's `~/.npmrc`. The lockfile check passes a far-future `--exclude-newer`, which overrides any `uv.toml` or `UV_EXCLUDE_NEWER` cutoff. A test keeps all three in place.
6. **Builder-image pins are checked.** check-updates reads uv, Go, and nfpm from `packaging/Dockerfile`. The Node image is already tied to `web/.node-version` by a packaging test.

The first exception: **TypeScript 7.0.2**, tested on 2026-10-27 in a copy of `web/`. typescript-eslint 8.72.0 refuses TypeScript 7.0 (8.72.1, adopted the same day, still does), and SvelteKit 3.1.0 crashes loading `tsconfig.json` (`Cannot read properties of undefined (reading 'readFile')`). That breaks lint, svelte-check, the unit tests, the API-types check, and the build. The same copy passes all five on 6.0.3, so vakr stays on 6.0.3.

## Alternatives

* **Keep "apply or defer" (TOOLS-002 as it was).** Rejected by the owner. Deferrals collected reasons other than test failures, and nothing brought them back.
* **A cooldown of a few days as supply-chain protection.** A cooldown gives the ecosystem time to notice and yank a compromised release. The owner chose currency instead. The remaining protections are hash-locked installs (`uv.lock`, `package-lock.json`, `--require-hashes` in the package build), `make audit` and `npm audit`, the vakr test suites, and release review.
* **Exceptions in `CHANGELOG.md` only.** Free text cannot be checked, so nothing would notice a newer release or a stale entry. The changelog still lists holds for operators, copied from the file.
* **Exceptions in `check-updates.toml`.** That file says what to check. Holds are decisions that change with every release. A separate file, like `audit-ignore.txt`, keeps the two apart.
* **Run the gate in CI on every push.** Every upstream release would turn `main` red with nothing wrong in vakr. The gate belongs where the decision is made: at release time.
* **Report the newest release on the held line** (6.0.4 while 7.0.2 is held). That needs every version of every held package, which PyPI and npm serve only as large documents. It is a manual step in the procedure for now.

## Consequences

* **Benefits.** Security and bug fixes reach production with the next release, however recently they were published. Every hold carries evidence and comes back for retesting by itself. The bundled CPython follows uv's newest download.
* **Risks.** A release can stop in preflight because something was published after CI went green, and a busy dependency such as hypothesis, which publishes almost daily, can do that more than once in a day. Recovery is to update, commit, wait for CI, and run the release again. Without a cooldown, a compromised release could be adopted before it is noticed. The protections above, and reading release notes for every major version, are what remain.
* **Security impact.** As above: currency gets fixes in sooner, and the lack of a cooldown removes one supply-chain safeguard. Nothing changes for production hosts, which install only signed releases.
* **Performance impact.** The release preflight takes about 30 to 60 seconds longer for the update check. The `@types/node` line lookup downloads about 2 MB.
* **Compatibility and migration.** None for operators. For developers, `make updates` gains `held` and `reference` statuses and a `--gate` flag, and its JSON has a `problems` list. `release.toml` gains `update_check`.
* **Test changes.** TOOLS-T06 to TOOLS-T10: `tools/check-updates/test_updates_gate.py`, `test_updates_lines.py`, `test_updates_shipped.py`, `tools/release/test_release_currency.py`, `tests/unit/test_dependency_currency.py`.
* **Documentation changes.** Blueprint 16.1, 28.7, 28.8, and Appendix C; [keeping tools and dependencies current](../development/updates.md); [releasing vakr](../development/release.md); the check-updates and release tool READMEs; `CLAUDE.md`; `CHANGELOG.md`.
* **Revisit** if a release adopted without a cooldown ships a compromised or yanked dependency, or if preflight stops on newly published releases often enough to hold up releases. Answers then could be a short cooldown for some ecosystems, or a gate that accepts releases published after CI started.
