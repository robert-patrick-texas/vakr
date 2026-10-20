# Changelog

All notable changes to vakr are recorded here. Versions follow Semantic Versioning, and the version itself lives only in [`VERSION`](VERSION). Until MVP acceptance, minor releases may contain breaking changes; each one is listed here along with the migration that handles it.

## Unreleased

### Added

- Compact SQLite telemetry storage (`telemetry.db`, ADR-021 layout C+H): day-partitioned native tables with a staging table, monthly rollup tables, exact 30/400-day retention by horizon with whole-partition drops, and a `telemetry_db` health check. `vakr db status|migrate|backup` now cover both databases (`vakr db status telemetry` for one).
- The scheduler (`vakr.scheduler`): SHA-256 phase placement, a due-time plan, bounded workers, the poll state machine with ICMP gating, and an asyncio driver with graceful shutdown. A missed or overrun poll is recorded as not attempted or missed, never as a device outage (SCHED-001..013, SCHED-016..018). It is not yet wired to collection.
- New options: `polling.default_interval`, `polling.reachability_strategy`, and the `scheduler.*` group (collector name, phase and order modes, workers, queue capacity, lateness, start delay, poll deadline, shutdown grace). `vakr config validate` checks that `scheduler.late_after` does not exceed `scheduler.max_start_delay`.
- Compact SQLite telemetry layout decided by benchmark (ADR-021, Appendix E.21); measured sizing in blueprint 27.3.
- Durable observation spool and batched ingestion (`vakr.ingest`, STORE-004 to STORE-011): collectors hand observations to an fsynced, append-only spool under `/var/lib/vakr/spool/<collector UUID>/`, and ingestion writes them to the telemetry database in idempotent batches. Losses are recorded as telemetry-loss incidents (STORE-007). See [the spool guide](docs/operations/spool.md).
- `[spool]` options (`directory`, `max_size_mib`, `segment_size_mib`, `fsync_interval`, `fsync_records`, `queue_capacity`, `warning_pct`, `critical_pct`, `exhausted_pct`, `ingest_interval`, `ingest_max_rows`). Their defaults apply without any edit.
- A `spool` check in `vakr check health` and `vakr doctor`: fill level, backlog age, database write failures, recent loss incidents, and quarantined files.
- SNMPv2c collection with PySNMP's asyncio API (ADR-034): discovery of the system group, IF-MIB, and IP-MIB with per-table completeness; Stage 1 status polling by exact OID; Stage 2 counters for up/up interfaces with HC preference, 32-bit fallback, and learned capabilities; one request in flight per device, tooBig size halving, and counted retries (SNMP-001..019, DISC-004..009).
- `vakr snmp discover` and `vakr snmp poll TARGET --credential NAME`, which check one device and report in plain language. A community is never accepted on the command line.
- New `[snmp]` options: `port`, `timeout`, `retries`, `max_varbinds`, `max_repetitions`, `max_walk_rows`, `rediscovery_interval`, and `capability_reprobe_interval` (Appendix E.23). Their defaults apply without any edit.
- The headless collector, `vakr-collector.service` (`vakr collector run`): it polls the inventory with ICMP and SNMP on the scheduler, discovers devices without a poll plan and rediscovers every device daily, derives traffic, packet, and error rates from checkpointed baselines so a restart costs no rate interval, makes every result durable in the spool, writes it to the telemetry database, reports its own health at least once per poll interval, and pauses polling while the spool is full. It runs startup checks before polling, drains gracefully on stop, and reloads its configuration on `systemctl reload`. The package installs the unit disabled. `vakr collector once DEVICE` runs one diagnostic poll and explains it. New options `rates.max_gap_intervals` and `rates.plausibility_factor` (Appendix E.4, E.27); their defaults apply without any edit. See [the collector guide](docs/operations/collector.md).
- A synthetic 100-device IOS XE simulator served by snmpsim, and `tools/snmpsim/walk2rec.py`, which turns field-kit walks into snmpsim data.
- `vakr status [DEVICE] [--json]` and a read-only `vakr tui` (Textual) show current state from the local databases: devices with Attention first, each status as an icon and a label, the age and freshness of every value, and "—" with a reason wherever data is missing, never zero. Collector or storage trouble reads as Unknown, never Down. `vakr status` exits with Nagios codes (0 OK, 1 attention, 3 unknown). New option `tui.refresh_interval` (10 s). See [the status and TUI guide](docs/operations/status-and-tui.md) (TUI-001..008, Appendix E.29).
- File-managed inventory and interface identity: `vakr inventory validate|plan|apply|list` for `/etc/vakr/inventory.toml` (sites, devices, interface annotations; removed devices are disabled, never deleted), the audited `vakr interface claim`, conservative identity reconciliation on rediscovery with `vakr interface review list|map|new` for uncertain cases, and the collector-facing `InventoryService`. New options `inventory.file`, `security.allowed_target_cidrs`, and `security.denied_target_addresses` (cloud metadata services denied by default). Metadata schema 2 adds identity reviews: run `vakr db migrate` (it takes a backup first). See [the inventory guide](docs/operations/inventory.md).

## 0.1.1 - 2026-10-20

Second pre-release, cut to prove the self-update path from `0.1.0` on production hosts.

### Added

- `update.health_settle_seconds` (default 30, new in this release): after an update installs, the post-update health check repeats for up to this many seconds while it reports CRITICAL, so services that are still restarting are not mistaken for a failed update and rolled back (UPD-006). Also available as `vakr check health --retry-for SECONDS`.
- `vakr self-update` now ends its summary with the configuration options added since the previously installed release (CFGM-004).
- The package now runs `vakr config upgrade` on every install and upgrade. That applies config migrations even for manual `apt install` upgrades, and records the installed release so later `vakr config new-options` runs have a baseline.

### Notes for operators updating from 0.1.0

- `0.1.0`'s updater does not yet print new options. After `sudo vakr self-update`, run `vakr config new-options --since 0.1.0` to see `update.health_settle_seconds`. Its default applies without any edit.
- The settle window protects updates made *from* `0.1.1` onward.

### Changed

- Building from source now rebuilds the installed package whenever `VERSION` changes (uv cache keys), so `vakr --version` always matches `VERSION`.

## 0.1.0 - 2026-10-20

First pre-release. It delivers the pipeline only: the package, signed self-update, configuration upgrade, and the field data kit. Production hosts can install it, update themselves, and send field data back, but vakr does not monitor any devices yet. Monitoring arrives in milestones M2–M4.

### Added

- Blueprint revision 1.1: MVP definition, delivery and lifecycle tooling, portable tools, and default values.
- Owner decisions of 2026-10-20 recorded in the blueprint: install under `/opt/vakr`, enterprise proxy and TLS-inspection CA support, a strict X.509 policy with an opt-in fallback, `openssl cms` field bundles, and interface classification from description keywords.
- `vakr` CLI with `--version`, `doctor` (and `--network`), and `check health`, a Nagios-format self-check for CheckMK/Nagios (OPS-001).
- Configuration catalog with packaged defaults, operator overrides, versioned config migrations, and the commands `vakr config show-effective`, `validate`, `upgrade`, `new-options`, `defaults`, and `template` (CFGM-001..007).
- `vakr self-update`: verified and signed GitHub-release updates with backup, rollback, an optional daily timer, offline install, and `hold_version` (UPD-001..008).
- Outbound HTTPS through proxies with an extra trusted-CA directory, and `vakr tls trust-ca` / `list` / `remove` for enterprise TLS-inspection CAs (EGRESS-001..006).
- `.deb` package for Ubuntu 26.04 that installs under `/opt/vakr` with a bundled Python 3.15, built reproducibly in a container (`make deb`, `make package-test`; PKG-001..005).
- `tools/release`: signed releases (SHA256SUMS plus an SSH signature), with a dry run.
- `tools/fieldkit`: one-command production data collection into one encrypted bundle (CMS AES-256-GCM), plus the developer-side `field_inbox.py` (FIELD-001..010).
- `tools/check-updates` (`make updates`), a dependency vulnerability audit (`make audit`), and Dependabot (TOOLS-001..003, QG-008).
- `tools/check-docstrings` (QG-003), `tools/check-loc` (QG-004), and `tools/check-version` (VER-003) quality gates; CI with a gitleaks secret scan.
- Documentation site: a GitHub link and a left-side page index on every page (DOCPUB-004).
- `tools/md2html`: portable Markdown-to-HTML converter with a link checker, plus a GitHub Pages workflow.
- Apache-2.0 license, `VERSION`, and `CLAUDE.md` agent instructions.
