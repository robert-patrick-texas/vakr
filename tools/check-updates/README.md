# check-updates

A small, portable report of everything a project pins (toolchain, locked dependencies, CI actions, vendored assets) next to the latest stable upstream release, with a link to the release notes.

check-updates answers one question before a release: *what is out of date, and where do I read about the new version?* With `--gate` it is also the release gate: it fails until every item is current, or is held back by a recorded exception because its newest release failed the project's tests. It compares:

| What | Current version from | Latest stable from |
|---|---|---|
| Python | `.python-version` (minor series), `requires-python` floor, the interpreter `uv python find` selects | python.org release API |
| Go | `go version`, other files that name a Go release (`pins`, such as a builder image tag), the `go` directive of every `go.mod` (minor series) | `go.dev/dl/?mode=json` |
| Go modules | the direct requirements of every `go.mod` | `go list -m -u -json all` (the Go module proxy) |
| Node.js | a version file such as `.node-version` (as written, usually the major line), the `engines.node` floor of `package.json` (major line), `node --version` | the newest LTS release in `nodejs.org/dist/index.json`; the newest Current release is named for information |
| PyPI packages | `uv.lock`, for every direct dependency, optional extra, and dependency-group member in `pyproject.toml` | PyPI JSON API |
| The whole lockfile | `uv lock --upgrade --dry-run`, which also covers transitive packages | the resolver |
| npm packages | `package-lock.json`, for every `dependencies` and `devDependencies` entry of `package.json` | the registry's `latest` dist-tag; for a package in `lines`, the newest release on the major line another file pins |
| GitHub Actions | `uses:` lines in workflows: tag pins (`@v7`) and commit pins with a `# vX.Y.Z` comment | GitHub releases |
| Tools and other pins | a command's output (`uv --version`), a regex over a file (a Makefile pin, PEP 723 inline metadata), or a literal | PyPI, GitHub releases, or npm |
| Vendored front-end files | a literal version in the config, for files copied into the repository by hand | npm or GitHub releases |

Pre-releases (`a`, `b`, `rc`, `dev`, and SemVer suffixes such as `-beta.1`), drafts, yanked files, and non-CPython entries on python.org are never treated as the latest release. A release's age is never a reason to skip it: one published a minute ago is the latest release.

## Requirements

[uv](https://docs.astral.sh/uv/) and network access. The entry script declares its Python version (3.15 or newer) and its dependencies (none) as [PEP 723](https://peps.python.org/pep-0723/) inline metadata, so `uv run` needs to install nothing. The GitHub CLI (`gh`) is optional. When it is installed and logged in, GitHub lookups go through `gh api`, which raises the rate limit from 60 to 5,000 requests an hour. check-updates never reads the token itself.

## Usage

```sh
# Readable table (always exits 0)
uv run tools/check-updates/check_updates.py

# Machine-readable report
uv run tools/check-updates/check_updates.py --json

# Exit 1 when any update is available
uv run tools/check-updates/check_updates.py --fail-on-outdated

# Release gate: also exit 1 for an unknown row or a stale exception
uv run tools/check-updates/check_updates.py --gate

# Another repository, another config
uv run tools/check-updates/check_updates.py --root ../other --config ../other/check-updates.toml
```

| Option | Meaning |
|---|---|
| `--config PATH` | Configuration file. Default: `check-updates.toml` next to the script. |
| `--root DIR` | Repository root that config paths are relative to. Default: the current directory. |
| `--json` | Print `{"summary": {...}, "items": [...], "problems": [...]}` instead of the table. `problems` lists exceptions that no longer match the report. |
| `--fail-on-outdated` | Exit 1 if any item has an update available. |
| `--gate` | Exit 1 if any item has an update available or is `unknown`, or if any exception has a problem. `held` and `reference` rows pass. The table ends with a line that names what blocks. |
| `--timeout SECONDS` | Limit for each request or command. Overrides `settings.timeout_seconds`. |

Exit status: `0` for a completed report, `1` with `--fail-on-outdated` when an update is available or with `--gate` when anything blocks, and `2` for a configuration error, including an invalid exceptions file. An item that cannot be checked, for example because an API is unreachable or a tool is not installed, is reported as `unknown` with the reason. It never stops the run and never fails `--fail-on-outdated`, so an outage cannot block everyday work. It does fail `--gate`, because a release must not claim a check that never happened: re-run it.

## Statuses

| Status | Meaning |
|---|---|
| `current` | The pin is the latest release, at the precision it is written with. |
| `update available` | A newer release exists. Under an exception, the detail says the newer release must be tested. |
| `held` | An exception holds the item: its latest release failed testing, and nothing newer has been published yet. |
| `reference` | The repository pins nothing for this item; the row shows the latest release for information. |
| `unknown` | The tool could not decide; the line under the table says why. |

## Output

```text
ITEM                        CURRENT  LATEST   STATUS            RELEASE NOTES
Python (.python-version)    3.15     3.15.0   current           https://docs.python.org/release/3.15.0/whatsnew/changelog.html
tomlkit                     0.13.0   0.15.2   update available  https://github.com/python-poetry/tomlkit/blob/master/CHANGELOG.md
actions/checkout@v7         v7       v7.0.1   current           https://github.com/actions/checkout/releases/tag/v7.0.1
astral-sh/setup-uv@1c37ad0  v10.2.0  v10.3.0  update available  https://github.com/astral-sh/setup-uv/releases/tag/v10.3.0
typescript                  6.0.3    7.0.2    held              https://github.com/microsoft/TypeScript/releases
gitleaks                    -        v8.30.1  reference         https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1
nfpm                        -        v2.48.0  unknown           https://github.com/goreleaser/nfpm/releases/tag/v2.48.0

held: typescript: 7.0.2 failed testing on 2026-10-27 (lint refuses TS 7.0); staying on 6.0.3 until a newer release is published
unknown: nfpm: nfpm is not installed

check-updates: 7 items: 2 current, 2 update available, 1 held, 1 reference, 1 unknown
check-updates: gate failed: 2 update available (tomlkit, astral-sh/setup-uv@1c37ad0); 1 unknown (nfpm)
```

The last line appears with `--gate` only. The summary line names `held` and `reference` only when some row has them.

A version is compared at the precision it is written with. `3.15` in `.python-version` stays current while 3.15.x is the newest series, and a tag pin `@v7` stays current while 7.y.z is. A `go` directive is compared by minor version. A pre-release pin is behind the final release it precedes, and a pin newer than the latest stable release counts as current. Commit-SHA action pins are compared through the version in their trailing comment. A SHA pin without one, or a branch pin such as `@main`, is `unknown`.

## Configuration

Everything project-specific lives in `check-updates.toml`. Paths are relative to `--root`. A section that is absent is not checked, and unknown sections or keys are rejected, so a typo cannot make an item silently disappear.

```toml
assets = [                                  # hand-vendored front-end files; must precede tables
    { name = "htmx", version = "2.0.7", github = "bigskysoftware/htmx" },
]

[settings]
timeout_seconds = 20                        # per request or command
max_workers = 8                             # concurrent checks (capped at 32)

[exceptions]
file = "tools/check-updates/update-exceptions.toml"   # held items; see "Exceptions" below

[python]
version_file = ".python-version"            # compared by minor series
requires_python = "pyproject.toml"          # floor of requires-python, by minor series
uv_interpreter = true                       # `uv python find --show-version`
releases_url = "https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false"

[go]
binary = ["go", "version"]
pins = [{ file = "Dockerfile", regex = 'FROM golang:(\d+\.\d+\.\d+)@' }]   # other Go pins
go_mod = ["**/go.mod"]                      # .git, .venv, vendor, node_modules are skipped
modules = true                              # also report each go.mod's direct requirements

[node]
version_file = "web/.node-version"           # compared as written: `24` is current while 24.x is the newest LTS
engines = "web/package.json"                # floor of engines.node, by major line
binary = ["node", "--version"]
releases_url = "https://nodejs.org/dist/index.json"

[pypi]
pyproject = "pyproject.toml"
lockfile = "uv.lock"
direct_dependencies = true
extra = ["certifi"]                         # more locked names to report

[lockfile]
lockfile = "uv.lock"
# A far-future --exclude-newer overrides any uv.toml or UV_EXCLUDE_NEWER cutoff.
command = ["uv", "lock", "--upgrade", "--dry-run", "--exclude-newer", "2999-12-31T00:00:00Z"]

[npm]
package_json = "web/package.json"
lockfile = "web/package-lock.json"         # lockfileVersion 1 to 3
direct_dependencies = true                  # every dependencies and devDependencies entry
extra = ["zrender"]                         # more locked top-level names to report
lines = { "@types/node" = "web/.node-version" }   # compared within that file's major line

[actions]
workflows = [".github/workflows/*.yml", ".github/workflows/*.yaml"]

[[tools]]
name = "nfpm"
command = ["nfpm", "--version"]             # current: the installed binary
regex = 'GitVersion:\s*v?(\S+)'             # optional; default is the first version-like token
github = "goreleaser/nfpm"                  # latest: exactly one of pypi, github, npm
per_page = 10                               # GitHub releases scanned (lower for asset-heavy repos)

[[tools]]
name = "pip-audit (make audit)"
file = "Makefile"                           # current: first regex group in this file
regex = 'pip-audit==(\S+)'
pypi = "pip-audit"
```

A `[[tools]]` entry takes its current version from `command`, from `file` with `regex`, or from `version`. With none of them, the row shows the latest release for reference and its status is `reference`.

A package in `[npm] lines` is compared with the newest stable release whose major version is the major of the version in that file, instead of the `latest` dist-tag. Type definitions that must describe the runtime the build uses, such as `@types/node` against `.node-version`, need this. The lookup downloads the registry's abbreviated document for the package, about 2 MB for `@types/node`.

## Exceptions

The only reason to stay behind the latest release is that it failed the project's tests. Such a hold is recorded in the file `[exceptions] file` names:

```toml
[[exception]]
item = "typescript"         # the ITEM column, exactly as printed
version = "7.0.2"           # the newest release that failed
reason = "typescript-eslint 8.72.0 refuses TS 7.0 (lint); 6.0.3 passes"
recorded = 2026-10-27       # TOML date of the failed test
```

| Latest release of the item | Row | `--gate` |
|---|---|---|
| equals `version` | `held`, with the reason; CURRENT is the last-known-good version | passes |
| newer than `version` | `update available`: test the new release, then update or move the exception to it | fails |
| older than `version` | unchanged, plus a problem: the exception names a release newer than the latest | fails |
| no update pending (CURRENT is the latest) | unchanged, plus a problem: remove the exception | fails |
| not checked (`unknown`) | unchanged; the exception is not judged | fails (unknown) |

An exception whose item is not in the report is a problem too. Each item may have one exception. Unknown keys, a missing reason, an unparseable version, or a `recorded` value that is not a date make the file invalid, which exits 2.

The exception only records the hold. Keeping the lockfile on the held version is the package tool's job: for uv, add `NAME<VERSION` to `[tool.uv] constraint-dependencies`; npm pins exact versions in `package.json` already.

## Safety

- **Read-only.** The lockfile check uses `uv lock --dry-run`. If a configured command changes `uv.lock` anyway, the original bytes are put back. `go list` runs with Go's default `-mod=readonly`, so `go.mod` and `go.sum` are not rewritten. It may fill the module cache outside the repository.
- **HTTPS only**, verified against the system trust store. Standard `HTTPS_PROXY` and `NO_PROXY` are honoured. Each request has a timeout and a response-size cap.
- **Commands** come only from the config file and run as argument lists, never through a shell. Fetched data is parsed as JSON and never executed.

## Using it in another project

Copy the directory and edit `check-updates.toml`:

```sh
cp -r path/to/this/repo/tools/check-updates your-project/tools/
$EDITOR your-project/tools/check-updates/check-updates.toml
uv run your-project/tools/check-updates/check_updates.py --root your-project
```

The directory holds `check_updates.py` (entry point and command line), its `updates_*.py` sibling modules (configuration, checks, upstream parsers, network access, versions, report), the tests and their recorded fixtures under `testdata/`, and this README. Files outside the tool directory, such as an audit ignore list, belong to the project.

**Portability rule:** check-updates must never import anything from the project that hosts it, and must never hard-code anything about that project. Keep every project-specific value in the config file.

## Development

The tests are offline: every request and command is answered from `testdata/` or canned results.

```sh
uv run --with pytest==9.1.1 pytest tools/check-updates
uvx ruff@0.17.1 check tools/check-updates && uvx ruff@0.17.1 format --check tools/check-updates
```

## Known limitations

- A commit-SHA pin's comment is trusted. The tool does not check that the SHA really is the commented tag.
- Workflow `uses:` lines are found with a line-oriented pattern, not a YAML parser. A `uses:` value split across lines is missed.
- GitHub lookups read the newest `per_page` releases (default 10) and pick the highest stable tag among them.
- PyPI rows show the newest release on PyPI, even when the project's version constraints forbid it. The lockfile row shows what the resolver would actually choose.
- Go module rows cover direct requirements only, and their status is Go's own: a requirement is behind when `go list -m -u` reports an `Update`.
- npm rows show the registry's `latest` release, even when a peer dependency of another package forbids it (TypeScript 7 while the Svelte tools accept 6). There is no whole-lockfile npm row; transitive packages follow from the direct ones, or are named in `extra`.
- A held row compares only with the latest release. A newer release on the held line (6.0.4 while 7.0.2 is held) is not reported; look for one by hand when recording or revisiting an exception.
- Node rows are judged against LTS releases only. A new major line counts once nodejs.org promotes it to LTS, usually in October of its release year. `.node-version` aliases such as `lts/*` cannot be compared and show as `unknown`.
- `[go]`, `[node]`, `[npm]`, and `[exceptions]` reject unknown keys; the other sections check only their section names.

## License

Apache License 2.0.
