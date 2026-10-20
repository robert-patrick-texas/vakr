# packaging

Sources for the vakr `.deb` (blueprint 28.6, ADR-025). The package installs everything under `/opt/vakr`, with a bundled Python 3.15 and a locked virtual environment. Only OS integration points live elsewhere. Build with `make deb` and test with `make package-test`; the [release guide](../docs/development/release.md) explains both, and the [installation guide](../docs/operations/install.md) is the operator's view.

| Path | Purpose |
|---|---|
| `nfpm.yaml` | Package spec. Format-neutral at the top, Debian specifics under `overrides.deb`, so the later RPM adds `overrides.rpm` (PKG-006). |
| `Dockerfile` | Builder image: `ubuntu:26.04` plus uv and nfpm pinned by digest and version, lintian, and systemd |
| `build-deb.sh` | Host entry point (`make deb`): stages the source, derives the versions, and runs the container build |
| `container/build.sh` | Inside the builder: interpreter, venv, defaults, share files, bytecode, checks, nfpm, and lintian |
| `build-constraints.txt` | Hashed pins for the wheel build backend |
| `systemd/` | `vakr.target`, `vakr-update.service`, `vakr-update.timer` (UPD-003) |
| `sysusers.d/vakr.conf`, `tmpfiles.d/vakr.conf` | The `vakr` user and groups, and the directories with their modes |
| `scripts/deb/` | `postinst`, `prerm`, `postrm` (PKG-003, PKG-005, CFGM-005) |
| `templates/config.toml` | Fallback `/etc/vakr/config.toml` when `vakr config template` is unavailable |
| `examples/` | Optional files operators install by hand, never packaged: the collector-only `CAP_NET_RAW` drop-in for hosts that cannot enable unprivileged ICMP ([ICMP operations](../docs/operations/icmp.md), ADR-023) |
| `release-signers` | Trusted release keys in `allowed_signers` format; a placeholder until the owner adds the key |
| `lintian/vakr` | Lintian overrides, each with its justification (PKG-004) |
| `copyright` | Debian copyright file covering vakr and the bundled runtime |
| `package-test.sh`, `container/*-test.sh`, `container/Dockerfile.systemd` | Container install, upgrade, removal, and systemd tests |
| `tests/` | Fast static checks of these files, run first by `make package-test` (`uv run pytest packaging/tests`) |

Rules for changes here:

- A new service unit joins the target with `WantedBy=vakr.target` and `PartOf=vakr.target`, so the upgrade restart and removal stop reach it. A unit outside the target must be added to `prerm`. `vakr-update.service` must never join the target, because the update restarts it.
- New lintian overrides need owner approval and a written justification, and must be confined to `/opt/vakr`. `tests/test_packaging.py` enforces this.
- Nothing in `/etc` is ever a dpkg conffile (CFGM-005).
