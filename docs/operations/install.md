# Installing vakr

vakr ships as one `.deb` for Ubuntu 26.04 LTS on amd64 (PKG-001). The package is self-contained: it brings its own Python 3.15 interpreter and a locked virtual environment under `/opt/vakr`, so the host's system Python and its upgrades do not affect it. This page covers a first installation from a GitHub release, what the package puts where, the first commands to run, and turning on automatic updates.

## Contents

- [Before you start](#before-you-start)
- [1. Download a release](#1-download-a-release)
- [2. Verify the download](#2-verify-the-download)
- [3. Install the package](#3-install-the-package)
- [What the package installs](#what-the-package-installs)
- [First run](#first-run)
- [Automatic updates](#automatic-updates)
- [Proxies, TLS inspection, and updates](#proxies-tls-inspection-and-updates)
- [Upgrade, removal, and purge](#upgrade-removal-and-purge)

## Before you start

You need:

- Ubuntu 26.04 LTS on amd64, with `sudo`;
- `curl` and `ssh-keygen` for the download and signature check. Minimal images may lack them: `sudo apt install curl openssh-client`. The package itself depends on `openssh-client`, so later self-updates need nothing extra;
- outbound HTTPS to `github.com` and GitHub's download hosts (`*.githubusercontent.com`), directly or through a proxy. A host without GitHub access can install files you copy to it.

Releases are listed at <https://github.com/robert-patrick-texas/vakr/releases>. Until MVP acceptance every release is marked *pre-release*.

## 1. Download a release

Each release has three files you need: the package, `SHA256SUMS`, and its signature `SHA256SUMS.sig`. These commands find the newest release and download them:

```sh
repo=robert-patrick-texas/vakr
# The newest release, release candidates included: the list self-update reads.
# To install a particular release instead, replace this with version=1.4.0
version=$(curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=1" |
    sed -n 's/^ *"tag_name": *"v\([^"]*\)".*/\1/p')
echo "release: ${version:?not found; check the network or proxy, or set version yourself}"
debver=$(printf '%s\n' "$version" | sed 's/-rc\./~rc./')   # the package's form: 1.4.0~rc.1
base=https://github.com/$repo/releases/download/v$version
curl -fL -O "$base/vakr_${debver}_amd64.deb" -O "$base/SHA256SUMS" -O "$base/SHA256SUMS.sig"
```

Tags are the version as written in `VERSION` (`v1.4.0`, `v1.4.0-rc.1`). The package file uses the Debian form, where `-rc.N` becomes `~rc.N`. With the GitHub CLI, `gh release download v$version --repo $repo` fetches the same files.

**Why not GitHub's "latest" links.** `/releases/latest`, `/releases/latest/download/FILE`, and `gh release download` without a tag all skip pre-releases. Every vakr release is a pre-release until MVP acceptance, so today they answer 404 or "release not found". They would also need file names without a version, and the package's name carries one. The release list above works in both cases.

## 2. Verify the download

Releases are trusted through an SSH signature over `SHA256SUMS` (ADR-026). The trusted key is in the repository's `packaging/release-signers` file, which the package later installs as `/opt/vakr/share/release-signers` for self-update. For this first install, fetch that file for the same tag, and compare its key fingerprint with the one the project owner published separately:

```sh
curl -fLO "https://raw.githubusercontent.com/robert-patrick-texas/vakr/v$version/packaging/release-signers"
# allowed-signers lines are "principal options keytype key"; print each key's fingerprint
grep -v '^#' release-signers | awk 'NF {print $3, $4}' | ssh-keygen -lf -
# compare with the fingerprint published in the README (SHA256:RNCDpt4j...0G5cQ)
ssh-keygen -Y verify -f release-signers -I vakr-release -n vakr-release \
    -s SHA256SUMS.sig < SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
```

Both commands must succeed: `ssh-keygen` prints `Good "vakr-release" signature`, and `sha256sum` prints `OK` for the package. If either fails, do not install.

## 3. Install the package

```sh
sudo apt install ./vakr_${debver}_amd64.deb
```

`apt` installs the dependencies: `ca-certificates`, `openssh-client` (which provides `ssh-keygen` for release verification), `openssl` (field-kit encryption), and `systemd`. It also installs the recommended `snmp` tools and `iputils-ping`, which the [field kit](field-data.md) uses, unless you pass `--no-install-recommends`.

## What the package installs

| Path | Contents | Owned by |
|---|---|---|
| `/opt/vakr/python/` | Bundled CPython 3.15 (python-build-standalone) | package |
| `/opt/vakr/venv/` | Locked virtual environment containing vakr | package |
| `/opt/vakr/share/defaults/` | Documented defaults for every option (CFGM-001) | package |
| `/opt/vakr/share/release-signers` | Trusted release signing keys (UPD-001) | package |
| `/opt/vakr/share/web/` | The web UI: static files that `vakr-api` serves at `/` beside the API (ADR-035) | package |
| `/opt/vakr/share/doc/web-licenses/` | Licence and notice files of the front-end packages bundled into the web UI | package |
| `/opt/vakr/share/systemd/`, `share/doc/`, `share/templates/` | Unit sources, license and changelog, config template | package |
| `/usr/bin/vakr` | Symlink to `/opt/vakr/venv/bin/vakr` | package |
| `/usr/lib/systemd/system/` | `vakr.target`, `vakr-collector.service` ([collector](collector.md)), `vakr-update.service`, `vakr-update.timer` | package |
| `/usr/lib/sysusers.d/vakr.conf`, `/usr/lib/tmpfiles.d/vakr.conf` | Account and directory definitions | package |
| `/etc/vakr/config.toml` | Your settings, overrides only (mode `0640`, `root:vakr`) | you |
| `/etc/vakr/secrets/` | Credentials (mode `0750`, `root:vakr`) | you |
| `/etc/vakr/tls/ca.d/` | Extra trusted CA certificates (EGRESS-002) | you |
| `/var/lib/vakr/` | State and telemetry history (mode `0750`, `vakr:vakr`) | vakr |
| `/var/cache/vakr/` | Downloaded and previous packages for rollback | vakr |

The package also creates the system user and group `vakr` (home `/var/lib/vakr`, no login shell) and the group `vakr-admin`.

`/etc/vakr/config.toml` is created only if it does not exist, and the package never overwrites it. It is deliberately not a dpkg conffile, so upgrades never stop to ask about it (CFGM-005). Defaults live in `/opt/vakr/share/defaults/`, so the file only needs the settings you change.

## First run

Nothing starts on installation (PKG-003). Check the installation first:

```sh
vakr --version
vakr doctor               # one line per self-check
vakr check health         # Nagios/CheckMK plugin output and exit code
```

Create the first admin account for the web UI with `sudo vakr setup`, which asks for the password twice and lists the remaining steps ([users](users.md)).

To give an operator administrative access to the local vakr API and TUI, add them to `vakr-admin`. For read-only access, which is all `vakr status` and `vakr tui` need, use `vakr-view`, which, unlike `vakr`, cannot read the SNMP credentials:

```sh
sudo usermod -aG vakr-admin alice
sudo usermod -aG vakr-view bob
```

To start monitoring:

1. Store the SNMP community in `/etc/vakr/secrets/credentials.toml` ([credentials](credentials.md)).
2. List the devices in `/etc/vakr/inventory.toml`, then run `vakr inventory validate`, `vakr inventory plan`, and `sudo vakr inventory apply` ([inventory](inventory.md)).
3. Enable the collector and `vakr.target`, the unit that starts and stops all vakr services together: `sudo systemctl enable --now vakr-collector.service vakr.target` ([collector](collector.md)).
4. Watch the result with `vakr status` or `vakr tui` ([status and TUI](status-and-tui.md)).
5. Start the API for the web UI, integrations, and the local socket: `sudo systemctl enable --now vakr-api.service`, then put it behind TLS with a reverse proxy ([API](api.md), [web access](web-access.md)). The same service serves the web UI at the proxy's address; sign in with the account `vakr setup` created ([web UI](web-ui.md)).

## Automatic updates

`vakr-update.timer` checks for a newer signed release once a day, at a random time spread over four hours, and catches up after downtime (UPD-003). It ships disabled. To turn it on:

```sh
sudo systemctl enable --now vakr-update.timer
systemctl list-timers vakr-update.timer      # shows the next run
journalctl -u vakr-update.service            # what each run did
```

`sudo systemctl disable --now vakr-update.timer` turns it off again. To keep a host on one version while the timer runs, set `[update] hold_version` in `/etc/vakr/config.toml` (UPD-008).

## Proxies, TLS inspection, and updates

Self-update, manual updates with `vakr self-update`, offline installs, and rollback are described in the [updates guide](updates.md). In short:

- **Proxy:** set `[network] https_proxy` (and `no_proxy`) in `/etc/vakr/config.toml`, or the standard `HTTPS_PROXY` and `NO_PROXY` environment variables (EGRESS-001).
- **Enterprise TLS inspection:** trust the proxy's CA with `sudo vakr tls trust-ca FILE`, which installs it in `/etc/vakr/tls/ca.d/` after you confirm its fingerprint (EGRESS-003). Certificate verification can never be turned off.
- **Diagnosis:** `vakr doctor` checks DNS, the proxy, the TLS handshake, and GitHub reachability step by step (EGRESS-005).

## Upgrade, removal, and purge

| Command | What happens |
|---|---|
| `sudo apt install ./vakr_<newer>_amd64.deb`, or `vakr self-update` | Replaces `/opt/vakr`. Your configuration is untouched, and `vakr.target` is restarted only if it was running. |
| `sudo apt remove vakr` | Stops vakr's units and removes `/opt/vakr` and the integration files. Keeps `/etc/vakr`, `/var/lib/vakr`, and `/var/cache/vakr` (PKG-005). |
| `sudo apt purge vakr` | Also removes `/etc/vakr`, `/var/cache/vakr`, and your enablement links for vakr units. Keeps the telemetry history in `/var/lib/vakr` and prints a notice (PKG-005). |

The `vakr` user and groups stay after a purge, so files left in `/var/lib/vakr` keep an owner. To delete the history as well, run `vakr data purge` (once that command exists) before removing the package, or delete `/var/lib/vakr` yourself afterwards.

Maintainers: the build and release process is in [Releasing vakr](../development/release.md).
