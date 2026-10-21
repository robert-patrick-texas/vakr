# Keeping tools and dependencies current

vakr always builds with the latest stable Python, Go, uv, ruff, and ty (blueprint section 16.1), and builds the web UI with the newest Node.js LTS (ADR-035). Tools and dependencies are checked for updates at least once per release. This page explains how to run the checks, how to read their output, and what to do with each finding.

| Command | What it does | Requirement |
|---|---|---|
| `make updates` | Lists every pinned toolchain (Python, Go, Node), PyPI and npm dependency, action, and tool version next to the latest stable release, with release-notes links. | TOOLS-001 |
| `make audit` | Fails if any locked runtime or development dependency has a known vulnerability. | QG-008 |
| Dependabot | Opens weekly pull requests for GitHub Actions and `uv` dependencies. | TOOLS-002 (supplement only) |

## Running the update check

```sh
make updates                                     # table; always exits 0
make updates UPDATES_FLAGS=--json                # JSON for scripts
make updates UPDATES_FLAGS=--fail-on-outdated    # exit 1 if anything is behind
```

`make updates` runs [`tools/check-updates`](../../tools/check-updates/README.md), a portable tool that is configured only by [`tools/check-updates/check-updates.toml`](../../tools/check-updates/check-updates.toml). It needs network access to python.org, go.dev, nodejs.org, PyPI, the npm registry, and GitHub. Standard `HTTPS_PROXY` and `NO_PROXY` settings are honoured. If the GitHub CLI is installed and logged in (`gh auth status`), GitHub lookups use it, which avoids the 60-requests-an-hour limit for anonymous API calls. The check reads files and runs `uv lock --upgrade --dry-run` and `node --version`; it never changes the repository and never runs npm.

## Reading the output

Each row has five columns:

| Column | Meaning |
|---|---|
| ITEM | What was checked. Workflow pins are shown as `owner/repo@ref`, with a commit SHA shortened to seven characters. |
| CURRENT | The version this repository pins or the installed binary reports. `-` when nothing is pinned. |
| LATEST | The newest stable upstream release. Pre-releases, drafts, and yanked releases are never shown here. |
| STATUS | `current`, `update available`, or `unknown`. |
| RELEASE NOTES | Where to read what changed in LATEST. |

Below the table, each `unknown` row and each `update available` row that has extra detail gets one line of explanation. The last line counts the rows by status.

How to read the statuses:

- **current**: nothing to do. A pin is compared at the precision it is written with. `3.15` in `.python-version` is current while 3.15.x is the newest series, `actions/checkout@v7` is current while 7.y.z is the newest, and a `go.mod` directive is compared by minor version. Patch releases inside a pinned series still appear in the more precise rows, such as *Python (interpreter uv selects)*, *Go (installed)*, and *Node (installed)*.
- **Node** rows compare with the newest **LTS** release, never with a Current one. `24` in `web/.node-version` and an `engines.node` floor of `24.22.0` are current while 24.x is the newest LTS line; the line under the table names the newest Current release for information. A new major line is adopted once nodejs.org promotes it to LTS.
- **npm** rows compare the version locked in `web/package-lock.json` with the registry's `latest` release, for every package `web/package.json` lists. The detail says whether it is *bundled* into the shipped web UI (`dependencies`) or a *development* tool (`devDependencies`).
- **update available**: apply the update or defer it (see the release policy below).
- **unknown**: the tool could not decide, and the line under the table says why. Typical reasons:
  - a network failure or rate limit, in which case you re-run;
  - a tool that is not installed on this host, such as `nfpm` or `gitleaks`;
  - an item listed for reference only, such as `python-build-standalone`, which is not pinned until packaging lands (PKG-002);
  - a commit-pinned action without a `# vX.Y.Z` comment.

  An `unknown` never fails `--fail-on-outdated`, so an outage cannot block work. That also means a release must not treat it as "current".

Some items appear twice on purpose:

- `markdown-it-py` appears once as a locked dev dependency and once as a pin in `tools/md2html`'s inline script metadata. The two pins are independent and both must move.
- The row `uv.lock (all packages)` summarises what `uv lock --upgrade` would change. It adds rows only for packages that are not already listed by name, which are usually transitive dependencies.

## Release-time policy (TOOLS-002)

Step 1 of the release process (blueprint section 28.7) is:

1. Run `make updates` and `make audit` on an up-to-date `main`.
2. For every `update available` row, either **apply** the update or **defer** it.
   - **Apply** it in the release branch, then run `make check`.
   - **Defer** it by adding a line to `CHANGELOG.md` under the release's `### Deferred updates` heading. Name the item, the current and latest versions, the reason, and when it will be revisited, for example:

     ```markdown
     ### Deferred updates

     - ruff 0.17.1 → 0.18.0: new default rules need a separate clean-up change; revisit in the next release.
     ```
3. Resolve every `unknown` row that is not reference-only. Re-run after a network failure, or check the item by hand and record the result like a deferral.
4. `make audit` must pass. A vulnerability is either fixed by upgrading, or recorded with a justification in the ignore list (see below).

Dependabot pull requests are welcome between releases, but they do not replace this step. Dependabot does not see the Python, Go, and Node toolchains, PEP 723 inline pins, the pip-audit pin in the `Makefile`, or the bundled interpreter. When the first `go.mod` lands, add a `gomod` entry to `.github/dependabot.yml` for its directory.

## Applying updates

| Item | How to update |
|---|---|
| A locked package (`ruff`, `ty`, `cryptography`, ...) | `uv lock --upgrade-package NAME`, then `uv sync` and `make check`. Raise the `>=` floor in `pyproject.toml` only when vakr starts relying on the new version. |
| Every locked package at once | `uv lock --upgrade`, then `uv sync` and `make check`. Read the release notes of every major version bump in the diff. |
| uv | `uv self update` on development hosts. CI installs the latest uv through `astral-sh/setup-uv`. |
| Go | Install the new release. Raise a `go.mod` directive (`go mod edit -go=1.N`) only when adopting a new minor version. |
| A Go module requirement | In the module's directory, `go get MODULE@VERSION` and `go mod tidy`, then build and test that module. |
| An action with a tag pin (`@v7`) | Change the major version when a new one is released, after reading its release notes. |
| An action with a commit pin | Replace the SHA and the `# vX.Y.Z` comment together. `gh api repos/OWNER/REPO/commits/vX.Y.Z --jq .sha` prints the commit a tag points to. |
| md2html inline pins | Edit the pins in `tools/md2html/md2html.py` and the test command in its README together. |
| pip-audit | Edit `PIP_AUDIT` in the `Makefile`. |
| An npm package of the web UI | In `web/`: `npm install --save-exact --ignore-scripts NAME@VERSION` (add `--save-dev` for a development tool), which updates `package.json` and `package-lock.json` together; then `make check`. A major update of a bundled package needs its release notes read and the bundle-size budget in Appendix E.31 re-measured. |
| Node.js | Install the new LTS on development hosts and the release machine. Within a major line nothing else changes, because `web/.node-version` names only the major. A new major line moves `web/.node-version`, `engines` in `web/package.json`, and the builder image in `packaging/Dockerfile` together. |
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
- **`uv`** covers `pyproject.toml` and `uv.lock`. Minor and patch updates are grouped into one pull request, and major updates arrive separately.

Review a Dependabot pull request like any other change: CI must pass, and a major update needs its release notes read and, when operators are affected, a changelog entry.
