# check-updates

A small, portable report of everything a project pins (toolchain, locked dependencies, CI actions, vendored assets) next to the latest stable upstream release, with a link to the release notes.

check-updates answers one question before a release: *what is out of date, and where do I read about the new version?* It compares:

| What | Current version from | Latest stable from |
|---|---|---|
| Python | `.python-version` (minor series), `requires-python` floor, the interpreter `uv python find` selects | python.org release API |
| Go | `go version`, the `go` directive of every `go.mod` (minor series) | `go.dev/dl/?mode=json` |
| Go modules | the direct requirements of every `go.mod` | `go list -m -u -json all` (the Go module proxy) |
| PyPI packages | `uv.lock`, for every direct dependency, optional extra, and dependency-group member in `pyproject.toml` | PyPI JSON API |
| The whole lockfile | `uv lock --upgrade --dry-run`, which also covers transitive packages | the resolver |
| GitHub Actions | `uses:` lines in workflows: tag pins (`@v7`) and commit pins with a `# vX.Y.Z` comment | GitHub releases |
| Tools and other pins | a command's output (`uv --version`), a regex over a file (a Makefile pin, PEP 723 inline metadata), or a literal | PyPI, GitHub releases, or npm |
| Vendored front-end assets | a literal version in the config | npm or GitHub releases |

Pre-releases (`a`, `b`, `rc`, `dev`, and SemVer suffixes such as `-beta.1`), drafts, yanked files, and non-CPython entries on python.org are never treated as the latest release.

## Requirements

[uv](https://docs.astral.sh/uv/) and network access. The entry script declares its Python version (3.15 or newer) and its dependencies (none) as [PEP 723](https://peps.python.org/pep-0723/) inline metadata, so `uv run` needs to install nothing. The GitHub CLI (`gh`) is optional. When it is installed and logged in, GitHub lookups go through `gh api`, which raises the rate limit from 60 to 5,000 requests an hour. check-updates never reads the token itself.

## Usage

```sh
# Readable table (always exits 0)
uv run tools/check-updates/check_updates.py

# Machine-readable report
uv run tools/check-updates/check_updates.py --json

# Gate: exit 1 when any update is available
uv run tools/check-updates/check_updates.py --fail-on-outdated

# Another repository, another config
uv run tools/check-updates/check_updates.py --root ../other --config ../other/check-updates.toml
```

| Option | Meaning |
|---|---|
| `--config PATH` | Configuration file. Default: `check-updates.toml` next to the script. |
| `--root DIR` | Repository root that config paths are relative to. Default: the current directory. |
| `--json` | Print `{"summary": {...}, "items": [...]}` instead of the table. |
| `--fail-on-outdated` | Exit 1 if any item has an update available. |
| `--timeout SECONDS` | Limit for each request or command. Overrides `settings.timeout_seconds`. |

Exit status: `0` for a completed report, `1` with `--fail-on-outdated` when an update is available, and `2` for a configuration error. An item that cannot be checked, for example because an API is unreachable or a tool is not installed, is reported as `unknown` with the reason. It never stops the run and never fails the gate.

## Output

```text
ITEM                        CURRENT  LATEST   STATUS            RELEASE NOTES
Python (.python-version)    3.15     3.15.0   current           https://docs.python.org/release/3.15.0/whatsnew/changelog.html
tomlkit                     0.13.0   0.15.2   update available  https://github.com/python-poetry/tomlkit/blob/master/CHANGELOG.md
actions/checkout@v7         v7       v7.0.1   current           https://github.com/actions/checkout/releases/tag/v7.0.1
astral-sh/setup-uv@1c37ad0  v10.2.0  v10.3.0  update available  https://github.com/astral-sh/setup-uv/releases/tag/v10.3.0
nfpm                        -        v2.48.0  unknown           https://github.com/goreleaser/nfpm/releases/tag/v2.48.0

unknown: nfpm: nfpm is not installed

check-updates: 5 items: 2 current, 2 update available, 1 unknown
```

A version is compared at the precision it is written with. `3.15` in `.python-version` stays current while 3.15.x is the newest series, and a tag pin `@v7` stays current while 7.y.z is. A `go` directive is compared by minor version. A pre-release pin is behind the final release it precedes, and a pin newer than the latest stable release counts as current. Commit-SHA action pins are compared through the version in their trailing comment. A SHA pin without one, or a branch pin such as `@main`, is `unknown`.

## Configuration

Everything project-specific lives in `check-updates.toml`. Paths are relative to `--root`. A section that is absent is not checked, and unknown sections or keys are rejected, so a typo cannot make an item silently disappear.

```toml
assets = [                                  # vendored front-end files; must precede tables
    { name = "echarts", version = "6.0.0", npm = "echarts" },
]

[settings]
timeout_seconds = 20                        # per request or command
max_workers = 8                             # concurrent checks (capped at 32)

[python]
version_file = ".python-version"            # compared by minor series
requires_python = "pyproject.toml"          # floor of requires-python, by minor series
uv_interpreter = true                       # `uv python find --show-version`
releases_url = "https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false"

[go]
binary = ["go", "version"]
go_mod = ["**/go.mod"]                      # .git, .venv, vendor, node_modules are skipped
modules = true                              # also report each go.mod's direct requirements

[pypi]
pyproject = "pyproject.toml"
lockfile = "uv.lock"
direct_dependencies = true
extra = ["certifi"]                         # more locked names to report

[lockfile]
lockfile = "uv.lock"
command = ["uv", "lock", "--upgrade", "--dry-run"]

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

A `[[tools]]` entry takes its current version from `command`, from `file` with `regex`, or from `version`. With none of them, the row shows the latest release for reference and its status is `unknown`.

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

## License

Apache License 2.0.
