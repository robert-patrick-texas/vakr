# release

Builds, checksums, SSH-signs, verifies, tags, and publishes a GitHub release. It is a portable scaffolding tool (blueprint section 29): everything project-specific lives in `release.toml`, and the code imports nothing from the project it releases. vakr's release procedure is in [Releasing vakr](../../docs/development/release.md).

## Usage

```sh
uv run tools/release/release.py --dry-run         # everything except tag, push, release
uv run tools/release/release.py                   # a real release
uv run tools/release/release.py --print-version deb             # from VERSION
uv run tools/release/release.py --print-version deb --version 1.4.0-rc.1
uv run tools/release/release.py --print-notes                   # notes from built artifacts
```

| Option | Meaning |
|---|---|
| `--config FILE` | Configuration file; default `release.toml` beside the script |
| `--root DIR` | Repository root; default the current directory |
| `--dry-run` | Run every check and step except tag, push, and release. Sign with a throwaway key if the real key is absent. |
| `--print-notes` | Print the release notes for `VERSION` (the changelog section plus the `[notes]` tables) from artifacts already built, then exit. Reads the previous release from GitHub; builds, tags, and publishes nothing. |
| `--print-version FORM` | Print `VERSION` (or `--version`) as `semver`, `pep440`, `deb`, `tag`, or `next-patch`, then exit. Build scripts use this so the mapping exists once. |

Exit status: 0 on success (or a dry run that a real release would pass), 1 when a check or step fails (including a table `--print-notes` cannot generate), and 2 for a usage, configuration, or version error.

## What a release does

1. **Preflight.** `VERSION_OVERRIDE` must be unset. The working tree must be clean, `HEAD` must be on `branch`, and the tag must exist neither locally nor on `remote`. Every GitHub check run for `HEAD` must have passed (`gh api repos/<repo>/commits/<sha>/check-runs`), except those named in `ci_ignore_checks`. The changelog needs a `## <version>` section, and `allowed_signers` must list at least one key. Each program in `build_tools` must answer `--version`, and when `node_version_file` is set, `node --version` must match it at the precision it is written with (`24` admits any 24.x). With a `[notes]` table, the changelog section must follow its layout (see [Release notes](#release-notes)). Last, `update_check` runs and must exit 0, so dependency currency is judged when the release starts, not when it was prepared; a dependency published an hour ago counts.
2. **Build** with `build_command`, with `VERSION_OVERRIDE` removed from its environment.
3. **Stage** the files matching `artifacts` (required) and `optional_artifacts` into `output_dir`, and write `SHA256SUMS` in `sha256sum` format.
4. **Sign** with `ssh-keygen -Y sign -f <signing_key> -n <namespace> SHA256SUMS`.
5. **Verify** with `ssh-keygen -Y verify -f <allowed_signers> -I <identity> -n <namespace>`, and stop if it fails.
6. **Notes:** write `release-notes.txt` into `output_dir`: the changelog section, and with a `[notes]` table, the lines-of-code and versions tables after it.
7. **Publish:** `git tag --annotate`, `git push <remote> refs/tags/<tag>`, then `gh release create <tag> --verify-tag` with those notes and `--prerelease` according to the `prerelease` rule.

A real release stops at the first failed preflight check, before building. A dry run reports every failure and carries on, so one run shows everything to fix.

## Configuration

`release.toml` keys, all required except `build_tools`, `node_version_file`, `ci_ignore_checks`, `update_check`, and the `[notes]` table. Relative paths are relative to the repository root; `~` expands to the home directory of the user running the tool.

| Key | Example | Meaning |
|---|---|---|
| `repo` | `owner/name` | GitHub repository for status checks and the release |
| `package` | `vakr` | `{package}` in patterns |
| `version_file` | `VERSION` | The single version source |
| `changelog` | `CHANGELOG.md` | Source of release notes |
| `branch`, `remote` | `main`, `origin` | Where releases are cut from and tags go |
| `signing_key` | `~/.config/vakr-release/release_ed25519` | Private SSH key, kept off GitHub |
| `namespace`, `identity` | `vakr-release` | Signature namespace and `allowed_signers` principal |
| `allowed_signers` | `packaging/release-signers` | The trusted signers file the product ships |
| `build_command` | `["make", "deb"]` | Argument list; never run through a shell |
| `artifacts`, `optional_artifacts` | `["dist/{package}_{deb_version}_amd64.deb"]` | Glob patterns relative to the root, with placeholders `{package}`, `{version}`, `{pep440}`, `{deb_version}`, `{tag}` |
| `output_dir` | `dist/release/{tag}` | Where `SHA256SUMS`, the signature, notes, and artifact copies go |
| `prerelease` | `never` | `rc-or-major-zero`, `rc`, `always`, or `never` |
| `build_tools` (optional) | `["node", "npm"]` | Programs the release host must have; each is run with `--version` in preflight. Absent or empty: no check |
| `node_version_file` (optional) | `web/.node-version` | The installed `node` must be on the version this file names. Absent or empty: no check |
| `ci_ignore_checks` (optional) | `["Dependabot"]` | Check-run names the CI check disregards, such as Dependabot's update jobs on a commit that changes `.github/dependabot.yml`. Absent or empty: every check run counts |
| `update_check` (optional) | `["uv", "run", "tools/check-updates/check_updates.py", "--gate"]` | Argument list run in preflight; the release stops unless it exits 0. The preflight line shows the command's last output line. Absent or empty: no check |

Unknown or mistyped keys are errors.

## Release notes

An optional `[notes]` table sets the layout the changelog section must follow and the two tables the tool appends to it (VER-005 to VER-007). Without it, the section is published unchecked and unchanged.

| Key | Example | Meaning |
|---|---|---|
| `sections` | `["New", "Changed", "Fixed", "Additional Notes"]` | The only `###` topics allowed, in this order, each at most once and never empty |
| `summary_sentences` (optional) | `3` | The section must open with a summary paragraph of 1 to this many sentences. Default 3 |
| `held_file`, `held_section` (optional) | `"tools/check-updates/update-exceptions.toml"`, `"Additional Notes"` | Every `[[exception]] item` in the file must be named in that topic |
| `loc_command` (optional) | `["cloc"]` | cloc, run with `--version` and with `--list-file=<list> --json --quiet`. Default `["cloc"]` |
| `loc_exclude` (optional) | `["uv.lock"]` | `fnmatch` patterns of tracked files that are not first-party content |
| `[[notes.versions]]` | see below | Rows of the versions table, in order |

**Lines of code.** cloc is given exactly the files `git ls-files` lists, minus `loc_exclude`, so untracked files (virtual environments, `node_modules`, build output) never reach it. The table has one row per language and a bold total. The statement under it names the cloc version and the excluded patterns, and says that no dependencies or external components are counted.

**Versions.** Each row has a `name` and exactly one source:

| Source | Keys | Reads |
|---|---|---|
| Repository file | `file`, `regex` | The first group of `regex` (multi-line mode) in the file, such as a pin in a Dockerfile |
| Lockfile | `lock`, `package` | The package's locked version: `uv.lock`, or npm's `package-lock.json` for a `.json` lockfile |
| Built package | `deb`, `member`, `regex` | A member of the built `.deb` (`deb` takes the artifact placeholders; `member` is a glob such as `./opt/x/python/lib/python3.*/_sysconfig_vars_*.json`), matched with `regex` |

A source that holds no version stops the release: a missing cell is never published. The table compares with the newest earlier release on GitHub (`gh release list`, then `gh release view`), parsing the `### Versions` table the tool published there. When no earlier release has one, the table has a single Version column. Otherwise it has Old and New columns, and a changed, added (old shows `—`), or removed row is in bold. The row name is the key, so renaming a row reads as one removal and one addition.

## Requirements

Python 3.15 (standard library only), `git`, `ssh-keygen` (OpenSSH 8.1 or newer), an authenticated `gh` for the CI check and the release itself, cloc when `[notes]` is configured, and whatever `build_tools` names (for vakr, Node.js, npm, and cloc).

## Files and tests

| File | Purpose |
|---|---|
| `release.py` | Entry point and orchestration |
| `release_version.py` | `VERSION` to PEP 440, Debian/RPM, and tag forms; prerelease rule |
| `release_config.py` | Loads and validates `release.toml` |
| `release_checks.py` | Preflight checks, including the GitHub check-run evaluation |
| `release_artifacts.py` | Collection, `SHA256SUMS`, signing, verification, throwaway keys |
| `release_publish.py` | Changelog notes and the tag, push, and release commands |
| `release_notes_config.py` | Validates the `[notes]` table |
| `release_notes.py` | Release-note layout check, held-dependency check, previous release lookup, and the composed notes |
| `release_loc.py` | First-party file list, cloc, and the lines-of-code table |
| `release_versions.py` | Version sources (file, lockfile, `.deb` member) and the versions table |
| `release_proc.py` | Runs external commands with argument lists |

`uv run pytest tools/release` runs the tests. They use real `git` repositories with a local bare remote and real `ssh-keygen` keys, all under a temporary directory. Only `gh`, `git push`, the `build_tools` versions, and cloc (except in one test that is skipped without it) are replaced by a fake runner, so no test touches GitHub or needs Node. The `.deb` tests write a small package with the standard library.

To reuse the tool in another project, copy this directory and edit `release.toml`.
