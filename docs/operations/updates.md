# Updates, proxies, and enterprise TLS inspection

This guide is for operators who run vakr on monitoring hosts. It covers:

- updating vakr from signed GitHub releases, by hand or with the daily timer;
- reaching GitHub through an enterprise proxy;
- trusting an enterprise TLS-inspection CA;
- installing on hosts without GitHub access;
- rolling back.

The requirements behind this behavior are in the blueprint, sections [28.3 Self-update](../../tool-blueprint.md#283-self-update) and [28.3.1 Outbound HTTPS](../../tool-blueprint.md#2831-outbound-https-proxies-and-enterprise-tls-inspection).

## How an update works

`vakr self-update` runs as root and makes no change to the host until the release passes verification. It runs these steps in order:

1. **Lock.** It takes `/run/vakr/self-update.lock`, so a second update (for example, the timer firing while you update by hand) fails immediately with "another update is already running".
2. **Discover.** It lists the 30 most recent releases of `update.repository` from the GitHub API and selects the newest release on `update.channel`, at or below `update.hold_version`.
3. **Download.** It saves `vakr_<version>_amd64.deb`, `SHA256SUMS`, and `SHA256SUMS.sig` into `/var/cache/vakr/updates/<version>/`.
4. **Verify.** It runs `ssh-keygen -Y verify` to check the signature on `SHA256SUMS` against the release signers in the installed package (`/opt/vakr/share/release-signers`, identity and namespace `vakr-release`). It then checks that the package's SHA-256 matches its line in `SHA256SUMS`. If either check fails, the update stops with nothing changed.
5. **Back up.** It writes `/var/lib/vakr/backups/pre-update-<UTC>.tar.gz` (mode 0600). The archive holds `/etc/vakr`, which includes `secrets/` and the trusted-CA directory, plus a consistent snapshot of every `/var/lib/vakr/*.db`. The five newest archives are kept.
6. **Prepare rollback.** It makes sure a verified package of the version that is running now is cached. If that package isn't cached, it downloads it by tag.
7. **Install.** It runs `apt-get install -y --allow-downgrades <package>` with `DEBIAN_FRONTEND=noninteractive`, so apt resolves dependencies.
8. **Post-install hooks.** These run through the **new** binary, `/opt/vakr/venv/bin/vakr`:
   1. `config upgrade`. Exit status 3 means the command is not available yet; this is logged and skipped.
   2. `systemctl try-restart vakr.target`, which restarts only the services that were running.
   3. `check health`. A CRITICAL result counts as a failure.
9. **Record.** It writes the outcome to `/var/lib/vakr/update-state.json`. On success it prunes the package cache to the installed version plus the two most recent older versions.

If the install or any hook fails, self-update rolls back automatically. See [Rollback](#rollback).

The command ends with a short summary, and for an update it includes an excerpt of the release notes:

```text
vakr self-update: updated: updated 0.4.0 -> 0.5.0
release notes (0.5.0):
  ## Changes
  - ...
```

## Checking for updates

```console
$ sudo vakr self-update --check
egress https://api.github.com/: ok
  [ok  ] dns: api.github.com -> 140.82.114.5
  [ok  ] tcp: connected to api.github.com:443 in 27 ms
  [ok  ] tls: TLSv1.3 verified; issuer 'CN=...'
  [ok  ] http: HTTP 200 from api.github.com
vakr self-update: available: 0.5.0 is available (installed 0.4.0)
```

`--check` changes nothing. It first tests the path to the GitHub API step by step (DNS, TCP or proxy CONNECT, TLS, HTTP), then reports `available`, `up-to-date`, or `skipped` with the reason. It exits 0 when the check completed, whatever the answer, and exits 1 when GitHub cannot be reached or the API refuses the request.

`--check` doesn't need root.

## Updating by hand

| Command | Effect |
|---|---|
| `sudo vakr self-update` | Installs the newest release on the channel, if it is newer than the installed version. It asks for confirmation first. |
| `sudo vakr self-update --yes` | Same, without asking. Without a terminal and without `--yes`, nothing is installed. |
| `sudo vakr self-update --version 0.5.0` | Installs that release, even if it is older or the same (a downgrade or reinstall). It accepts `0.5.0-rc.1`, `0.5.0rc1`, `0.5.0~rc.1`, and `v0.5.0-rc.1`. |
| `sudo vakr self-update --version 0.5.0 --if-newer` | Installs that release only if it is newer than the installed version. |
| `sudo vakr self-update --channel stable` | Overrides `update.channel` for this run only. |

Automatic selection never downgrades. To go back a version, use `--version` or `--rollback`.

## Configuration

Set these keys in `/etc/vakr/config.toml` or in a file under `/etc/vakr/conf.d/`.

| Key | Default | Meaning |
|---|---|---|
| `update.repository` | `robert-patrick-texas/vakr` | GitHub `owner/name` that releases come from. |
| `update.channel` | `prerelease` | `prerelease` accepts release candidates. `stable` accepts only final releases (neither marked pre-release on GitHub nor `-rc.N`). The default becomes `stable` at MVP acceptance (Appendix E.14). |
| `update.hold_version` | empty | Pins the deployment. Automatic selection never goes past this version. Timer runs refuse anything newer, even with `--version`. An operator at the console may still install a newer release explicitly with `--version`. |
| `network.https_proxy` | empty | HTTPS proxy, for example `http://proxy.corp.example:3128`. |
| `network.no_proxy` | `[]` | Destinations reached without the proxy. |
| `network.tls_strict_x509` | `true` | Enforce the strict X.509 profile. Set `false` only as a fallback for an inspection CA that can't be reissued; see [The strict X.509 profile](#the-strict-x509-profile). |

```toml
[update]
channel = "stable"
hold_version = "0.6.2"

[network]
https_proxy = "http://proxy.corp.example:3128"
no_proxy = ["corp.example", "10.0.0.0/8"]
```

## The daily update timer

The timer is disabled by default. To enable it:

```console
$ sudo systemctl enable --now vakr-update.timer
$ systemctl list-timers vakr-update.timer
```

The timer runs once a day, with a random delay of up to 4 hours so that a fleet doesn't update all at once. Its service runs:

```text
/opt/vakr/venv/bin/vakr self-update --yes --if-newer --unattended
```

In unattended mode the rules are stricter than at the console:

- It installs only a release newer than the installed version, at or below `update.hold_version`.
- A version whose install failed is **not retried** until a newer release appears. You can retry it by hand with `sudo vakr self-update --version X`.
- A release whose notes contain the marker `manual-update-required` is skipped. The release tool adds this marker when a release needs an operator, for example when rollback would also need a telemetry restore (UPD-005).
- Every step is sent to journald with a stable `EVENT_ID`.

```console
$ journalctl -t vakr-update --since today
$ journalctl EVENT_ID=update.failed
```

| Event ID | Meaning |
|---|---|
| `update.download.start` | Download of a release started. |
| `update.verify.ok`, `update.verify.failed` | Signature and checksum result. |
| `update.backup.done`, `update.install.start` | Backup written; package install started. |
| `update.hook.ok`, `update.hook.skipped` | Result of each post-install hook. |
| `update.done`, `update.failed` | Final outcome. |
| `update.rollback.start`, `update.rollback.done`, `update.rollback.incomplete` | Rollback progress. |
| `update.rollback.unavailable` | No verified previous package could be cached, so a rollback could restore only configuration and data. |
| `tls.ca.trusted`, `tls.ca.system`, `tls.ca.removed`, `tls.ca.system.removed` | Audit trail of trust changes (`journalctl -t vakr-tls`). |

`vakr check health` and CheckMK report the outcome of the last update. The `update` check is WARNING after a failed or refused update. It reads only the local state file, never the network.

## Proxies

vakr reaches GitHub (`api.github.com`, `github.com`, and the release-asset host `release-assets.githubusercontent.com`) either directly or through an HTTP proxy that supports `CONNECT`. TLS runs end to end through the tunnel, and the certificate is verified against the GitHub host name.

Each setting is resolved on its own, in this order:

1. `network.https_proxy` (or `network.no_proxy`) when it is set and not empty;
2. otherwise the environment: `HTTPS_PROXY`, then `https_proxy` (or `NO_PROXY`, then `no_proxy`);
3. otherwise no proxy (or no bypass entries).

`no_proxy` entries can take these forms:

| Entry | Matches |
|---|---|
| `*` | Every destination. |
| `corp.example` or `.corp.example` | `corp.example` and every name under it. `*.corp.example` is accepted too. |
| `10.1.2.3`, `::1` | That IP literal. |
| `10.0.0.0/8` | IP-literal destinations inside the network. Host names are not resolved for this match. |
| `host.example:8443`, `[::1]:443` | That host on that port only. |

The proxy URL must be `http://HOST:PORT`, with an explicit port. TLS to the proxy itself (`https://`) is not supported. **Credentials in the proxy URL are refused**, because plain configuration and environment variables end up in logs and support bundles. Proxy authentication will come from the credential provider in a later release.

## Enterprise TLS inspection

Some networks decrypt outbound HTTPS and re-sign it with an enterprise CA. vakr never turns certificate verification off: no option, flag, or environment variable does that. Instead, you add the enterprise CA to vakr's trust store. The CA must also meet the [strict X.509 profile](#the-strict-x509-profile), or you must explicitly relax that profile.

### Symptoms and diagnosis

```console
$ sudo vakr doctor --network
[CRITICAL] egress api.github.com tls: certificate verify failed: unable to get local issuer
certificate; server presented subject 'CN=api.github.com' issued by 'CN=Corp Inspection CA,O=Example
Corp' (SHA-256 4A:...); verify its fingerprint out of band, then run: vakr tls trust-ca FILE
```

`vakr doctor --network` and `vakr self-update --check` test each step to the GitHub hosts: DNS, TCP or proxy CONNECT, TLS handshake, and HTTP status. When the certificate chain ends at an issuer that this host doesn't trust, the output names the subject and issuer of the certificate the server presented. To read that certificate, vakr makes one extra handshake that doesn't verify it. That handshake sends no application data, and the certificate it returns is only displayed, never trusted.

If the issuer is your organization's inspection CA, get the CA certificate from your security team. If the issuer is something else, stop: someone may be intercepting the traffic.

### Trusting the CA

```console
$ sudo vakr tls trust-ca corp-inspection-ca.pem --name corp-inspection
subject:  CN=Corp Inspection CA,O=Example Corp
issuer:   CN=Corp Inspection CA,O=Example Corp
SHA-256:  4A:1F:...:9C
expires:  2029-03-31 23:59Z
Trust this CA as 'corp-inspection' for vakr outbound HTTPS? [y/N] y
installed /etc/vakr/tls/ca.d/corp-inspection.pem
```

Before you answer, compare the SHA-256 fingerprint with the value your security team publishes.

`trust-ca` accepts only a single PEM X.509 certificate that has `basicConstraints CA:TRUE` and is within its validity period. It refuses a leaf (server) certificate, an expired or not-yet-valid certificate, a DER or other non-PEM file, and a file that holds several certificates. By default it also refuses a CA that doesn't meet the [strict X.509 profile](#the-strict-x509-profile). A refused file installs nothing.

| Command | Effect |
|---|---|
| `vakr tls trust-ca FILE [--name NAME] [--yes]` | Installs into `/etc/vakr/tls/ca.d/NAME.pem` (mode 0644). The name defaults to the file name. It affects vakr only. |
| `vakr tls trust-ca FILE --system` | Also copies the certificate to `/usr/local/share/ca-certificates/NAME.crt` and runs `update-ca-certificates`. On the RHEL family it uses `/etc/pki/ca-trust/source/anchors/` and `update-ca-trust extract` instead. **This changes trust for every program on the host.** |
| `vakr tls list` | Shows each trusted CA with its expiry state, subject, and fingerprint. |
| `vakr tls remove NAME` | Removes the CA. If `--system` added identical bytes to the OS store, that copy is removed and the OS bundle is rebuilt. |

vakr's trust store is the OS bundle (`/etc/ssl/certs/ca-certificates.crt` on Ubuntu, `/etc/pki/tls/certs/ca-bundle.crt` on the RHEL family) plus every `*.pem` or `*.crt` file in `/etc/vakr/tls/ca.d/`. The bundled Python interpreter's compiled-in CA paths are never used. A malformed file in `ca.d` is an error that names the file. It is never silently skipped.

`vakr check health` reports WARNING when a trusted CA expires within 30 days, and CRITICAL once one has expired (the `trusted_cas` check). Install the replacement CA before the old one expires, then remove the old one.

### The strict X.509 profile

vakr runs on Python 3.13 or later, which checks certificates against OpenSSL's strict X.509 profile. vakr applies that profile to all outbound HTTPS. Many enterprise inspection CAs were issued before clients enforced it, and they fail it even after you trust them. To pass, a CA must:

- be an X.509 version 3 certificate;
- have `basicConstraints` with `CA:TRUE`, marked critical;
- have a `keyUsage` extension that includes `keyCertSign`;
- have a subject key identifier;
- have an authority key identifier, unless it is self-signed (a root CA).

The certificates that the inspection proxy issues for each site must also carry an authority key identifier.

**By default, `trust-ca` refuses a CA that fails the profile.** It lists each missing item:

```console
$ sudo vakr tls trust-ca corp-inspection-ca.pem
vakr: refusing corp-inspection-ca.pem: this CA does not meet the strict X.509 profile that Python
3.13+ enforces (VERIFY_X509_STRICT), so every handshake through it would fail. It lacks:
  - keyUsage extension (with keyCertSign)
  - subject key identifier (subjectKeyIdentifier extension)
Ask the team that runs this CA to reissue it with these items.
If a reissue is not possible, set network.tls_strict_x509 = false in the [network] table of the
vakr configuration (EGRESS-007). That relaxes only the strict-profile rules;
chain signatures, validity, purpose, and host names are still verified.
Then run trust-ca again; nothing was installed
```

Ask your security team to reissue the CA with the missing items. That is the right fix, because every other client on Python 3.13 or later fails the same way.

**If the CA can't be reissued**, add this to `/etc/vakr/config.toml`:

```toml
[network]
tls_strict_x509 = false
```

This clears only OpenSSL's strict-profile flag for vakr's outbound HTTPS. Certificate verification stays on. The chain must still end at a trusted CA with valid signatures, every certificate must be within its validity period and allowed for its purpose, and the host name must match. No vakr setting turns verification off. The change applies from the next command, with no restart.

While `tls_strict_x509` is `false`:

- `trust-ca` installs a CA that fails the profile, after a warning that lists what it lacks. The audit event records the missing items too.
- `vakr check health` reports WARNING. The `tls_policy` check says `strict X.509 checks relaxed for outbound HTTPS`.
- `vakr doctor --network` names the setting on each TLS step. `vakr self-update --check` prints a note.

Once the reissued CA arrives, trust it, remove the old one with `vakr tls remove NAME`, and delete the setting.

A CA whose `keyUsage` leaves out `keyCertSign` is refused in both modes. OpenSSL rejects such a CA whatever the setting, so it could never work.

**Diagnosing a strict-profile failure.** If the CA is trusted but the handshake still fails with a reason such as `Missing Authority Key Identifier` or `CA cert does not include key usage extension`, `vakr doctor --network` and `vakr self-update --check` make one more handshake with only the strict profile relaxed. That handshake still verifies the chain and the host name. It sends no application data, and its result only chooses the message. vakr never retries a real request with the profile relaxed.

```console
$ sudo vakr doctor --network
[CRITICAL] egress api.github.com tls: certificate verify failed: Missing Authority Key Identifier;
only the strict X.509 profile rejects it (it verifies with just that relaxed); server presented
subject 'CN=api.github.com' issued by 'CN=Corp Inspection CA,O=Example Corp' (SHA-256 4A:...); ask
for certificates that meet the strict X.509 profile; if that is not possible, set
network.tls_strict_x509 = false (EGRESS-007), which relaxes only those rules
```

If the relaxed handshake fails too, the output shows the reason that remains (for example a host-name mismatch) and doesn't suggest the setting, because changing it wouldn't help.

## Hosts without GitHub access

On a machine that can reach GitHub, download the three release files from the release page, `https://github.com/robert-patrick-texas/vakr/releases/tag/v<version>`:

- `vakr_<debian-version>_amd64.deb`; a release candidate is named `vakr_0.5.0~rc.1_amd64.deb`, though GitHub may show the `~` as `.`;
- `SHA256SUMS`;
- `SHA256SUMS.sig`.

Copy them to the host (for example with `scp`) and install:

```console
$ sudo vakr self-update --file vakr_0.5.0_amd64.deb --sums SHA256SUMS --sig SHA256SUMS.sig
```

The package's file name must be `vakr_<version>_amd64.deb`. Both the Debian spelling `0.5.0~rc.1` and GitHub's `0.5.0.rc.1` are accepted. The three files are first copied into the root-owned package cache and verified there, and the verified copy is what gets installed, so nobody with write access to the directory you copied them to can swap the package after verification. Verification, backup, hooks, and rollback work exactly as for an online update, with no network access. Because the files stay in the cache, the version can later serve as a rollback target.

To verify by hand before copying, run:

```console
$ ssh-keygen -Y verify -f /opt/vakr/share/release-signers -I vakr-release -n vakr-release \
    -s SHA256SUMS.sig < SHA256SUMS
$ sha256sum --check --ignore-missing SHA256SUMS
```

## Rollback

### Automatic

If `apt-get` fails, or a post-install hook fails (including a CRITICAL `check health`), self-update rolls back:

1. It reinstalls the cached, re-verified package of the previous version.
2. It restores `/etc/vakr` and the databases from the pre-update backup. Databases are replaced atomically, and stale `-wal` and `-shm` files are removed.
3. It runs `systemctl try-restart vakr.target`.
4. It records the failed version, so the timer doesn't retry it, and exits with status 1.

If no previous package could be cached, the output says so (`update.rollback.unavailable`). That can happen on the very first update of a host that was installed by hand while offline. In that case only configuration and data are restored.

### By hand

```console
$ sudo vakr self-update --rollback
```

This reinstalls the version that the last successful update came from (or, if there is no such record, the newest older cached version) and restores the newest pre-update backup. Configuration changes you made since the update are replaced, so the current configuration is first saved as `/var/lib/vakr/backups/pre-rollback-<UTC>.tar.gz`.

During the MVP, telemetry migrations are additive, so a rollback doesn't need a telemetry restore.

## Files

| Path | Contents |
|---|---|
| `/opt/vakr/share/release-signers` | Trusted release signing keys (`allowed_signers` format), shipped in the package. |
| `/var/cache/vakr/updates/<version>/` | Downloaded package, `SHA256SUMS`, and signature. The installed version and two older versions are kept. |
| `/var/lib/vakr/backups/` | `pre-update-*` and `pre-rollback-*` archives (mode 0600, five of each kept). |
| `/var/lib/vakr/update-state.json` | Last result, versions, message, and versions that failed to install. |
| `/etc/vakr/tls/ca.d/` | Extra trusted CAs for vakr's outbound HTTPS. |
| `/run/vakr/self-update.lock` | Update lock. |

## Exit status

| Status | Meaning |
|---|---|
| 0 | Updated, rolled back, already up to date, skipped by a rule, or (with `--check`) the check completed. |
| 1 | The update failed and was rolled back, verification refused the release, the operator declined, the lock is held, or GitHub could not be reached. |
| 2 | Usage or configuration error, for example `--file` without `--sums` and `--sig`, or an invalid `hold_version`. |

## Troubleshooting

| Message | What to do |
|---|---|
| `GitHub API rate limit exhausted ... resets at HH:MM UTC` | Unauthenticated API calls are limited per source address, and every host behind one NAT shares that limit. Wait until the reset time, or use `--file`. |
| `signature verification failed` | Treat this as possible tampering. Don't install by other means. Compare the release files with the GitHub release and report it. |
| `release signers file missing` | The installed package is damaged. Reinstall the current version with `--file`. |
| `another update is already running` | Wait for the other run (the timer or another operator) to finish. |
| `certificate verify failed ... issued by '...'` | See [Enterprise TLS inspection](#enterprise-tls-inspection). |
| `refusing ...: this CA does not meet the strict X.509 profile` or `only the strict X.509 profile rejects it` | Get the CA reissued. If that isn't possible, see [The strict X.509 profile](#the-strict-x509-profile). |
| `proxy refused CONNECT ...: HTTP/1.1 407` | The proxy requires authentication, which vakr doesn't support yet. Ask for an exception for the GitHub hosts, or use `--file`. |
