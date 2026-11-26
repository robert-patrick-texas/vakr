# Releasing vakr

A release is a GitHub release holding the `.deb`, the field kit `vakr-fieldkit.pyz` (once it is built), `SHA256SUMS`, and `SHA256SUMS.sig`. Production hosts install it only if the signature verifies against the release signers shipped in the package they already run (UPD-001, ADR-026). Releases are cut only with `tools/release` on the development host. CI builds and tests packages on every push but cannot sign, because the signing key never leaves the development host. This page covers the one-time key setup, each release, the dry run, and building packages locally.

## Contents

- [One-time setup (project owner)](#one-time-setup-project-owner)
- [Cutting a release](#cutting-a-release)
- [What tools/release does](#what-toolsrelease-does)
- [Release notes](#release-notes)
- [Dry run](#dry-run)
- [Versions](#versions)
- [Building and testing packages locally](#building-and-testing-packages-locally)
- [Rotating the release key](#rotating-the-release-key)

## One-time setup (project owner)

1. **Create the release signing key** on the development host, with a passphrase. It must never be copied to GitHub, CI secrets, or a production host.

   ```sh
   install -d -m 0700 ~/.config/vakr-release
   ssh-keygen -t ed25519 -C "vakr release" -f ~/.config/vakr-release/release_ed25519
   ```

   Keep an offline backup of the private key. Losing it means shipping a new signer through a release that production can still verify, and without the old key that is impossible.

2. **Trust it in the package.** Add the public key to `packaging/release-signers`. The package installs that file as `/opt/vakr/share/release-signers`, and self-update verifies every later release against it. Both the principal and the namespace are `vakr-release`:

   ```sh
   printf 'vakr-release namespaces="vakr-release" %s\n' \
       "$(cat ~/.config/vakr-release/release_ed25519.pub)" >> packaging/release-signers
   ssh-keygen -lf ~/.config/vakr-release/release_ed25519.pub
   ```

   Commit the change. Publish the fingerprint printed by the last command somewhere operators can compare it before their first install, for example the repository README. The [installation guide](../operations/install.md) tells them to check it.

3. **Authenticate the GitHub CLI** with `gh auth login`. The release tool uses `gh` to read CI status and create releases.

4. **Install Node.js and npm** (the exact release `web/.node-version` names, 26.13.1 today) on the development host. The web UI is built and checked with them (ADR-035): `make check` runs its lint, type, and unit checks, and the release preflight stops if `node` or `npm` is missing or `node` is on another line. The package itself is built in a container that brings its own Node, and production hosts never need it.
5. **Install cloc** (`sudo apt install cloc`). The release counts its lines of code with it for the release notes (VER-006), and the preflight stops if `cloc --version` fails.

Until step 2 is done, the shipped signers file is a placeholder, every signature check fails closed, and `tools/release` refuses to publish.

## Cutting a release

1. **Check for the latest version of every component and dependency, and update to it.** Run `make updates` and apply every update, however recently it was published: Python, Go, Node.js, uv, the builder images, every locked Python and npm package, and the GitHub Actions (TOOLS-002, ADR-036). Hold one back on its last-known-good version only when the new release fails vakr's tests and there is no simple fix. Record the hold as an exception (TOOLS-004), name the obstacle under **Additional Notes** in the release notes, and test again when that component publishes its next release. Nothing else, such as a release's age, is a reason to stay behind. [Keeping tools and dependencies current](updates.md#release-time-policy-tools-002) has the procedure. Step 5 checks again.
2. Bump `VERSION` (`MAJOR.MINOR.PATCH`, or `MAJOR.MINOR.PATCH-rc.N` for a candidate). Add a `## <version> - <date>` section to `CHANGELOG.md` in the [release-notes layout](#release-notes): a summary of one to three sentences, then the topics New, Changed, Fixed, and Additional Notes, listing breaking changes, migrations, and new options (VER-004, VER-005). That section becomes the release notes. Do not write the lines-of-code or versions tables; the release tool generates both.
3. Commit, push to `main`, and wait until every CI check for that commit is green, including the *Package* workflow.
4. Run `make release-dry-run` and read its report (see [Dry run](#dry-run)).
5. Run the release. Its preflight runs `check-updates --gate` again (TOOLS-005), so a dependency published since step 1, even the same day, stops it: apply it (or hold it after a failed test), commit, and wait for CI again. `ssh-keygen` asks for the key's passphrase:

   ```sh
   uv run tools/release/release.py
   ```

6. Verify the update path against the published release (UPD-T01):

   ```sh
   make github-update-test        # previous published release -> VERSION
   make github-update-test FROM=1.3.0 TO=1.4.0
   ```

   In a systemd-booted `ubuntu:26.04` container it installs the previous release from GitHub, verifying the key fingerprint against this checkout's `packaging/release-signers`, the signature, and the checksum, exactly as the install guide does. It then runs `vakr self-update --yes`, `--rollback`, and the timer's `vakr-update.service` twice. For a release marked `manual-update-required`, it checks instead that the rolled-back release refuses the new telemetry schema and that the timer skips the release, and then updates as an operator would. It checks the version, the update record, the databases (current, vakr-owned, and accepted by the older release after the rollback), health, the pre-update backup, that the second timer run changes nothing, and a collector start and stop. It needs Docker, privileged containers, and outbound HTTPS.
7. Documentation publishes to GitHub Pages automatically.

## What tools/release does

The tool reads all project-specific values from `tools/release/release.toml` (see [its README](../../tools/release/README.md)). In order, it:

1. Checks that `VERSION_OVERRIDE` is unset, the tree is clean, `HEAD` is on `main`, the tag `v<version>` exists neither locally nor on `origin`, every GitHub check run for `HEAD` passed (except Dependabot's update jobs, `ci_ignore_checks`), `CHANGELOG.md` has a section for the version, `packaging/release-signers` lists a key, `node` and `npm` answer `--version`, `node` is the release `web/.node-version` pins, `cloc` answers `--version`, the changelog section follows the release-notes layout, and `check-updates --gate` passes (`update_check`): every dependency is on its newest release or held by a recorded exception, and every item could be checked. A real release stops at the first failed check, before building anything.
2. Builds with `make deb`, in a clean `ubuntu:26.04` container.
3. Copies the artifacts into `dist/release/v<version>/` and writes `SHA256SUMS` there in `sha256sum` format, with one `<sha256>  <file name>` line per artifact.
4. Signs it: `ssh-keygen -Y sign -f ~/.config/vakr-release/release_ed25519 -n vakr-release SHA256SUMS`, producing `SHA256SUMS.sig`.
5. Verifies that signature against `packaging/release-signers` with `ssh-keygen -Y verify`, exactly as production self-update will, and stops if it fails.
6. Writes the release notes to `release-notes.txt` in the output directory: the changelog section, then the lines-of-code table and the versions table (see [Release notes](#release-notes)).
7. Creates the annotated tag `v<version>`, pushes it, and runs `gh release create` with those notes. No release is marked `--prerelease`: `prerelease = "never"` in `tools/release/release.toml`, because every packaged version must download as a normal release (owner, 2026-10-28). Mark one only when the owner asks, by setting `prerelease` for that release or with `gh release edit v<version> --prerelease` afterwards.

## Release notes

Every release's notes on GitHub have the same layout (owner, 2026-11-14), in this order:

1. **Summary.** One to three sentences that say what the release is.
2. **Topics,** as `###` headings in this order: **New**, **Changed**, **Fixed**, **Additional Notes**. Leave out any topic with nothing in it, so a release with a single bug fix shows only Fixed. Additional Notes holds what operators and developers should know that is not a change itself: notes for operators updating, and every dependency held back on a known-good version (TOOLS-004), with what failed and that it is tested again when the dependency publishes its next release.
3. **Lines of code** (VER-006), generated. cloc counts the files git tracks at the release commit, by language. Only first-party content counts: vakr vendors nothing, so no dependency or external component is in the repository, and generated files are left out (`loc_exclude` in `tools/release/release.toml`): the lockfiles and hash pins that record third-party packages (`uv.lock`, `web/package-lock.json`, `packaging/build-constraints.txt`), the OpenAPI document (`docs/api/openapi.json`), and the ADR-021 benchmark digests (`docs/performance/*.json`). A statement under the table says so and names the cloc version.
4. **Versions** (VER-007), generated. Python and the prominent components and dependencies, each with its version in this release. The interpreter version is read from the built package, locked packages from `uv.lock` and `web/package-lock.json`, and toolchain pins from the files that pin them. The rows are the `[[notes.versions]]` tables in `release.toml`; add one there when a component becomes prominent, and never rename one, because the name matches it to the previous release's row. From the second release with a table on, it has two columns, old (the previous release's published table) and new, and every version that changed is in bold, as are rows added or removed.

You write items 1 and 2 in `CHANGELOG.md`; the release tool appends 3 and 4. The preflight stops a release whose section breaks the layout: no summary or one longer than three sentences, a topic that is unknown, out of order, repeated, or empty, or a held dependency in `tools/check-updates/update-exceptions.toml` that Additional Notes does not name (VER-005). The latest release when this rule was made (2026-11-14) was the first to get the tables: its notes were regenerated from its tag and package and edited on GitHub. Earlier releases have no tables.

To see the notes a release would publish, after `make deb` has built the package for `VERSION`:

```sh
uv run tools/release/release.py --print-notes
```

## Dry run

`make release-dry-run` (that is, `uv run tools/release/release.py --dry-run`) runs every check and step except tagging, pushing, and creating the release. It prints the commands it would have run.

- It continues past failed checks so you see them all, and exits 1 if a real release would have stopped.
- If the release key is absent, it signs with a throwaway key and verifies against a matching temporary signers file. That exercises the signing path but not your trust setup. With the real key present, it signs with it (asking for the passphrase) and verifies against `packaging/release-signers`, which proves the key is trusted.
- The files land in `dist/release/v<version>/`, so you can inspect `SHA256SUMS` and the notes.

## Versions

`VERSION` is the only place the version is written (VER-001). Every other form is derived from it by `tools/release/release.py --print-version FORM` (VER-002):

| `VERSION` | PEP 440 (`vakr --version`) | Debian/RPM package | Git tag |
|---|---|---|---|
| `1.4.0-rc.1` | `1.4.0rc1` | `1.4.0~rc.1` | `v1.4.0-rc.1` |
| `1.4.0` | `1.4.0` | `1.4.0` | `v1.4.0` |

The `~` makes dpkg and RPM sort a release candidate before the final release, so upgrading from `1.4.0~rc.1` to `1.4.0` works.

## Building and testing packages locally

Both targets need Docker, git, and uv.

| Command | Result |
|---|---|
| `make deb` | `dist/vakr_<debver>_amd64.deb`, plus `dist/vakr-fieldkit.pyz` once the field kit exists |
| `make package-test` | Static packaging checks, then two builds (N from `VERSION`, and N+1 = the next patch version) and container tests: install, remove, reinstall, upgrade N to N+1 keeping the operator's config, and purge. Then a systemd-booted container checks the timer and the restart-only-if-running upgrade rule. Set `PACKAGE_TEST_SYSTEMD=0` where privileged containers are not allowed, and `PACKAGE_TEST_REPRO=1` (as CI does) to rebuild N and require a byte-identical package. |

How the build works (`packaging/build-deb.sh`, which runs `packaging/container/build.sh` in the builder image from `packaging/Dockerfile`):

- **Pinned inputs.** uv 0.13.10 and Go 1.27.4 images are pinned by digest, and nfpm v2.49.0 is verified by the Go checksum database. `uv python install 3.15` fetches a checksum-verified python-build-standalone interpreter into `/opt/vakr/python`. Runtime wheels come from `uv export --frozen` with `--require-hashes --only-binary :all:`, and the hatchling build backend is pinned with hashes in `packaging/build-constraints.txt`.
- **Reproducible.** `SOURCE_DATE_EPOCH` is the commit time. Every packaged file gets that mtime, bytecode is compiled with `PYTHONHASHSEED=0` and hash-based invalidation, and nfpm stamps the same time on the archive, so two builds of one commit are byte-identical (PKG-004).
- **Checked.** Before packaging, the build imports every extension module, checks that no binary needs a newer glibc than the declared `libc6` dependency, and runs `systemd-analyze verify` on the units. After packaging, it runs `lintian --fail-on error`. The only accepted lintian tags are listed, each with its justification, in `packaging/lintian/vakr`: `dir-or-file-in-opt` (the owner's `/opt/vakr` decision) and tags caused by the bundled interpreter.
- **Test builds.** `VERSION_OVERRIDE=X.Y.Z make deb` builds a package that reports version `X.Y.Z`. It exists only for upgrade tests; `tools/release` refuses to run while it is set, and removes it from the build environment.

`packaging/nfpm.yaml` keeps all Debian-specific settings under `overrides.deb`, so the RPM build after MVP (PKG-006) adds an `overrides.rpm` section beside it.

## Rotating the release key

1. Generate the new key as in [One-time setup](#one-time-setup-project-owner) and add its line to `packaging/release-signers`, keeping the old line.
2. Release with the **old** key, which still sits at the configured `signing_key` path. Production verifies that release with the signers it already has, and installs the new signers file.
3. Move the new key to the `signing_key` path, remove the old line from `packaging/release-signers`, and release again.
