# Changelog

All notable changes to vakr are recorded here. Versions follow Semantic Versioning, and the version itself lives only in [`VERSION`](VERSION). Until MVP acceptance, minor releases may contain breaking changes; each one is listed here along with the migration that handles it.

## Unreleased

### Changed

- Developers: `make secrets` scans every tracked file for secrets with gitleaks 8.30.1 (QG-007), and CI's `secrets` job runs it after the gitleaks action. The action scans only the first-parent commits of a push, so commits that arrived on a merged branch were never scanned. `.gitleaks.toml` keeps the default rules and allows only interface names such as `GigabitEthernet0/0/0`, which read as access keys. The action now installs the same gitleaks release, and `make updates` watches both pins.

## 0.6.1 - 2026-10-27

Fix release for two-position deployments. It changes no database schema and adds no options, so the update timer installs it, and `sudo vakr self-update --rollback` returns to 0.6.0. A single-position node behaves exactly as on 0.6.0.

### Fixed

- Two positions: a replication page now counts the interface status records it carries against its 6 MiB budget (E.13, ADR-024 decision 31). Before, a page near the budget whose polls carried many status records, such as the baseline recorded at every collector start, could pass the partner's 8 MiB answer limit and be refused on every retry, stopping observation replication at that point. Only two-position deployments are affected.

### Changed

- Developers: `make version` (VER-003) no longer counts another package's pin at the same number as a hard-coded copy of vakr's version, so a dependency pinned at `==`, `>=`, `<=`, `~=`, `!=`, or `===` that number passes; a pin of vakr itself still fails (`--project vakr`).

## 0.6.0 - 2026-10-27

The first M6 (MVP acceptance) release. It adds the tools that turn the parallel run with MRTG and SmokePing into acceptance evidence, manual device entry, bulk inventory import and export, and node promotion. It adds no options. Telemetry migration `0004_interface_status.sql` is additive (`compat: additive`), and every release that can be rolled back to (0.4.0 and later) reads it, so this release carries no manual-update marker and the update timer installs it (E.14).

### Added

- **Manual device entry** (27.4, META-008, META-009; Appendix E.37; [inventory](docs/operations/inventory.md)).
  - **Ways to do it:** admins can add, change, and retire devices in the web UI (**Devices → Add a device**, **Edit device**), with `vakr device add|set|remove|show`, or through API v1 (`POST /api/v1/devices`, `PATCH` and `DELETE /api/v1/devices/{uuid}`). The same checks as `vakr inventory apply` apply.
  - **Effect:** devices added this way are managed in vakr, and collectors discover them within a minute. Retiring a device keeps its history.
  - **Ownership:** devices that `inventory.toml` manages are shown read-only, naming the file.
- **Classifying interfaces in vakr** (META-007, META-011, META-013; E.37).
  - The interface page shows and edits the annotation: interest, sticky, WAN circuit, provider, circuit, rates, vakr tags, and comment.
  - The interface list can select interfaces and tag them at once, and both interface lists offer **Only not yet classified**.
  - The same is available as `vakr interface annotate` and `vakr interface tag`, and as `PATCH /api/v1/interfaces/{uuid}/annotation` and `POST /api/v1/interfaces/tags`.
- **Bulk inventory import and export** (META-010, META-013; E.38; [bulk import and export](docs/operations/inventory.md#bulk-import-and-export)).
  - **Import:** `vakr inventory import FILE` reads a device CSV, an interface-annotation CSV, or a TOML file in the `inventory.toml` format, and shows what it would change. `--apply` writes it in one transaction, audited per change. Problems are listed by row and column, and any one of them stops the whole import.
  - **Ownership:** an import writes the same records the web UI writes, and new ones are `api:import`. It never changes what `inventory.toml` manages or claims, never moves a record between owners, and never disables or deletes anything. A column left out keeps what is stored; an empty cell clears it.
  - **Export:** `vakr inventory export [--format toml|csv]` writes the stored sites, devices, and interface annotations with their UUIDs and owners, byte for byte the same for the same records. Importing an export unchanged plans as no changes, and a TOML `--owner file` export is a valid `inventory.toml`. `--unannotated` lists every interface with its description, for classifying interfaces in a spreadsheet.
- **Node promotion** (MPOS-003, MPOS-006, ADR-024 decisions 23–30, E.39; [losing the primary](docs/operations/two-positions.md#losing-the-primary)).
  - **Promote:** `sudo vakr node promote` makes a peer the metadata primary when the primary is lost. It reports the newest metadata pull and its age, and refuses without `--force` while the old primary still answers or when that cannot be told. It rewrites `[node]` for you, with a backup, records the old values, audits the change, and restarts only `vakr-collector` and `vakr-api`. Observations keep replicating both ways.
  - **Two primaries:** while the partner's latest "primary" answer is at most 5 minutes old, the `replication` check of `vakr check health` is CRITICAL and names the node to demote. `vakr status`, the TUI, and the web UI show `TWO PRIMARIES`. After 5 minutes with no newer answer, for example when the old primary was switched off instead of demoted, the check is a WARNING saying when the partner was last seen as a primary. No device's state changes.
  - **Demote:** `sudo vakr node demote` makes a returning old primary a peer of the new one. It writes its resync marker before it changes the role; if it cannot, nothing changes and it exits 1. At its next pull the node takes one backup of `metadata.db`, however many attempts the resync needs. It writes its report of discarded edits before it discards anything, then takes the new primary's metadata exactly.
  - **Status:** `vakr node status [--json]` shows the role, the primary, the newest pull, any conflict, and the newest resync.
- **Acceptance evidence from the node's own data** (MVP-ACC-01, MVP-ACC-02, MVP-ACC-10; E.35; [acceptance reports](docs/operations/acceptance.md)).
  - `vakr acceptance schedule` reports on-time poll starts, same-device overlap, and drift.
  - `vakr acceptance gaps` lists every period without data, with the recorded evidence that explains it.
  - `vakr acceptance coverage` checks graph coverage from 1 h to 400 d, with resets rather than spikes across reloads.
  - Each prints text or `--json`, per position, and ends in a `RESULT` line. `vakr field collect --profile vakr` brings all three back.
- **The parallel run with MRTG and SmokePing** (MVP-ACC-03, MVP-ACC-04, FIELD-002, FIELD-004, FIELD-005; E.34; [field data](docs/operations/field-data.md)).
  - `vakr acceptance export` writes this node's interface samples and ICMP cycles over a window (50 h by default). It is read-only and bounded.
  - The field kit gains `mrtg`, `smokeping`, and `acceptance` profiles. They collect MRTG's and SmokePing's targets and recent data without any credential, plus vakr's export. Run `vakr field collect --profile host,mrtg,smokeping,acceptance,vakr --pseudonymize` daily during the parallel run, never more than 48 hours apart. rrdtool is needed for RRD files.
  - New field kit options: `--mrtg-config`, `--smokeping-config`, `--compare-hours`, `--max-targets`, `--acceptance-targets`, `--acceptance-max-rows`, `--rrdtool`.
- **Interface status records** (E.35, MPOS-004).
  - Telemetry migration `0004_interface_status.sql` (additive). Stage 1 records an interface's admin and oper status when it changes, and once per interface after each collector start. They are status records, not samples: down interfaces still get no traffic records.
  - A link that goes down and comes back while its device answers is now explained as `interface_down` by `vakr acceptance gaps`.
  - The records replicate to the partner with their poll and expire with native data. They cost about 73 MB per node at a pessimistic rate.
- `inventory.toml` interface annotations accept an optional `uuid`, so an annotation keeps applying after its interface is renamed. Existing files need no change.
- Developers:
  - `make resilience-test` runs the MVP-ACC-06 resilience system tests (restart, reboot, telemetry store unavailable, power loss) against the package (E.36; [resilience tests](docs/development/resilience-tests.md)).
  - `make acceptance-report` turns the parallel-run field bundles in `inbox/` into the ACC-03 and ACC-04 report ([parallel run](docs/development/parallel-run.md)).
  - The two-position harness has a promote scenario, MPOS-T05 (`MPOS_FEATURES=promote`).

### Changed

- Interface lifecycle now follows each Stage 1 poll (blueprint E.6). An interface that goes down shows `inactive` in `vakr status`, the TUI, and the web within seconds, instead of at the next rediscovery. The interface table says "not up/up" only when a Stage 1 status record says so, and shows that status, such as "not up/up (up/down)". Stale, retired, and unsampled interfaces keep their usual wording. On a peer, lifecycle comes from the primary.

### Fixed

- After a crash or power cut, the `unclean_stop` telemetry-loss incident now estimates the loss from the busiest second among the newest spool records. Its time range now runs from the last durable record to the restart (SPOOL-T08, E.24). Before, after a quiet spell, it could undercount a lost burst of poll results and give a time range that did not contain them.
- `vakr replication status --json` no longer fails on a peer that has a pull record.
- Observation and metadata replication keep running when a state file under `/var/lib/vakr/replication` cannot be written, for example on a full disk. The failure is recorded as node health (MPOS-006). Before, the replication task stopped until the collector restarted.
- `sudo systemctl reload vakr-collector` on a peer now applies a copied `policies.toml` and interface keywords. Before, they took effect only after a restart.

### Notes for operators

- From 0.5.0, nothing is needed: the update timer installs this release, or run `sudo vakr self-update`. `sudo vakr self-update --rollback` returns to 0.5.0, which reads the new telemetry schema because the migration is additive.
- During the parallel run, run the field kit daily as described in [field data](docs/operations/field-data.md#the-parallel-run-with-mrtg-and-smokeping), and send the bundles as before.

## 0.5.0 - 2026-10-27

Maintenance release. vakr now ships on the newest release of every tool and dependency, checked again when the release is cut, and holds one back only after it fails vakr's tests (ADR-036). This release bundles CPython 3.15.2 and updates FastAPI, Starlette, and pyasn1, the SNMP codec. It changes no database schema and adds no options. Upgrades are supported from 0.4.0 on, because no earlier release is in operation (owner, 2026-10-27), so this release carries no manual-update marker and the update timer installs it.

### Changed

- Developers: vakr now ships on the newest release of every tool and dependency, however recently it was published. An update is held back only when it fails vakr's tests, as a recorded exception that is retested when the dependency publishes again (ADR-036, TOOLS-002, TOOLS-004; [keeping tools and dependencies current](docs/development/updates.md)).
  - `tools/release` runs `check-updates --gate` in its preflight, when the release starts. It stops for any available update, any item it could not check, and any stale exception (TOOLS-005).
  - `make updates` shows a held item as `held` and an item vakr does not pin as `reference`. It also checks the uv, Go, and nfpm pins of the package builder image, and compares `@types/node` with the newest release on the Node 24 line.
  - Holds are recorded in `tools/check-updates/update-exceptions.toml` and listed under "Held dependencies" in each release's notes. They replace "Deferred updates".
  - Nothing waits for a release to age. Dependabot's default 3-day cooldown is off for every ecosystem, `web/.npmrc` sets `min-release-age=0`, and the lockfile check overrides any local uv `exclude-newer`.
- Toolchain and dependencies (TOOLS-001, TOOLS-002), every update `make updates` listed on 2026-10-27:
  - The package bundles CPython 3.15.2 (3.15.1 before), through uv 0.13.6 in the builder image (0.13.5 before).
  - Runtime: FastAPI 0.162.0 (0.159.1), Starlette 1.10.0 (1.9.0), pyasn1 0.6.5 (0.6.4) and platformdirs 4.13.6 (4.13.5), both published the day of this release, and certifi 2026.10.23. FastAPI's two breaking changes, in 0.160.0 and 0.162.0, concern parameter models and form fields, which vakr does not use.
  - Web UI: Svelte 5.57.3 and TanStack Svelte Query 6.3.6.
  - Developers: ruff 0.17.3, ty 0.0.92, coverage 7.17.0, hypothesis 6.170.12, eslint-plugin-jsdoc 65.4.2, svelte-eslint-parser 1.10.0, typescript-eslint 8.72.1, and `astral-sh/setup-uv` 10.3.2 in CI.

- Upgrades are supported from 0.4.0 on (`OLDEST_UPGRADE_SOURCE`, Appendix E.14 decision 5). Every supported release reads newer additive telemetry schemas, so a release that adds only additive telemetry migrations no longer needs the manual-update marker; one with a breaking migration still does.

### Fixed

- `vakr setup`'s closing checklist no longer says the web UI arrives in a later release. It now ends with starting `vakr-api` behind the HTTPS reverse proxy and signing in, then giving read-only people and monitoring agents the `vakr-view` group, as the [install guide](docs/operations/install.md#first-run) does.

### Held dependencies (TOOLS-004)

- **TypeScript 7.0.2** (staying on 6.0.3): typescript-eslint refuses TypeScript 7.0 (8.72.0 and 8.72.1 both), and SvelteKit 3.1.0 crashes loading `tsconfig.json`. Lint, svelte-check, the unit tests, the API-types check, and the build all fail; all pass on 6.0.3. Tested 2026-10-27; revisited with the next TypeScript release.

### Notes for operators

- **A new installation** needs nothing beyond the [install guide](docs/operations/install.md).
- **From 0.4.0** nothing is needed: the update timer installs 0.5.0, or run `sudo vakr self-update`. `sudo vakr self-update --rollback` returns to 0.4.0, which reads the same schemas.
- **Releases before 0.4.0 are no longer upgrade sources**, because none is in operation. A 0.3.x node's timer would still stop at 0.4.0's marker, but a 0.2.x node's timer would install 0.5.0 straight over two telemetry migrations. Reinstall such a node from the install guide instead.

## 0.4.0 - 2026-10-22

Milestone M5, two positions: node roles, `vkr_` replication tokens, metadata pull to a read-only peer, and observation replication in both directions. Each position keeps working through a partition and backfills afterwards; the composite status reads "Reachable from <position> only". Also read-compatible telemetry versioning, and a security fix: self-update's archives, record, and lock now live where only root may write. **This release is marked `manual-update-required`** for nodes updating from 0.3.x (telemetry schema 3); a new installation is unaffected.

### Security

- Self-update keeps its pre-update archives and its `update-state.json` in `/var/lib/vakr-update`, which only root may write (UPD-005, Appendix E.14 decision 6). Rollback runs as root and restores the newest archive into `/etc/vakr` and the metadata database. Before, the archives lived in `/var/lib/vakr/backups`, which the `vakr` service account owns, so a compromised service could have planted one for root to restore. The update lock moved there too, from `/run/vakr`, and is opened without following links. Rollback now restores only a regular archive that root owns, with one link, that nobody else may write. Self-update refuses its directories if anyone else owns or may write them. The first `sudo vakr self-update` of this release moves an older release's root-owned archives and record across (`update.storage.adopted`). `apt purge` removes `/var/lib/vakr-update`, whose archives copy the configuration and its secrets.

### Added

- Two positions, part A (MPOS-001 to MPOS-003, MPOS-006; ADR-024, [two positions](docs/operations/two-positions.md)): `[node] role = "peer"` makes a node copy its primary's metadata (inventory, interface identity and annotations, sites, tags, groups, policies, accounts, dashboards, identity reviews) every 60 s. The copy is row for row, UUIDs and versions kept, and is applied only when it matches the primary's digest. A peer refuses local metadata changes, naming the primary (409 `peer_read_only`, and the CLI likewise), keeps polling with its last-known copy while the primary is away, and reports a failing pull as node health only.
- Replication tokens (`vkr_`): `sudo vakr replication token create|list|revoke` issues the token a partner node installs in `/etc/vakr/secrets/replication.token`. It grants only `/api/v1/replication/*` (`GET /api/v1/replication/metadata/state` and `/metadata`); `vakr replication status` shows a node's role and its newest pull.
- `vakr setup --role --position --primary-url` writes the `[node]` table, asking at a first-install terminal; on a peer it creates no admin. `GET /api/v1/version` and the web and TUI headers show the node's position and role.
- A `replication` check in `vakr check health` and `vakr doctor` (warning after 5 min, critical after 30 min without a successful pull, E.13), and the metrics `vakr_replication_metadata_age_seconds` and `vakr_replication_metadata_pulls_total`.
- Metadata schema 6 adds the `replication_token` table. The package runs `vakr db migrate`, which takes a backup first.
- Two positions, part B: observation replication (MPOS-004 to MPOS-008; [two positions](docs/operations/two-positions.md#observation-replication)). Each node pulls the other position's observations every 30 s from `GET /api/v1/replication/observations` with its replication token, oldest first and idempotently, so every device view shows both positions' verdicts side by side and the composite ("Reachable from east only"). After a partition the backlog drains without starving local ingestion. A primary pulls from the new option `node.peer_url`; a peer from `node.primary_url`.
- The `replication` check, the self-metrics (`vakr_replication_observation_lag_seconds`, `vakr_replication_backlog`, `vakr_replication_observations_total`, `vakr_replication_gaps_total`), the collector health page, and the TUI header show each partner's lag, backlog, last exchange, and gaps (27.5 item 7). Warning at 5 min, critical at 30 min (E.13). A replication failure is node health, never a device state.
- Telemetry schema 3 (`0003_replication.sql`) adds the replication log and cursors: two new tables, about 40 bytes per locally ingested observation, purged with native retention.
- Read-compatible telemetry versioning (Appendix E.14; [writing migrations](docs/development/migrations.md)). Telemetry migrations from 0003 on declare `compat: additive` or `compat: breaking`, and each telemetry database records a reader floor in a new `schema_compat` table.
  - This release and later ones keep running on a newer telemetry schema whose later changes were all additive. A self-update rollback, which never restores `telemetry.db`, therefore no longer strands them.
  - `vakr db status` then reports "schema M (readable by schema F and later)" and exits 0, and `vakr db migrate` does nothing.
  - A newer schema with a breaking change is still refused, naming the migration.
  - Older releases refuse every newer telemetry schema, so releases that add telemetry migrations still carry `manual-update-required`.

### Changed

- Developers: `make mpos-test` runs the two-position system tests, MPOS-T01 to MPOS-T04 ([two-position tests](docs/development/two-position-tests.md)). It starts two systemd nodes, `east` and `west`, and a simulated fleet in privileged containers, and applies nftables partitions and netem delays. `MPOS_QUICK=1` takes about 7 minutes with a prebuilt package; the full run holds a 30-minute partition. The checks that need node roles or observation replication (M5 parts A and B) print SKIP until `MPOS_FEATURES` names them. A manual GitHub workflow, **Two positions**, runs quick mode. Not part of `make check`.
- Developers: `make github-update-test` reads the update timer's log from a whole-journal cursor, because the unit has none before its first run. For a `manual-update-required` release, it accepts either answer from the rolled-back release: it may refuse the new telemetry schema, or, when FROM already has that migration, accept it. Verified against the published releases, 0.2.1 → 0.3.1 and 0.3.0 → 0.3.1.
- Toolchain and dependencies (TOOLS-001): the package bundles CPython 3.15.1 (from `.python-version`'s 3.15 line; 3.15.0 before), the builder image has uv 0.13.5, `pycparser` is 3.12, and the developer dependency `eslint-plugin-jsdoc` is 65.4.1.

### Deferred updates (TOOLS-001)

- **Node.js 26 and `@types/node` 26:** Node 26 is a Current release, not LTS. The web build stays on 24 LTS (E.31 decision 9), and Dependabot ignores `@types/node` majors.
- **TypeScript 7:** SvelteKit 3.1, svelte-check, and typescript-eslint support only 6.x (E.31 decision 9).

### Notes for operators

- **A new installation** needs nothing beyond the [install guide](docs/operations/install.md). A single node needs no `[node]` settings. For two positions, see [two positions](docs/operations/two-positions.md).

- **This release is marked `manual-update-required`.** It migrates the telemetry schema to 3 (`0003_replication.sql`), and self-update never copies `telemetry.db`, so an automatic rollback could not restore it. The update timer skips this release; install it with `sudo vakr self-update`. The migration adds two empty tables in milliseconds.
- To go back to 0.3.x after updating, stop `vakr-collector` and `vakr-api`, then drop the two tables and the journal row of version 3, as [the database guide](docs/operations/database.md#going-back-before-telemetry-schema-3) shows, before installing the older release. Observations a partner replicated stay, as ordinary rows of its collector.
- **After updating, self-update keeps its files in `/var/lib/vakr-update`.** The first `sudo vakr self-update` of this release moves an older release's pre-update archives and update record from `/var/lib/vakr` and logs `update.storage.adopted`. A file there that root doesn't own, or that others may change, is left behind and never restored.

## 0.3.1 - 2026-10-22

Fixes the update from 0.2.x. `vakr self-update` to 0.3.0 failed, and rolled itself back to 0.2.x, on a node where an earlier self-update had created `/var/lib/vakr/backups`. Install 0.3.1 instead of 0.3.0.

**This release is marked `manual-update-required`**, as 0.3.0 is: it carries 0.3.0's telemetry migration. The update timer skips it, so install it with `sudo vakr self-update`.

### Fixed

- The package now gives `/var/lib/vakr/backups` to `vakr` (mode 0700) before it migrates the databases (PKG-003). A 0.2.x self-update had created the directory as root, so the migration, which runs as `vakr`, could not write its pre-migration backup. dpkg then failed, and self-update rolled back to 0.2.x, as designed. Installing the 0.3.0 package with `apt` on a node that had never self-updated was not affected.
- Developers: `make github-update-test` understands a release marked `manual-update-required`. After the rollback it expects the older release to refuse the new telemetry schema, and it expects the timer to skip the release; it then updates as an operator would.

### Notes for operators updating from 0.2.x or 0.3.0

- Everything in the 0.3.0 notes below applies: the migrations, enabling `vakr-api`, the first admin, and `vakr-view`.
- If your self-update to 0.3.0 failed: 0.2.x is running and its data is as it was. Run `sudo vakr self-update`; the failed version recorded is 0.3.0, so nothing holds back 0.3.1.
- On 0.3.0, the timer also skips 0.3.1 because of the marker. 0.3.1 changes no schema or option, so `sudo vakr self-update` is all it takes.

## 0.3.0 - 2026-10-21

Milestone M4, viewing: the web UI and JSON API v1 (`vakr-api`, shipped disabled), local accounts, `vakr status` and `vakr tui` over the API socket, kiosk displays with dashboard rotation, custom dashboards, manual rediscovery, rollups and retention, the ranking projection, and self-metrics. **This release is marked `manual-update-required`**: the update timer skips it, so install it with `sudo vakr self-update` (see the notes at the end).

### Added

- Custom dashboards (UI-004, DASH-006, DASH-007, Appendix E.31 #17; [dashboards](docs/operations/dashboards.md)). Admins build them under **Dashboards** → **New dashboard**, or pin an interface from its page with **Pin to dashboard**; everyone signed in views them, and kiosk displays show them at `/kiosk/dashboard/<uuid>` or as `dashboard:<uuid>` in a rotation.
  - Widgets: pinned interfaces across devices (up to 50, in your order, with optional graph cards), interfaces or devices selected by site, tag, group, provider, WAN, or interest, and top utilization or errors within a selection.
  - A dashboard refresh is one bounded request (`GET /api/v1/dashboards/{uuid}/view`); every bound is in E.31.
  - A pinned interface that is retired or gone keeps its place with a note. Saving over someone else's change is refused (409) with an offer to reload. Creates, changes, and deletes are audited.
  - API: `GET`/`POST /api/v1/dashboards`, `GET`/`PATCH`/`DELETE /api/v1/dashboards/{uuid}`, and `/view`.
  - Metadata schema 5 adds the `dashboard` table (`0005_dashboards`; the package runs `vakr db migrate`, which takes a backup first). Not yet: dashboards from `/etc/vakr/dashboards.toml`, public publication, and a `vakr dashboard` command.
- Manual rediscovery (DISC-003, DISC-010, API-011, Appendix E.32): **Rediscover now** on a device's page (admins), `POST /api/v1/devices/{uuid}/rediscovery`, and `sudo vakr inventory rediscover DEVICE [--wait]` queue an operation and answer at once; `vakr-collector` picks it up within about 5 seconds, runs the same discovery as a scheduled one, and records the counts (interfaces, new, renumbered, missing, identity reviews opened) or the reason. `GET /api/v1/operations[/{uuid}]` and `vakr inventory operations` follow them, the overview's rediscovery events list them, and every request is audited. One active operation per device, 1,000 in all; unclaimed requests expire after 1 hour; finished ones are kept 30 days.
- Metadata schema 4 adds the `operation` table. The package runs `vakr db migrate`, which takes a backup first.
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
- Kiosk displays (UI-013, [kiosk displays](docs/operations/kiosk.md)): `/kiosk/overview`, `/kiosk/high-priority`, `/kiosk/wan`, and `/kiosk/rankings` show the built-in dashboards on a wall screen without navigation, in larger type, with names as plain text and a "Data as of … · refreshes every N s" line. `?refresh=` (15 s to 1 h) and `?window=` adjust them. Refreshes update rows in place; a failed refresh keeps the rows marked stale; an expired or revoked token fills the screen with what to do; a mistyped address is a full-screen error.
- Dashboard rotation for kiosk displays (owner decision 2026-10-21): `/kiosk/rotate?pages=overview,wan,rankings&every=60` shows 2 to 8 dashboards in turn, 15 s to 1 h each. Only the dashboard on screen reads data; the next one reads ahead 5 s before the switch, so it appears complete. The header shows "2 of 3 · next: WAN circuits in 40 s"; rotation pauses while the tab is hidden or with **Pause rotation**.
- `vakr kiosk create LABEL [--lifetime DURATION]`, `vakr kiosk list [--json]`, and `vakr kiosk revoke ID|LABEL` manage kiosk tokens; `create` prints the token once. The Admin page does the same in the browser, with a copy button.
- `POST /api/v1/auth/kiosk` exchanges a kiosk token once for an `HttpOnly`, `SameSite=Strict` kiosk cookie (`__Host-vakr_kiosk` over HTTPS), and `POST /api/v1/auth/kiosk/forget` clears it; a display is set up by pasting its token at `/kiosk/setup`. `GET`, `POST`, and `DELETE /api/v1/kiosk-tokens` (admin) list, create, and revoke tokens. A session cookie in the same browser wins over the kiosk cookie. Setups are audited as `kiosk.setup` (E.30 #17).
- A new `vakr-view` group (Appendix E.12, E.30 #16), created by the package: its members get read-only operator access on the local API socket, so they can use `vakr status`, `vakr tui`, and the API there, and nothing else. Unlike the service group `vakr`, it cannot read the SNMP credentials file or `metadata.db`. Give it to people and monitoring agents (such as NRPE's `nagios`) that only look. The socket's refusal message now points to `vakr-view`, and an account that cannot read the databases is told so, instead of "no data yet", when `vakr-api` is not running or `--local` is given.
- New runtime dependencies `fastapi` (MIT) and `uvicorn` (BSD-3-Clause), with `starlette`, `pydantic`, `pydantic-core`, `h11`, `anyio`, `idna`, `click`, `annotated-types`, `typing-inspection`, `annotated-doc`, and `opentelemetry-api`; licenses are listed in the package's copyright file.
- Self-metrics (OBS-001, OBS-004, OBS-005, OBS-007; Appendix E.33): `GET /api/v1/metrics` answers vakr's own figures in Prometheus text format: poll latency percentiles, step outcomes and durations, ICMP probes, queues, spool fill and replay lag, ingestion, telemetry upkeep, CPU, memory, event-loop lag, API requests and durations by route group, connections, inventory counts, database sizes and schema versions, and rollup and ranking-projection lag. It needs the operator role: scrape it over the local socket as a `vakr-view` member, or through the HTTPS proxy with a kiosk token as the bearer credential ([self-metrics](docs/operations/metrics.md)). A figure that cannot be measured is absent, never 0, and `vakr_collector_metrics_up` / `vakr_state_metrics_up` say why. No label names a device, interface, user, address, or path.
- The collector writes its metrics snapshot to `/run/vakr/collector-metrics.json` every 15 s (mode 0640) and removes it at a clean stop; `vakr-collector.service` may now write `/run/vakr`.

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
- Self-update's timer also skips a release when an older release it would jump over carries `manual-update-required`, because installing the newer one applies the marked release's changes too (UPD-005). Run `sudo vakr self-update` by hand once, and the timer takes over again. This protects nodes from 0.3.0 on; 0.2.x checks only the newest release's notes.
- Toolchain and dependencies (TOOLS-001): uv 0.13.4 (package builder image), Node.js 24.22.1 (`web/.node-version`, the builder image, and the release host), `fastapi` 0.159.1, `httpx2` and `httpcore2` 2.16.0 (tests), `ty` 0.0.90, and the `astral-sh/setup-uv` action v10.3.1.
- Developers: the release preflight disregards check runs named in `tools/release/release.toml`'s new `ci_ignore_checks`, set to Dependabot's update jobs, which run on any commit that changes `.github/dependabot.yml` and test nothing being released.

### Deferred updates (TOOLS-001)

- **Python 3.15.1:** uv 0.13.4 offers no python-build-standalone download for it yet, so the package still bundles CPython 3.15.0. It follows at the next release.
- **uv 0.13.5:** listed on GitHub, but no Linux build was published at release time.
- **Node.js 26:** a Current release, not LTS. The web build stays on the 24 LTS line (E.31 decision 9).
- **`@types/node` 26:** it describes Node 26 APIs, and the build runs Node 24. Dependabot now ignores its major versions; its pull request #1 can be closed.
- **TypeScript 7:** SvelteKit 3.1 (peer `^6.0.0`), svelte-check, and typescript-eslint support only 6.x (E.31 decision 9).

### Notes for operators updating from 0.2.x

- **This release is marked `manual-update-required`.** It migrates the telemetry schema (schema 2, `0002_ranking_projection.sql`: the ranking projection), and self-update never copies `telemetry.db`, so an automatic rollback could not restore it. The update timer skips this release; install it with `sudo vakr self-update`. The package's `vakr db migrate` applies schema 2 in milliseconds; the collector then builds the projection within its first minute.
- To go back to 0.2.x after updating, stop `vakr-collector` and `vakr-api` and remove schema 2 before installing the older release, as [the rollups guide](docs/operations/rollups.md#going-back-to-a-release-without-the-projection) shows. The projection holds only derived data, so nothing is lost.
- The package migrates the metadata database from schema 2 to schema 5 (accounts and sessions, operations, dashboards) after taking a backup, and the telemetry database to schema 2. Nothing needs to be re-entered: every new `[auth]` and `[api]` option has a default. `vakr config new-options --since 0.2.1 --describe` lists them.
- The web UI and API need `vakr-api`, which the package installs but does not enable: `sudo systemctl enable --now vakr-api.service vakr.target`. Put the HTTPS reverse proxy of [web access](docs/operations/web-access.md) in front of it, then create the first admin with `sudo vakr setup`.
- `vakr status` and `vakr tui` use `vakr-api` when it runs, and read the databases directly when it does not. Give people and monitoring agents (such as NRPE's `nagios`) that only look the new `vakr-view` group rather than `vakr`, which can also read the SNMP credentials: `sudo usermod -aG vakr-view NAME`.

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
