# Changelog

All notable changes to vakr are recorded here. Versions follow Semantic Versioning, and the version itself lives only in [`VERSION`](VERSION). Until MVP acceptance, minor releases may contain breaking changes; each one is listed here along with the migration that handles it.

## Unreleased

### Added

- The web UI (ADR-035, UI-001 to UI-017): a static SvelteKit app that `vakr-api` serves at every path outside `/api/`, same origin as the API, so the reverse proxy needs nothing new. It has sign-in and sign-out, global navigation and search, the overview (device state, health, high-priority interfaces, WAN circuits, top utilization and errors, events, quick filters), the device list in Attention, Active, Offline/Retired order with filters and browsing by site, tag, group, provider, collector, freshness, and admin state, device and interface pages, graphs (traffic, packets, errors, utilization, the interface state timeline, and SmokePing-style latency with every probe, the median, and loss) with range presets from 1 hour to 400 days or a custom range, rankings, high-priority and WAN pages, collector health, and identity reviews with the admin's decision. Missing data is a gap, never zero; every graph names its resolution and time zone and has a table of exact values and CSV/JSON downloads. Every page tells loading, empty, stale, partial, permission-denied, and backend-unavailable apart, and status always has an icon and words beside its colour. See [the web UI guide](docs/operations/web-ui.md).
- The package installs the web UI in `/opt/vakr/share/web/` and the bundled front-end packages' licence and notice files in `/opt/vakr/share/doc/web-licenses/` (all MIT, Apache-2.0, BSD-3-Clause, or 0BSD; listed in the copyright file).
- Pages carry a Content-Security-Policy without `unsafe-inline` or `unsafe-eval`: SvelteKit's bootstrap script is allowed by its SHA-256 hash, read from the build's manifest. Content-hashed files are cached for a year; pages revalidate. Without the web files, `vakr-api` logs `api.web_unavailable` once and serves the API alone.
- `GET /api/v1/devices` filters by evidence freshness (`freshness=fresh|stale|never`, repeatable) and by collector (`collector=<uuid>`), as the device list of blueprint 12.3 requires; `GET /api/v1/devices/{uuid}/interfaces` takes `interest=primary|secondary` (UI-017). `docs/api/openapi.json` is regenerated.
- Developers: `web/` (Node 24 LTS, npm, exact pins, `npm ci --ignore-scripts`). `make check` now also runs ESLint (300-line files, 50-line functions, TSDoc, no raw HTML, no inline styles), svelte-check, Prettier, Vitest, a drift check of the TypeScript types generated from the OpenAPI document, the production build with its size budget, and a licence allowlist. `make web-e2e` runs the Playwright suite (WEB-T01 to WEB-T11, with axe for accessibility) against a real `vakr api serve` on seeded databases, and `make web-audit` runs `npm audit`. CI runs all of them. See [developing the web UI](docs/development/web.md).
- `tools/check-updates` reports newer npm packages of `web/` and newer Node LTS releases; the release preflight checks that Node and npm are installed and that Node is the release `web/.node-version` pins.
- The hourly 15-minute rollup job (RET-004 to RET-009): the collector now rolls up every complete quarter-hour before retention runs, so native data finally expires after 30 days (ROLL-T04). A 0.2.x backlog drains in bounded passes of at most 7 days and 2 minutes each. A new `rollups` check in `vakr check health` and `vakr doctor` warns when rollups fall behind. The rollups themselves need no schema change. See [rollups](docs/operations/rollups.md).
- Local accounts for the web UI and API (SEC-005, SEC-006): `sudo vakr setup` creates the first admin, and `sudo vakr user list|add|passwd|disable|enable|role|revoke-sessions` manages accounts. Passwords are asked for twice at a hidden prompt and never accepted as arguments. Every change is audited with the invoking operator. See [the users guide](docs/operations/users.md).
- The authentication layer below HTTP (`vakr.auth`): Argon2id hashing with transparent rehash on login (SEC-009); opaque server-side sessions with idle and absolute expiry, rotation, and revocation (SEC-010); kiosk tokens for wall displays; CSRF tokens bound to sessions (SEC-011); per-account-and-address login throttling (SEC-012); and the Unix-socket peer-credential roles from Appendix E.12. The `vakr-api` service will use it.
- New `[auth]` options: `session_idle_timeout` (30 min), `session_lifetime` (12 h), `kiosk_token_lifetime` (30 d), `login_throttle_after` (5), `login_throttle_max_delay` (30 s), `login_failure_reset` (15 min), and `password_min_length` (15). Their defaults apply without any edit.
- Metadata schema 3 adds accounts, sessions, kiosk tokens, and failed-login counts. The package runs `vakr db migrate`, which takes a backup first.
- New runtime dependency `argon2-cffi` (MIT).
- The `vakr-api` service (`vakr api serve`, `vakr-api.service`, shipped disabled): JSON API v1 on loopback HTTP (`127.0.0.1:8080`) for a TLS reverse proxy, and on the local socket `/run/vakr/api.sock`, where root and `vakr-admin` members are admins, `vakr` members operators, and everyone else is refused (E.12). It serves the device list, device and interface detail, traffic, packet, error, utilization, status, and reachability history with disclosed resolution and one series per position, the overview, rankings, high-priority and WAN views, search, sites, tags, groups, collector health, identity history and reviews, bounded CSV/JSON exports, and the admin's identity-review decision (API-001 to API-008, API-012). See [the API guide](docs/operations/api.md).
- Sign-in over HTTP (SEC-010 to SEC-013): `POST /api/v1/auth/login` sets an `HttpOnly`, `SameSite=Lax` session cookie, `Secure` and `__Host-` prefixed over HTTPS; sign-in over plain HTTP is refused except from loopback. Every cookie-authenticated change needs the session's `X-CSRF-Token`; cross-site browser requests are refused; CORS is off. Failed sign-ins are delayed and throttled with `Retry-After`. Forwarded headers count only from `api.trusted_proxies`. [Web access](docs/operations/web-access.md) has a complete nginx configuration.
- Every API answer has a stable error envelope (`code`, `message`, `request_id`, `fields`), the security headers of blueprint 15.4, and `Cache-Control: no-store`; request bodies are limited to 64 KiB.
- The OpenAPI document of API v1 is checked in as [`docs/api/openapi.json`](docs/api/openapi.json); `vakr api openapi` prints it and `make openapi` regenerates it (API-008).
- A new `api` check in `vakr check health` and `vakr doctor` asks the running API over its socket. It is OK while the service is not installed or not running.
- New `[api]` options: `listen_address` (`127.0.0.1`), `port` (8080), `socket_path` (`/run/vakr/api.sock`), `trusted_proxies` (none), and `max_body_bytes` (65536). Their defaults apply without any edit.
- The ranking projection (E.28 decision 1, DASH-003, DASH-004): the collector keeps exact per-quarter-hour and per-hour ranking partials for the last 25 hours, so 6-hour and 24-hour top-utilization and top-error rankings answer within the 2 s of blueprint 12.9 with the same values as before. At the MVP workload the 24-hour ranking's p95 fell from 4.7 s to 0.84 s and the 6-hour one's from 1.2 s to 0.33 s. The projection is rebuilt from native data at every collector start and updated every 15 minutes; it takes about 220 MiB. Telemetry schema 2 adds its two tables. See [ranking projection](docs/operations/rollups.md#ranking-projection).
- A new `rankings` check in `vakr check health` and `vakr doctor` warns when more than an hour of recent data is not projected (rankings then stay exact but slower). It is never CRITICAL.
- `GET /api/v1/device-lookup?key=` (operator) answers the device screen for an exact display name, else a device UUID, exactly as `vakr status DEVICE` looks devices up (E.30 #15). `docs/api/openapi.json` and the web UI's generated types are regenerated.
- `vakr status --local` and `vakr tui --local` read the local databases directly, as before, even while `vakr-api` runs.
- New runtime dependencies `fastapi` (MIT) and `uvicorn` (BSD-3-Clause), with `starlette`, `pydantic`, `pydantic-core`, `h11`, `anyio`, `idna`, `click`, `annotated-types`, `typing-inspection`, `annotated-doc`, and `opentelemetry-api`; licenses are listed in the package's copyright file.

### Changed

- `vakr status` and `vakr tui` now read through the running `vakr-api` over its local socket (blueprint 13.1, TUI-007, Appendix E.29 #10), with the same answers, text, JSON, and exit codes as before. Root and members of `vakr-admin` or `vakr` can run them without `sudo`; another account is told how to join `vakr`. When there is no socket (the API is shipped disabled), they read the databases directly, as before. A socket that exists but does not answer makes both exit with status 3, naming the socket and what to check, rather than quietly reading the databases. The output says where the data came from: a new `data:` line in `vakr status`, a new `"source"` field (`"api"` or `"local"`) in `--json` (schema `vakr.status/1` is unchanged, since nothing was removed or renamed), and the TUI header, which also shows how long the last read took. Over the socket a connect may take 2 s, a request 30 s, and an answer 16 MiB, with no retries (E.29). See [where the data comes from](docs/operations/status-and-tui.md#where-the-data-comes-from).
- A TUI screen has at most one read in flight: refreshes asked for while one runs (one per key typed into the search) are dropped in favour of the newest, instead of each holding a worker thread.
- `vakr tui --api-url` and the new `--profile` are refused with the reason: a TUI on another host needs API credentials for a person, which do not exist yet.
- `vakr status` on an unusable telemetry database now says "telemetry database unusable: …", as `vakr-api` does.
- Every API answer under `/api/` still carries `Cache-Control: no-store`; the web UI's files outside `/api/` get their own cache headers (ADR-035).
- `/run/vakr` is now mode 0751 (was 0750), so members of `vakr-admin` who are not in `vakr` can reach the API socket; `vakr-api` decides each connection's role itself. The package's tmpfiles entry applies it at installation and every boot.
- The install and field-data guides find the newest release themselves instead of asking for a version. GitHub's "latest release" links skip pre-releases, so the guides read the same release list that self-update uses.
- Ranking packet sums, behind errors per million packets, are now summed exactly (`math.fsum`), so the result never depends on the order or split in which samples are read (E.28). No measured count changed.
- Until the collector has built the ranking projection after a start (about 25 s at the MVP workload), a 24-hour ranking reads every native sample and takes about 6 s, against 4.7 s in 0.2.x, because the exact packet sum reads each sample.

### Notes for operators updating from 0.2.x

- **This release is marked `manual-update-required`.** It migrates the telemetry schema (schema 2, `0002_ranking_projection.sql`: the ranking projection), and self-update never copies `telemetry.db`, so an automatic rollback could not restore it. The update timer skips this release; install it with `sudo vakr self-update`. The package's `vakr db migrate` applies schema 2 in milliseconds; the collector then builds the projection within its first minute.
- To go back to 0.2.x after updating, stop `vakr-collector` and `vakr-api` and remove schema 2 before installing the older release, as [the rollups guide](docs/operations/rollups.md#going-back-to-a-release-without-the-projection) shows. The projection holds only derived data, so nothing is lost.

## 0.2.1 - 2026-10-20

Fixes from the first production walk-through (0.2.0 installed from GitHub, a device polled, and a field bundle collected and decrypted). No configuration options or database schemas change.

### Changed

- `vakr config new-options` lists new options by section, names only, so the summary self-update prints after an update stays short (43 options in about a dozen lines). `--describe` shows each option's type, reload behaviour, and description, as before.

### Fixed

- The field kit's GitHub reachability test got HTTP 404 from the release API, because it asked for `/releases/latest`, which ignores pre-releases. It now lists releases the way self-update does.

### Added

- The package recommends `iputils-ping`, which the field kit's `paths` profile needs. Minimal images lack it.
- `make github-update-test` (developers): after a release is published, installs the previous release from GitHub in a systemd container, self-updates to the new one, rolls back, and updates again through the timer unit, checking every step.

### Notes for operators updating from 0.2.0

- `sudo vakr self-update`, or the update timer, installs it. Nothing needs editing: there are no new options and no migrations.
- The update also installs `iputils-ping` if it is missing, unless apt is configured not to install recommended packages. In that case, run `sudo apt install iputils-ping` before collecting a field bundle.
- Field bundles collected with this release report HTTP 200 for the GitHub release API, where 0.2.0's field kit reported 404.

## 0.2.0 - 2026-10-20

First release that monitors devices (milestones M2 and M3): SNMPv2c and ICMP collection for Cisco IOS XE, a durable spool, Compact SQLite storage, file-managed inventory with conservative interface identity, and current state in `vakr status` and `vakr tui`. Graphs, the web UI, and rollups arrive in M4.

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

### Behaviour to know about (owner decisions of 2026-10-20)

- A host that cannot send ICMP Echo is a hard stop: `vakr-collector` refuses to start (exit 78, not restarted) when `net.ipv4.ping_group_range` excludes the `vakr` group and no `CAP_NET_RAW` drop-in is installed, and stops the same way if permission is withdrawn while it runs. The `icmp` check in `vakr check health` is now CRITICAL; self-update counts it as a warning, because rolling a release back cannot fix a host setting. `vakr doctor` prints the exact fix.
- Utilization percentages are shown only for physical ports and port-channels. Tunnels, loopbacks, VLAN interfaces, and subinterfaces report nominal speeds (IOS XE: 100 kb/s on many tunnels), so their traffic is shown without a percentage unless `capacity_override_bps` is set.
- A poll cut short by shutdown is recorded as `cancelled`, with the evidence its finished steps produced, instead of partial or failed with a collector-degraded flag.
- Interface rollup values are stored at single precision (ADR-021 amendment 3), keeping the worst case at about 40 GB per node.
- Each device is polled as a whole at one interval: its `interval` in `inventory.toml`, or `polling.default_interval`. Settings inside collection rules in `policies.toml` (such as `polling_interval` on an interface rule) are shown by `vakr policy explain` but not applied yet; `vakr policy validate` warns about each one and names the device-level field to use.

### Notes for operators updating from 0.1.1

- `sudo vakr self-update` installs it. The update migrates the configuration (43 new options, all with packaged defaults; `vakr config new-options --since 0.1.1` lists them) and creates both databases (`metadata.db` at schema 2 and the new `telemetry.db`).
- Nothing is polled until you enable the collector. To start monitoring:
  1. Put the SNMPv2c community in `/etc/vakr/secrets/credentials.toml` (mode 0640, owner root:vakr) under a name such as `[credentials.network-v2c]`; `vakr credentials check` verifies it.
  2. List the devices in `/etc/vakr/inventory.toml` (see the inventory guide), then run `vakr inventory validate`, `vakr inventory plan`, and `sudo vakr inventory apply`.
  3. Run `vakr doctor`. The `icmp` line must be OK: a host whose `net.ipv4.ping_group_range` excludes the `vakr` group is a hard stop, and the line prints the one-line fix.
  4. `sudo systemctl enable --now vakr-collector.service vakr.target`, then watch `vakr status` or `vakr tui`. Interfaces appear after the first discovery, and rates after the second poll.
- `vakr snmp discover ADDRESS --credential NAME` and `vakr icmp probe ADDRESS` check one device before you enable the collector.

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
