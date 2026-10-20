# release

Builds, checksums, SSH-signs, verifies, tags, and publishes a GitHub release. It is a portable scaffolding tool (blueprint section 29): everything project-specific lives in `release.toml`, and the code imports nothing from the project it releases. vakr's release procedure is in [Releasing vakr](../../docs/development/release.md).

## Usage

```sh
uv run tools/release/release.py --dry-run         # everything except tag, push, release
uv run tools/release/release.py                   # a real release
uv run tools/release/release.py --print-version deb             # from VERSION
uv run tools/release/release.py --print-version deb --version 0.2.0-rc.1
```

| Option | Meaning |
|---|---|
| `--config FILE` | Configuration file; default `release.toml` beside the script |
| `--root DIR` | Repository root; default the current directory |
| `--dry-run` | Run every check and step except tag, push, and release. Sign with a throwaway key if the real key is absent. |
| `--print-version FORM` | Print `VERSION` (or `--version`) as `semver`, `pep440`, `deb`, `tag`, or `next-patch`, then exit. Build scripts use this so the mapping exists once. |

Exit status: 0 on success (or a dry run that a real release would pass), 1 when a check or step fails, and 2 for a usage, configuration, or version error.

## What a release does

1. **Preflight.** `VERSION_OVERRIDE` must be unset. The working tree must be clean, `HEAD` must be on `branch`, and the tag must exist neither locally nor on `remote`. Every GitHub check run for `HEAD` must have passed (`gh api repos/<repo>/commits/<sha>/check-runs`). The changelog needs a `## <version>` section, and `allowed_signers` must list at least one key.
2. **Build** with `build_command`, with `VERSION_OVERRIDE` removed from its environment.
3. **Stage** the files matching `artifacts` (required) and `optional_artifacts` into `output_dir`, and write `SHA256SUMS` in `sha256sum` format.
4. **Sign** with `ssh-keygen -Y sign -f <signing_key> -n <namespace> SHA256SUMS`.
5. **Verify** with `ssh-keygen -Y verify -f <allowed_signers> -I <identity> -n <namespace>`, and stop if it fails.
6. **Publish:** `git tag --annotate`, `git push <remote> refs/tags/<tag>`, then `gh release create <tag> --verify-tag` with the changelog section as notes and `--prerelease` according to the `prerelease` rule.

A real release stops at the first failed preflight check, before building. A dry run reports every failure and carries on, so one run shows everything to fix.

## Configuration

`release.toml` keys (all required). Relative paths are relative to the repository root; `~` expands to the home directory of the user running the tool.

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
| `prerelease` | `rc-or-major-zero` | `rc-or-major-zero`, `rc`, `always`, or `never` |

Unknown or mistyped keys are errors.

## Requirements

Python 3.15 (standard library only), `git`, `ssh-keygen` (OpenSSH 8.1 or newer), and an authenticated `gh` for the CI check and the release itself.

## Files and tests

| File | Purpose |
|---|---|
| `release.py` | Entry point and orchestration |
| `release_version.py` | `VERSION` to PEP 440, Debian/RPM, and tag forms; prerelease rule |
| `release_config.py` | Loads and validates `release.toml` |
| `release_checks.py` | Preflight checks, including the GitHub check-run evaluation |
| `release_artifacts.py` | Collection, `SHA256SUMS`, signing, verification, throwaway keys |
| `release_publish.py` | Changelog notes and the tag, push, and release commands |
| `release_proc.py` | Runs external commands with argument lists |

`uv run pytest tools/release` runs the tests. They use real `git` repositories with a local bare remote and real `ssh-keygen` keys, all under a temporary directory. Only `gh` and `git push` are replaced by a fake runner, so no test touches GitHub.

To reuse the tool in another project, copy this directory and edit `release.toml`.
