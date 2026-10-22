# Releasing vakr

A release is a GitHub release holding the `.deb`, the field kit `vakr-fieldkit.pyz` (once it is built), `SHA256SUMS`, and `SHA256SUMS.sig`. Production hosts install it only if the signature verifies against the release signers shipped in the package they already run (UPD-001, ADR-026). Releases are cut only with `tools/release` on the development host. CI builds and tests packages on every push but cannot sign, because the signing key never leaves the development host. This page covers the one-time key setup, each release, the dry run, and building packages locally.

## Contents

- [One-time setup (project owner)](#one-time-setup-project-owner)
- [Cutting a release](#cutting-a-release)
- [What tools/release does](#what-toolsrelease-does)
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

4. **Install Node.js and npm** (the exact release `web/.node-version` names, 24.22.1 today) on the development host. The web UI is built and checked with them (ADR-035): `make check` runs its lint, type, and unit checks, and the release preflight stops if `node` or `npm` is missing or `node` is on another line. The package itself is built in a container that brings its own Node, and production hosts never need it.

Until step 2 is done, the shipped signers file is a placeholder, every signature check fails closed, and `tools/release` refuses to publish.

## Cutting a release

1. Run `tools/check-updates` (TOOLS-001). Apply each update, or record why it is deferred in `CHANGELOG.md`.
2. Bump `VERSION` (`MAJOR.MINOR.PATCH`, or `MAJOR.MINOR.PATCH-rc.N` for a candidate). Add a `## <version> - <date>` section to `CHANGELOG.md` listing breaking changes, migrations, and new options (VER-004). That section becomes the release notes.
3. Commit, push to `main`, and wait until every CI check for that commit is green, including the *Package* workflow.
4. Run `make release-dry-run` and read its report (see [Dry run](#dry-run)).
5. Run the release. `ssh-keygen` asks for the key's passphrase:

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

1. Checks that `VERSION_OVERRIDE` is unset, the tree is clean, `HEAD` is on `main`, the tag `v<version>` exists neither locally nor on `origin`, every GitHub check run for `HEAD` passed (except Dependabot's update jobs, `ci_ignore_checks`), `CHANGELOG.md` has a section for the version, `packaging/release-signers` lists a key, `node` and `npm` answer `--version`, and `node` is the release `web/.node-version` pins. A real release stops at the first failed check, before building anything.
2. Builds with `make deb`, in a clean `ubuntu:26.04` container.
3. Copies the artifacts into `dist/release/v<version>/` and writes `SHA256SUMS` there in `sha256sum` format, with one `<sha256>  <file name>` line per artifact.
4. Signs it: `ssh-keygen -Y sign -f ~/.config/vakr-release/release_ed25519 -n vakr-release SHA256SUMS`, producing `SHA256SUMS.sig`.
5. Verifies that signature against `packaging/release-signers` with `ssh-keygen -Y verify`, exactly as production self-update will, and stops if it fails.
6. Creates the annotated tag `v<version>`, pushes it, and runs `gh release create` with the changelog section as notes. Under the MVP rule, a release is marked `--prerelease` if `VERSION` has `-rc` or the major version is 0.

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

- **Pinned inputs.** uv 0.13.5 and Go 1.27.3 images are pinned by digest, and nfpm v2.48.0 is verified by the Go checksum database. `uv python install 3.15` fetches a checksum-verified python-build-standalone interpreter into `/opt/vakr/python`. Runtime wheels come from `uv export --frozen` with `--require-hashes --only-binary :all:`, and the hatchling build backend is pinned with hashes in `packaging/build-constraints.txt`.
- **Reproducible.** `SOURCE_DATE_EPOCH` is the commit time. Every packaged file gets that mtime, bytecode is compiled with `PYTHONHASHSEED=0` and hash-based invalidation, and nfpm stamps the same time on the archive, so two builds of one commit are byte-identical (PKG-004).
- **Checked.** Before packaging, the build imports every extension module, checks that no binary needs a newer glibc than the declared `libc6` dependency, and runs `systemd-analyze verify` on the units. After packaging, it runs `lintian --fail-on error`. The only accepted lintian tags are listed, each with its justification, in `packaging/lintian/vakr`: `dir-or-file-in-opt` (the owner's `/opt/vakr` decision) and tags caused by the bundled interpreter.
- **Test builds.** `VERSION_OVERRIDE=X.Y.Z make deb` builds a package that reports version `X.Y.Z`. It exists only for upgrade tests; `tools/release` refuses to run while it is set, and removes it from the build environment.

`packaging/nfpm.yaml` keeps all Debian-specific settings under `overrides.deb`, so the RPM build after MVP (PKG-006) adds an `overrides.rpm` section beside it.

## Rotating the release key

1. Generate the new key as in [One-time setup](#one-time-setup-project-owner) and add its line to `packaging/release-signers`, keeping the old line.
2. Release with the **old** key, which still sits at the configured `signing_key` path. Production verifies that release with the signers it already has, and installs the new signers file.
3. Move the new key to the `signing_key` path, remove the old line from `packaging/release-signers`, and release again.
