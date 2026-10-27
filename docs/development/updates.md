# Keeping tools and dependencies current

vakr builds and ships with the newest release of every tool and dependency: the latest stable Python, Go, uv, ruff, and ty (blueprint section 16.1), the newest Node.js LTS for the web UI (ADR-035), and the newest release of every PyPI and npm package, action, and builder-image pin. A release's age never matters: one published the same day is adopted like any other (ADR-036). The only reason to stay behind is that the new release failed vakr's tests. That is recorded as an exception, vakr stays on the last-known-good version, and the exception is revisited as soon as the dependency publishes its next release (TOOLS-004). This page explains how to run the checks, how to read their output, and what to do with each finding.

| Command | What it does | Requirement |
|---|---|---|
| `make updates` | Lists every pinned toolchain (Python, Go, Node), PyPI and npm dependency, action, builder-image pin, and tool version next to the latest stable release, with release-notes links. | TOOLS-001 |
| `make updates UPDATES_FLAGS=--gate` | The release gate: exits 1 unless every item is current or held by a recorded exception. `tools/release` runs it in preflight. | TOOLS-005 |
| `make audit` | Fails if any locked runtime or development dependency has a known vulnerability. | QG-008 |
| Dependabot | Opens weekly pull requests for GitHub Actions, `uv`, and npm dependencies, with no waiting period. | TOOLS-002 (supplement only) |

## Running the update check

```sh
make updates                                     # table; always exits 0
make updates UPDATES_FLAGS=--json                # JSON for scripts
make updates UPDATES_FLAGS=--fail-on-outdated    # exit 1 if anything is behind
make updates UPDATES_FLAGS=--gate                # the release gate (TOOLS-005)
```

`make updates` runs [`tools/check-updates`](../../tools/check-updates/README.md), a portable tool that is configured only by [`tools/check-updates/check-updates.toml`](../../tools/check-updates/check-updates.toml) and reads the exceptions in [`tools/check-updates/update-exceptions.toml`](../../tools/check-updates/update-exceptions.toml). It needs network access to python.org, go.dev, nodejs.org, PyPI, the npm registry, and GitHub. Standard `HTTPS_PROXY` and `NO_PROXY` settings are honoured. If the GitHub CLI is installed and logged in (`gh auth status`), GitHub lookups use it, which avoids the 60-requests-an-hour limit for anonymous API calls. The check reads files and runs `uv lock --upgrade --dry-run` and `node --version`; it never changes the repository and never runs npm.

## Reading the output

Each row has five columns:

| Column | Meaning |
|---|---|
| ITEM | What was checked. Workflow pins are shown as `owner/repo@ref`, with a commit SHA shortened to seven characters. Exceptions name items exactly as printed here. |
| CURRENT | The version this repository pins or the installed binary reports. `-` when nothing is pinned. For a `held` row, this is the last-known-good version. |
| LATEST | The newest stable upstream release, however recently it was published. Pre-releases, drafts, and yanked releases are never shown here. |
| STATUS | `current`, `update available`, `held`, `reference`, or `unknown`. |
| RELEASE NOTES | Where to read what changed in LATEST. |

Below the table, each `unknown` row, each `held` row, and each `update available` row that has extra detail gets one line of explanation, followed by any problem with a recorded exception. The last line counts the rows by status; with `--gate`, one more line says whether the gate passed and names what blocks it.

How to read the statuses:

- **current**: nothing to do. A pin is compared at the precision it is written with. `3.15` in `.python-version` is current while 3.15.x is the newest series, `actions/checkout@v7` is current while 7.y.z is the newest, and a `go.mod` directive is compared by minor version. Patch releases inside a pinned series still appear in the more precise rows, such as *Python (interpreter uv selects)*, *Go (installed)*, and *Node (installed)*.
- **Node** rows compare with the newest **LTS** release, never with a Current one. `24.22.1` in `web/.node-version` and an `engines.node` floor of `24.22.0` are current while 24.22.1 is the newest LTS release; the line under the table names the newest Current release for information. A new major line is adopted once nodejs.org promotes it to LTS.
- **npm** rows compare the version locked in `web/package-lock.json` with the registry's `latest` release, for every package `web/package.json` lists. The detail says whether it is *bundled* into the shipped web UI (`dependencies`) or a *development* tool (`devDependencies`). `@types/node` is the one exception: it describes the Node release the build runs, so it is compared with the newest release on the major line of `web/.node-version` (`[npm] lines`).
- **Builder-image pins** in `packaging/Dockerfile` have their own rows: *uv (packaging/Dockerfile)*, *Go (packaging/Dockerfile)*, and *nfpm (packaging/Dockerfile)*. The builder's uv also decides which CPython patch release the package bundles, so it moves together with the developer uv. The Node image is kept equal to `web/.node-version` by a packaging test.
- **update available**: apply the update (see the release policy below).
- **held**: a recorded exception holds the item, because its latest release failed vakr's tests and nothing newer has been published. The detail names the failed release, the date, and what failed. Nothing to do until the row turns `update available`.
- **reference**: vakr pins nothing for this item, and the row shows the latest release for information: `python-build-standalone`, which the uv version chooses, and `gitleaks`, which CI's gitleaks-action chooses.
- **unknown**: the tool could not decide, and the line under the table says why. Typical reasons:
  - a network failure or rate limit, in which case you re-run;
  - a commit-pinned action without a `# vX.Y.Z` comment;
  - a pin the configured regex no longer finds, after a file was edited.

  An `unknown` never fails `--fail-on-outdated`, so an outage cannot block work. It does fail `--gate`: a release must not claim a check that never happened.

Some items appear twice on purpose:

- `markdown-it-py` appears once as a locked dev dependency and once as a pin in `tools/md2html`'s inline script metadata. The two pins are independent and both must move.
- `uv` appears for the developer host and for the package builder image. Both must move.
- The row `uv.lock (all packages)` summarises what `uv lock --upgrade` would change. It adds rows only for packages that are not already listed by name, which are usually transitive dependencies. Its command passes a far-future `--exclude-newer`, which overrides any `exclude-newer` a developer's `uv.toml` or `UV_EXCLUDE_NEWER` sets, so a cutoff on one host cannot hide a new release.

## Release-time policy (TOOLS-002)

vakr stays on the newest release of everything, and the release gate enforces it twice: when the release is prepared, and again when `tools/release` starts (TOOLS-005), because a dependency may publish in between.

1. Run `make updates` and `make audit` on an up-to-date `main`.
2. Apply every `update available` row (see [Applying updates](#applying-updates)), in the release branch or on `main`, then run `make check`, and `make package-test` when anything in the package or its builder moved. How new the release is does not matter. A release published the same morning is applied like any other.
3. When an update **fails vakr's tests**, and only then. A release that cannot be installed or built at all counts as a failure too, for example a CPython patch release that uv offers no download for yet, or a tool release with no Linux build:
   1. Go back to the last-known-good version, the one CURRENT shows. If a newer release exists on that line (6.0.4 while 7.0.2 fails), try it and keep it if it passes.
   2. Record an exception in [`tools/check-updates/update-exceptions.toml`](../../tools/check-updates/update-exceptions.toml): the ITEM as printed, the failing `version`, the `reason` (which test or command failed, with the error), and the `recorded` date.
   3. Make the hold stick. For a PyPI package, add `NAME<VERSION` (the first failing version) to `constraint-dependencies` under `[tool.uv]` in `pyproject.toml`, so `uv lock --upgrade` keeps the last-known-good version. For an npm package nothing more is needed, because `web/package.json` pins exact versions. For a toolchain or image pin, leave the pin. Close any Dependabot pull request for the failing version with `@dependabot ignore this version`. Dependabot still proposes the next one.
   4. List the hold in the release's `CHANGELOG.md` section under `### Held dependencies (TOOLS-004)`, naming the item, the version kept, the version that failed, and why:

      ```markdown
      ### Held dependencies (TOOLS-004)

      - **TypeScript 7.0.2** (staying on 6.0.3): typescript-eslint 8.72.0 refuses TS 7.0, and SvelteKit 3.1.0 crashes loading `tsconfig.json`, so lint, svelte-check, the unit tests, and the build fail. Revisited with the next TypeScript release.
      ```
4. Revisit every exception when the dependency publishes its next release. check-updates shows the row as `update available` again as soon as one appears, and the gate fails until it is dealt with. Test the new release. If it passes, apply it and delete the exception and any constraint. If it fails, move the exception's `version` to the new release and update the reason. A test failure caused by another package (TypeScript 7 waiting for SvelteKit) is still revisited when the held package publishes, and also whenever the package that blocked it moves.
5. Resolve every `unknown` row. Re-run after a network failure; fix the config when a pin moved.
6. `make audit` must pass. A vulnerability is either fixed by upgrading, or recorded with a justification in the ignore list (see below).
7. `make updates UPDATES_FLAGS=--gate` must end with `check-updates: gate passed`. A release cannot start otherwise: its preflight runs the same gate.

Recorded exceptions that no longer match the report also fail the gate: one whose item has no update pending (remove it), one that names a release newer than the latest (correct it), and one whose item is not in the report (fix the name).

Dependabot pull requests are welcome between releases, but they do not replace this step. Dependabot does not see the Python, Go, and Node toolchains, PEP 723 inline pins, the pip-audit pin in the `Makefile`, the builder image, or the bundled interpreter. When the first `go.mod` lands, add a `gomod` entry to `.github/dependabot.yml` for its directory, with the same `cooldown` block as the others.

## Applying updates

| Item | How to update |
|---|---|
| A locked package (`ruff`, `ty`, `cryptography`, ...) | `uv lock --upgrade-package NAME`, then `uv sync` and `make check`. Raise the `>=` floor in `pyproject.toml` only when vakr starts relying on the new version. |
| Every locked package at once | `uv lock --upgrade`, then `uv sync` and `make check`. Read the release notes of every major version bump in the diff. A held package stays where it is because of its `constraint-dependencies` entry. |
| uv | `uv self update` on development hosts. CI installs the latest uv through `astral-sh/setup-uv`. In `packaging/Dockerfile`, change the `ghcr.io/astral-sh/uv` tag and its digest together (`docker buildx imagetools inspect ghcr.io/astral-sh/uv:X.Y.Z` prints the digest) and the expectation in `packaging/tests/test_package_spec.py`; the new uv may bundle a newer CPython patch release, so run `make package-test`. |
| Go in the package builder, nfpm | Change the `golang` image tag and digest, or the `nfpm@vX.Y.Z` version, in `packaging/Dockerfile`, together with `packaging/tests/test_package_spec.py`, then `make package-test`. |
| Go | Install the new release. Raise a `go.mod` directive (`go mod edit -go=1.N`) only when adopting a new minor version. |
| A Go module requirement | In the module's directory, `go get MODULE@VERSION` and `go mod tidy`, then build and test that module. |
| An action with a tag pin (`@v7`) | Change the major version when a new one is released, after reading its release notes. |
| An action with a commit pin | Replace the SHA and the `# vX.Y.Z` comment together. `gh api repos/OWNER/REPO/commits/vX.Y.Z --jq .sha` prints the commit a tag points to. |
| md2html inline pins | Edit the pins in `tools/md2html/md2html.py` and the test command in its README together. |
| pip-audit | Edit `PIP_AUDIT` in the `Makefile`. |
| An npm package of the web UI | In `web/`: `npm install --save-exact --ignore-scripts NAME@VERSION` (add `--save-dev` for a development tool), which updates `package.json` and `package-lock.json` together; then `make check`. A major update of a bundled package needs its release notes read and the bundle-size budget in Appendix E.31 re-measured. |
| Node.js | Install the new LTS on development hosts and the release machine, and move `web/.node-version` and the `node` builder image (tag and digest) in `packaging/Dockerfile` together; a packaging test keeps them equal. A new major line also moves `engines` in `web/package.json`, and `@types/node` follows it to the new line. |
| A hand-vendored front-end file | Replace the file, then update its `version` in `check-updates.toml`. vakr vendors none: the web UI's packages are locked by npm. |

## Moving to a new Python minor version (TOOLS-003)

A new CPython minor release (for example 3.16.0) appears as `update available` on the `.python-version` and `requires-python` rows. Moving to it is a normal release change. Everything below moves in the same change, and the release notes say so.

1. **`.python-version`**: set the new minor version, for example `3.16`. CI, `uv run`, and `uvx` all read this file.
2. **`pyproject.toml`**:
   - `requires-python = ">=3.16"`;
   - the `Programming Language :: Python :: 3.16` classifier;
   - `[tool.ruff] target-version = "py316"`;
   - `[tool.ty.environment] python-version = "3.16"`.
3. **Lockfile**: `uv lock`, then `uv sync`, then `make check`. Fix anything the new interpreter or the newer lint target reports.
4. **Bundled interpreter**: once packaging exists (PKG-002), point the package build at a python-build-standalone release that provides the new minor version. The `python-build-standalone` row shows the newest release.
5. **CI**: every job takes its interpreter from `.python-version` through `astral-sh/setup-uv`. If a job or matrix names a Python version explicitly, change it too.
6. **Portable tools**: raise `requires-python` in each tool's PEP 723 header (`tools/*/*.py`) if it names a lower version.
7. **Documentation**: the toolchain line in `CLAUDE.md`, blueprint section 16.1, and `CHANGELOG.md`, which must state the new Python version and any operator impact.

Re-run `make updates` afterwards. The Python rows should all be `current`.

## Dependency vulnerability audit (QG-008)

`make audit` exports the locked runtime and development dependencies (`uv export --frozen --format requirements-txt --all-groups`) and scans them with [pip-audit](https://pypi.org/project/pip-audit/), pinned in the `Makefile` and run through `uvx`. Because the export has hashes for every package, pip-audit installs nothing. `--strict` makes a package that cannot be audited a failure too. The CI `audit` job runs the same command on every push and pull request.

When a vulnerability is reported, upgrade the package (`uv lock --upgrade-package NAME`) if a fixed version exists. Only when the vulnerability provably cannot affect vakr, or no fix exists yet, add its ID to [`tools/check-updates/audit-ignore.txt`](../../tools/check-updates/audit-ignore.txt):

```text
GHSA-aaaa-bbbb-cccc  # only the XML parser is affected; vakr never calls it; RP; review 2027-01-31
```

Each entry needs a `#` justification that says why vakr is unaffected, who accepted the risk, and when to review it. `make audit` rejects an entry without one. Remove the entry as soon as a fixed version is locked.

uv also ships an `uv audit` command, but it is marked experimental and its output format may change. The gate uses pip-audit until `uv audit` is stable.

## Dependabot

[`.github/dependabot.yml`](../../.github/dependabot.yml) asks for weekly pull requests:

- **`github-actions`** covers workflow `uses:` pins. It also updates the version comment after a commit-SHA pin, which check-updates relies on.
- **`uv`** covers `pyproject.toml` and `uv.lock`. Minor and patch updates are grouped into one pull request, and major updates arrive separately. Dependabot cannot resolve Python 3.15 yet, so these jobs fail; `make updates` covers the same ground.
- **`npm`** covers `web/package.json` and `web/package-lock.json`, grouped the same way. It ignores `@types/node` major versions, which follow the Node line.

Dependabot waits 3 days after a release before proposing it unless told otherwise. Every entry therefore sets `cooldown: exclude: ["*"]`, so a release is proposed on the next run however new it is (ADR-036). npm's own `min-release-age` is set to 0 in `web/.npmrc` for the same reason, which also overrides a developer's `~/.npmrc`. `tests/unit/test_dependency_currency.py` keeps both settings, and the uv override above, in place (TOOLS-T10).

Review a Dependabot pull request like any other change: CI must pass, and a major update needs its release notes read and, when operators are affected, a changelog entry. When it fails vakr's tests, record an exception as for any other update and close the pull request with `@dependabot ignore this version`.
