# vakr — Network Performance Monitoring Platform
## Consolidated Software Requirements, Architecture, UX, Test, and AI Development Blueprint

**Product name:** vakr (Old Norse *vakr*: wakeful, alert, vigilant)  
**Package, CLI, service, and path name:** `vakr`  
**Document version:** 1.1-draft  
**Document date:** 2026-10-19  
**Status:** Implementation baseline  
**MVP design point:** 100 devices / ~1,600 active interfaces / one or two collection positions (section 27)  
**Primary enterprise design point:** 4,000 devices / 100,000 monitored interfaces / 60-second default interval

### Revision history

| Version | Date | Summary |
|---|---|---|
| 1.0-draft | 2026-09-20 | Consolidated baseline under the working name `netperfmon`. |
| 1.1-draft | 2026-10-19 | Renamed to `vakr`; owner decisions recorded (license, name, MVP scope, positions, retention, packaging); added the MVP definition (section 27), delivery and lifecycle tooling (section 28), portable scaffolding rules (section 29), and default values and decision tables (Appendix E); corrected inconsistencies listed in section 22.1. |

---

## Table of contents

1. [Purpose and instructions](#1-purpose-and-instructions-to-the-implementation-agent)
2. [Mission, boundaries, and outcomes](#2-mission-product-boundaries-and-outcomes)
3. [Stakeholders and roles](#3-stakeholders-roles-and-authorization-model)
4. [Scale and invariants](#4-scale-service-levels-and-quality-invariants)
5. [Deployment profiles and database abstraction](#5-deployment-profiles-and-database-abstraction)
6. [System architecture](#6-system-architecture)
7. [Canonical domain model](#7-canonical-domain-model)
8. [Consolidated functional requirements](#8-consolidated-functional-requirements)
9. [Collection behavior and algorithms](#9-collection-behavior-and-algorithms)
10. [Telemetry schemas and retention](#10-telemetry-schemas-and-retention-behavior)
11. [Configuration model](#11-configuration-model)
12. [Web UX specification](#12-web-information-architecture-and-ux-specification)
13. [Terminal UI specification](#13-terminal-ui-specification)
14. [API surface](#14-api-surface)
15. [Security architecture](#15-security-architecture-and-threat-controls)
16. [Operations, packaging, backup, and recovery](#16-operations-packaging-backup-and-recovery)
17. [Coding and teaching standards](#17-coding-maintainability-and-teaching-standards)
18. [Documentation requirements](#18-documentation-requirements)
19. [Required test plan](#19-required-test-plan-and-progress-gates)
20. [Vertical implementation roadmap](#20-vertical-implementation-roadmap)
21. [Component completion checklists](#21-component-completion-checklists)
22. [Overlooked or ambiguous areas](#22-previously-overlooked-or-ambiguous-areas-and-their-resolution)
23. [Simulator and performance harness](#23-network-simulator-and-performance-harness)
24. [Future Go component rules](#24-rules-for-a-future-go-component)
25. [AI implementation protocol](#25-ai-implementation-protocol)
26. [Project completion criteria](#26-project-completion-criteria)
27. [MVP definition](#27-mvp-definition)
28. [Delivery, update, and lifecycle tooling](#28-delivery-update-and-lifecycle-tooling)
29. [Portable scaffolding tools](#29-portable-scaffolding-tools)
- [Appendix E. Default values and decision tables](#appendix-e-default-values-and-decision-tables)

## 1. Purpose and instructions to the implementation agent

This document is the single consolidated implementation blueprint for an open-source, Linux-native network performance and reachability monitoring platform. It combines the prior retained design summary, the Software System Requirements and Architecture Specification (SSRAS), the AI development instructions, the additional database, terminal UI, web dashboard, and teaching requirements, and the engineering decisions needed to make the project buildable with minimal additional direction.

Source materials consolidated here:

- `NETWORK_PERFORMANCE_MONITORING_SSRAS.md` — product behavior and architecture baseline.
- `AI_DEVELOPMENT_INSTRUCTIONS.md` — implementation-agent workflow, coding, quality, security, and documentation rules.
- `AI-MRTG-SMOKE-SUMMARY.md` — retained design rationale and detailed operational semantics.
- The accompanying owner prompt — database abstraction, SQLite/TimescaleDB options, TUI, public/operations dashboards, sticky interfaces, and teaching-oriented source documentation.

An AI coding agent shall treat this document as authoritative unless a later approved Architecture Decision Record (ADR) explicitly changes it.

The implementation agent shall:

1. Build the product in the vertical phases defined in this document.
2. Cite applicable requirement IDs in issues, implementation plans, pull requests, tests, and documentation changes.
3. Implement a runnable, tested, documented vertical slice before starting a dependent slice.
4. Update the requirements traceability matrix in the same change that implements or changes a requirement.
5. Never silently resolve a contradiction. Record it as an ADR or issue, state the chosen behavior, and continue unaffected work.
6. Never weaken a mandatory requirement because it is difficult.
7. Prefer the smallest maintainable design that meets the stated behavior, performance, security, and teaching goals.
8. Keep network collection, domain logic, persistence, API, web, and terminal presentation separated by explicit interfaces.
9. Preserve existing behavior when modifying implemented code unless fixing a confirmed bug or applying an approved requirement/ADR change.
10. Report incomplete work honestly. A phase is not complete merely because its happy path works.

### 1.1 Authority and precedence

When requirements appear to conflict, use this order:

1. This consolidated blueprint and any later approved ADR.
2. Explicit requirement statements with IDs.
3. Security, durability, identity, and data-quality invariants.
4. Detailed architecture and UX sections.
5. Examples and non-normative recommendations.

Words have the following meanings:

- **Shall / must:** mandatory.
- **Should:** expected unless an ADR records a justified exception.
- **May:** optional.
- **Default:** initial behavior that administrators may override where the requirement permits.

### 1.2 Decisions adopted to remove ambiguity

The following decisions are part of this baseline:

| Topic | Adopted decision |
|---|---|
| Product name | `vakr` is the product name and is used for the Python package, CLI, service user/group, paths, and unit names (owner decision 2026-10-19; closes ADR-019). |
| Initial language | Python, latest stable release (3.15 at this revision), with `asyncio`. Go, latest stable release (1.27 at this revision), may be used for a component where measured performance requires it, under the rules in section 24. At the MVP scale Python is expected to suffice. |
| Toolchain currency | Use the latest stable Python, Go, `uv`, `ruff`, and `ty`. Check tools and dependencies for updates at least once per release (TOOLS-001). |
| Version source | The project version lives only in the repository-root `VERSION` file. Every other consumer derives it (VER-001). |
| Compatibility policy | Breaking changes to configuration, API, CLI, and schemas are accepted until the project moves to third-party/enterprise readiness after MVP. Automated config upgrade and data migrations still apply so operators never re-enter settings by hand (ADR-031, CFGM-002). |
| Packaging | MVP ships as a `.deb` for Ubuntu 26.04 LTS. RHEL-family (AlmaLinux/Rocky/RHEL) `.rpm` packaging comes after MVP (PKG-001, PKG-006). |
| Documentation publishing | Every Markdown file is published as HTML to GitHub Pages using the portable `tools/md2html` converter (DOCPUB-001). |
| Web stack | FastAPI/Starlette, server-rendered Jinja templates, HTMX for partial updates, minimal locally served JavaScript, and Apache ECharts for time-series visualization. A framework change requires an ADR. |
| TUI stack | Textual, consuming the same application/query services as the API. |
| Metadata persistence | SQLite for Lite, Compact (MVP), and small/standalone use; PostgreSQL for Standard and Enterprise use. |
| Operational telemetry | SQLite for the Lite (lab/CI) and Compact (MVP production) profiles, TimescaleDB for the optional Standard profile, and ClickHouse for the Enterprise profile. |
| Enterprise authority | PostgreSQL owns metadata/control. ClickHouse owns operational observations, current-state projections, collector health, and rollups. |
| “TimeseriesDB” interpretation | The PostgreSQL add-on is TimescaleDB unless an ADR selects another PostgreSQL-compatible time-series extension. |
| Open-source license | Apache License 2.0 (owner decision 2026-10-19; closes ADR-018). |
| UI language | English only for v1; all user-facing strings shall be structured so later localization is possible. |
| Deployment style | Native Ubuntu 26.04 LTS with systemd, installed from the project `.deb`, is the production baseline. Containers may support development and CI but are not the only supported production method. |
| ICMP privilege | ICMP uses Linux unprivileged ICMP datagram sockets (`net.ipv4.ping_group_range`), so no vakr process needs `CAP_NET_RAW` by default. A collector-only raw-socket fallback is documented for hosts that cannot enable unprivileged ICMP (ADR-023). |
| Alerting integration | NetOps alerting stays in CheckMK/Nagios. vakr provides a Nagios-plugin-format self-health check (OPS-001) and does not alert on target state in the MVP. |
| Multi-tenancy | v1 is single-organization. Multi-tenancy is out of scope. |
| Alerting | A generalized alert/escalation engine is out of scope. External add-ons consume documented API/database state. |
| Public dashboards | Anonymous dashboards are explicit opt-in resources with a field allowlist and no navigation into authenticated inventory pages. |
| ICMP-gated SNMP | The source behavior remains the default. An opt-in `snmp_fallback` strategy is added for devices that filter ICMP; this does not change the default. |
| Interface pattern matching | Shell-style globs are the default. Regex is supported only with an explicit `re:` prefix, bounded input size, compile-time validation, and match time limits. |
| Source of configuration truth | Service/runtime settings and credentials are file-managed. Inventory, policies, sites, interface annotations, and dashboards may be file-managed or API-managed, with explicit ownership to prevent two writers. |
| File length | Production source files should remain at or below 300 physical lines, including docstrings and comments. Any exception requires a recorded justification and a decomposition review. |

### 1.3 Owner decisions that remain before public production release

These decisions do not block initial implementation because defaults are specified above, but the project owner shall confirm them before declaring v1 production-ready:

- ~~Final product name and public package/CLI naming.~~ Resolved 2026-10-19: `vakr`.
- ~~Final open-source license.~~ Resolved 2026-10-19: Apache-2.0.
- Whether anonymous dashboards may be internet-facing or are intended only for trusted internal networks. (Public dashboards are deferred past MVP.)
- Supported reference scale for the TimescaleDB profile. The Compact (SQLite) profile reference scale is the MVP workload in section 27.2; only the PostgreSQL + ClickHouse profile has the formal enterprise scale target.
- Whether the opt-in `snmp_fallback` reachability strategy is accepted as a core feature or retained as an extension. (Included as opt-in in the MVP.)
- Whether static PNG/SVG graph rendering is required for legacy integrations; interactive web graphs and data export are required, static image generation is not.
- Whether high availability for the API/web tier and database replication are v1 requirements; backup/recovery is required, active-active application HA is not. (The MVP's two-position design in section 27.5 gives collection resilience across partitions without database clustering.)

### 1.4 Owner decisions recorded 2026-10-19

| Topic | Decision |
|---|---|
| MVP scale | 100 Cisco IOS XE devices, split 80% routers / 20% switches; about 10 active interfaces per router and 40 per switch (about 1,600 active interfaces). |
| Interface classes | Each device has (a) interfaces of interest — uplinks and primary-path transit, viewed often — and (b) secondary interfaces, viewed occasionally for one-off questions (META-011). |
| SNMP | SNMPv2c is required for MVP. SNMPv3 stays in the design and is implemented after MVP. |
| Collection positions | One position is required; two (east and west, 55–70 ms RTT apart) are preferred, to show that collection continues for every reachable part of the network during a partition (section 27.5). |
| Retention | 30 days native plus 15-minute rollups through day 400, in the MVP. |
| Alerting | Not in MVP. NetOps alerts with CheckMK and Nagios. |
| Migration from MRTG/SmokePing | No history import. NetOps adds hosts manually and retires MRTG/SmokePing after a parallel run shows vakr is sufficient. |
| Repository | GitHub, public, Apache-2.0, with HTML documentation on GitHub Pages. |
| Delivery | Development happens on a separate host from production. Updates must be easy: an operator-invoked `vakr self-update` early in the roadmap, an optional systemd timer for automatic updates, and automatic merging of new config options (section 28). |
| Field data | When development needs data from production, first build a tool that collects it into one bundle, which the operator can encrypt and relay over HTTPS or scp (section 28.5). |
| Reuse | Scaffolding tools that sit outside the product core are built to be portable to other projects (section 29). |

---

## 2. Mission, product boundaries, and outcomes

### 2.1 Mission

Build a focused, open-source network performance and reachability monitoring platform that replaces the operational roles of MRTG and SmokePing while adding:

- automatic SNMP discovery;
- persistent device and interface identity;
- ICMP latency, jitter, packet-loss, and response-time history;
- interface traffic, packet, error, state, and capacity monitoring;
- policy-driven collection;
- modern web dashboards and graphs;
- a fast terminal UI for technical staff;
- durable, backend-abstracted storage;
- collector health and capacity diagnostics;
- a multi-poller-ready observation model; and
- a readable Python codebase suitable for teaching network monitoring and software engineering practices.

The product shall remain an operations-focused performance monitor, not a general-purpose Network Management System.

### 2.2 Primary operator outcomes

A successful implementation lets a network operator:

1. Add or import devices and credential references.
2. Discover system and interface inventory automatically.
3. See which devices are healthy, impaired, unreachable, administratively offline, or unknown.
4. Open a device and immediately see basic identity, uptime, reachability, SNMP status, comments, and current interface state.
5. Open an interface and see inbound/outbound bandwidth, utilization, packets, errors, state changes, and historical graphs without artificial reset spikes.
6. See SmokePing-like latency distribution, jitter, and packet loss over time.
7. Find high-priority and WAN circuit interfaces quickly.
8. Rank circuits by utilization and errors over a selected time window.
9. Browse by site, provider/ISP, WAN role, tag, or group.
10. Use anonymous public dashboards that expose only explicitly approved information.
11. Use a terminal UI on the monitoring server when a browser is inconvenient.
12. Distinguish a device outage from SNMP failure, collector overload, collector outage, data staleness, and database failure.
13. Preserve telemetry through a time-series database outage while local durable spool capacity remains available.
14. Inspect documented APIs and read-only database views for future add-ons.

### 2.3 In-scope capabilities

- ICMPv4 and ICMPv6 reachability.
- Five-probe reachability groups with individual RTTs, packet loss, summary latency, and jitter.
- SNMPv2c and SNMPv3 discovery and polling.
- Standards-based system, IF-MIB, and IP-MIB collection.
- Interface state, octet, packet, error, capacity, addressing, and identity monitoring.
- Persistent interface identity across `ifIndex` changes.
- Interface lifecycle and monitoring policy.
- Sites, tags, groups, high-priority annotations, WAN/circuit annotations, provider metadata, and sticky display ordering.
- Scheduling, bounded concurrency, capacity diagnostics, and opt-in adaptive polling.
- SQLite, PostgreSQL/TimescaleDB, and ClickHouse storage profiles behind repository interfaces.
- Durable telemetry spooling and idempotent replay.
- Web, API, TUI, CLI, health, and systemd integration.
- Retention and rollups.
- Built-in local authentication with admin and operator/user roles.
- Anonymous public dashboards with explicit publication controls.
- Multi-poller schema groundwork.
- Open-source developer, operator, and teaching documentation.

### 2.4 Explicit non-goals for core v1

The core shall not become responsible for:

- NetFlow, IPFIX, or sFlow;
- syslog collection or SIEM functions;
- SNMP trap management;
- configuration backup, configuration compliance, or configuration deployment;
- full network topology mapping;
- ticketing or ITSM;
- generic application, server, process, or synthetic transaction monitoring;
- vulnerability scanning;
- automated remediation;
- a general-purpose CMDB;
- a generalized alert, notification, escalation, or on-call engine;
- packet capture or deep packet inspection; or
- multi-tenant SaaS isolation.

The product may expose state and health derived directly from its own observations. External alerting processes may consume the API or supported read-only views.

### 2.5 Supported platforms

- Ubuntu Linux 26.04 LTS (amd64), installed from the project `.deb`.
- The latest stable Python release at build time (3.15 at this revision). The `.deb` bundles its own relocatable interpreter, so the host's system Python version does not matter (PKG-002).
- Modern Firefox, Chromium/Chrome, and Edge releases current at the time of a supported release.
- Accurate host time through NTP/chrony.
- After MVP: AlmaLinux, Rocky Linux, and RHEL (current major) via `.rpm` (PKG-006).

Other Linux distributions may work but are not release blockers unless explicitly added to the support matrix.

---

## 3. Stakeholders, roles, and authorization model

### 3.1 Human roles

| Role | Capabilities |
|---|---|
| Admin | Manage local users, API-managed devices and metadata, sites, tags/groups, policies, dashboard definitions, public publication, interface annotations, credential references, manual rediscovery, migrations/maintenance actions exposed by the product, and audit review. |
| Operator/User | Read current and historical monitoring state, dashboards, graphs, inventory, collector health, and approved data exports. No core configuration changes in v1. |
| Anonymous | Read only dashboards explicitly published as public and only through the public field/query allowlist. |

Authorization shall be enforced in application services and API handlers, not merely by hiding UI controls.

### 3.2 Machine roles

- **Collector:** monitoring host or logical observation point.
- **Worker:** polling process within a collector.
- **Ingest writer:** process or component that owns telemetry backend writes.
- **Metadata writer:** application role permitted to modify metadata/control data.
- **Read-only application role:** web/API query role.
- **Integration reader:** separately credentialed read-only API/database role for add-ons.
- **Migration role:** privileged role used only for controlled schema migrations.

### 3.3 Data ownership boundaries

- Metadata repositories own devices, interfaces, sites, tags, groups, policies, dashboards, users, sessions, credential references, audit records, and identity history.
- Telemetry repositories own immutable operational observations, latest/current projections, rollups, collection coverage, and collector health history.
- Credential providers own secret material. Metadata contains only references and non-secret descriptors.
- Presentation layers never issue SQL and never resolve network credentials.

---

## 4. Scale, service levels, and quality invariants

### 4.1 Enterprise design point

The formal enterprise acceptance workload is:

- 4,000 configured devices;
- 100,000 monitored interfaces;
- 60-second default interface polling;
- five ICMP probes per normal reachability cycle;
- 24-hour default rediscovery;
- mixed 60-, 120-, and 300-second device intervals in the same worker pool.

### 4.2 Required performance thresholds

At the reference workload:

- At least 99% of healthy-device polls begin within approximately plus or minus 5 seconds of their scheduled slot.
- No systematic scheduling drift occurs.
- No overlapping polls target the same device.
- Healthy-device poll completion is p95 under 15 seconds and p99 under 30 seconds.
- Normal completed-observation-to-query latency is under 10 seconds, with p95 under 15 seconds and p99 under 30 seconds.
- The platform exposes enough internal metrics to prove it is not operating at an unobservable saturation point.

### 4.3 Non-negotiable correctness invariants

1. Missing data is never represented as zero.
2. Collector overload is never reported as target downtime.
3. Collector outage is `UNKNOWN`/unobserved from that collector, not thousands of synthetic device-down events.
4. A partial successful result is retained and quality flagged.
5. Counter resets, discontinuities, and invalid baselines produce gaps/unknown rates, not spikes.
6. `ifIndex` is an observed attribute, never permanent interface identity.
7. Ambiguous interface matches are never automatically merged.
8. Uncommitted telemetry is never silently discarded.
9. Secrets never appear in logs, tracebacks, diagnostics, support bundles, API responses, or UI output.
10. The next due time is anchored to the intended schedule, not the previous completion time.
11. All canonical persisted timestamps are UTC.
12. Public dashboards cannot traverse to private inventory or admin functionality.

---

## 5. Deployment profiles and database abstraction

### 5.1 Supported profiles

| Profile | Metadata/control | Operational telemetry | Intended use | Required retention | Formal scale status |
|---|---|---|---|---|---|
| Lite | SQLite | SQLite | Development, training, CI, demos, and small standalone checks | 24 hours by default | Functional reference only; not enterprise-rated |
| Compact | SQLite | SQLite, time-partitioned | Small production deployments, including the MVP reference workload | 30 days native + 15-minute rollups through day 400 | Rated for the MVP workload in section 27.2 per node after the ADR-021 benchmark |
| Hybrid-small | SQLite | ClickHouse | Small site that wants ClickHouse history without PostgreSQL | 30 days native + rollups through day 400 | Supported functionally; benchmark before production sizing |
| Standard | PostgreSQL | TimescaleDB extension | Moderate deployments preferring a PostgreSQL-centered stack | Configurable; default same as enterprise where capacity permits | Supported functionally; scale claim requires benchmark |
| Enterprise | PostgreSQL | ClickHouse | Production design point | 30 days native + 15-minute rollups through at least day 400 | Must pass 4,000/100,000 acceptance |

Compact differs from Lite in purpose and retention, not in code. Both use the same SQLite adapters. Compact turns on time-partitioned telemetry tables (one table set per UTC day for native data and per month for rollups), so retention drops whole partitions instead of running large `DELETE` and `VACUUM` passes. The partition layout is confirmed by the ADR-021 benchmark.

The software shall not enforce arbitrary hard device limits for the Lite, Compact, or Standard profile. Documentation shall state tested reference workloads and warn when a deployment exceeds them. Only the Enterprise profile is required to meet the formal scale targets in this blueprint.

### 5.2 Mandatory persistence ports

All database access shall route through typed interfaces in the application’s port layer. At minimum, define:

- `MetadataUnitOfWork`
- `DeviceRepository`
- `InterfaceRepository`
- `SiteRepository`
- `TagGroupRepository`
- `PolicyRepository`
- `DashboardRepository`
- `UserSessionRepository`
- `AuditRepository`
- `CollectorRepository`
- `ObservationWriter`
- `ObservationQuery`
- `CurrentStateQuery`
- `RollupRepository`
- `CollectorHealthRepository`
- `MigrationManager`

Rules:

1. Domain, scheduler, collection, API, web, and TUI modules shall not import database-driver types.
2. SQLAlchemy models, ClickHouse rows, and driver result objects shall not escape adapter modules.
3. Repositories return domain objects or explicitly defined read models.
4. Transactions are owned by a unit-of-work boundary, not individual helper functions.
5. Backend capability differences are explicit through capability objects; code shall not use scattered `if backend == ...` checks.
6. Each adapter shall pass the same contract-test suite for behavior shared by all backends.
7. Backend-specific performance features may exist behind the port without changing public semantics.

### 5.3 SQLite requirements

- Enable WAL mode, foreign keys, and a configured busy timeout.
- Route writes through a bounded single-writer path to minimize lock contention.
- Never run blocking SQLite calls on the asyncio event loop; use an async driver or bounded worker thread.
- Lite telemetry shall use indexed, compact tables and a scheduled retention purge.
- Default Lite retention is 24 hours; configuration may reduce or extend it with an explicit capacity warning.
- SQLite current-state projections shall be reconstructable from retained observations within the profile’s retention limits.
- SQLite migrations shall be versioned and tested from every supported prior release.

### 5.4 PostgreSQL and TimescaleDB requirements

- PostgreSQL is the enterprise metadata/control database.
- The Standard profile may store operational telemetry in TimescaleDB hypertables.
- Use separate writer, reader, integration, and migration roles.
- Apply retention, compression, and continuous aggregation only through versioned migrations.
- Plain PostgreSQL tables shall not be used as an unbounded high-frequency telemetry store.
- TimescaleDB query results shall preserve the same gaps, quality flags, IDs, and rollup semantics as ClickHouse.

### 5.5 ClickHouse requirements

- ClickHouse is authoritative for enterprise operational observations, current-state projections, collector health, and rollups.
- Collectors shall never write one permanent row per individual metric operation; writes are bulk batches of observation rows.
- Schema design shall use compact numeric types, low-cardinality types where measured to help, and `ORDER BY`, partitioning, codecs, and materialized views selected by representative benchmarks.
- Initial candidate ordering shall favor the common filters `(device_uuid, interface_uuid, collector_uuid, timestamp)`; the final layout requires a recorded benchmark/ADR.
- Current-state tables or views shall use deterministic latest-observation semantics and remain rebuildable from authoritative observations where practical.
- Duplicate-safe replay shall use stable observation UUIDs and/or batch identifiers with query-visible deduplication semantics.

### 5.6 Migration rules

- Every schema change requires a versioned migration.
- Each migration includes upgrade steps, recovery/rollback notes, data-volume considerations, and test impact.
- Startup shall refuse to run with an unsupported future schema.
- Destructive migrations require backup verification and an explicit operator action.
- Migrations shall be restart-safe or detect partial completion clearly.
- Application binaries shall publish a supported schema-version range.

---

## 6. System architecture

### 6.1 Logical data flow

```text
Managed routers, switches, and related network devices
        │
        ├── ICMPv4 / ICMPv6
        └── SNMPv2c / SNMPv3
                │
                ▼
       Scheduler and poll-plan builder
                │
         ┌──────┴────────┐
         │               │
     ICMP tasks      SNMP tasks
         │               │
         └──────┬────────┘
                ▼
      Canonical typed observations
                │
         Bounded memory queue
                │
       Durable append-oriented spool
                │
       Idempotent batched ingestion
                │
      SQLite / TimescaleDB / ClickHouse
                │
       Query services and projections
         ┌──────┼───────────┐
         │      │           │
       API     Web          TUI/CLI
```

Metadata/control flows through SQLite or PostgreSQL repositories. Secret values flow only from credential providers into short-lived in-memory SNMP session objects.

### 6.2 Process model

The implementation shall support two modes:

**All-in-one mode** for Lite deployments:

- one process may host scheduler, collectors, ingestion, API/web, and maintenance tasks;
- internal queues remain bounded;
- blocking work remains off the event loop;
- components still communicate through the same ports used by multi-process mode.

**Service mode** for Standard/Enterprise deployments:

- `vakr-api.service` — API and web UI;
- `vakr-collector@.service` — one or more collector worker instances;
- `vakr-ingest.service` — spool ownership and telemetry writes;
- `vakr-maintenance.service` and timer — rollups, retention checks, reconciliation maintenance, and consistency checks;
- `vakr.target` — common start/stop/enable contract.

A supervisor-based alternative is allowed only if the external systemd contract and failure isolation remain equivalent.

**Compact (MVP) service mode** uses a reduced unit set with the same contract:

- `vakr-collector.service` — scheduler, ICMP and SNMP collection, discovery, spool, ingestion into the local SQLite telemetry store, rollups, and retention. It is the only writer of telemetry.
- `vakr-api.service` — API, web UI, the local Unix-socket API used by the TUI, and peer-replication endpoints (section 27.5). It reads telemetry and writes metadata.
- `vakr-update.service` and `vakr-update.timer` — optional automatic self-update, disabled by default (UPD-003).
- `vakr.target` — common start/stop/enable contract.

Inter-process contract (applies to all service modes):

- Collectors read their poll plans from the metadata repository and receive change notifications through a lightweight metadata change counter. They never take poll plans from the API process.
- Each collector writes its own spool directory (`/var/lib/vakr/spool/<collector_uuid>/`). The ingest component of that collector is the only consumer, so the spool has one producer and one consumer per collector. This resolves the ownership wording of sections 6.2 and 15.3.
- Configuration reload is signalled per unit (`systemctl reload` → `SIGHUP`), and each process validates and applies independently (CONF-003).
- Work assignment across several worker processes within one collector (SCHED-014) is deferred past MVP. The MVP runs one worker process per collector with bounded internal asyncio concurrency.
- Rate baselines (section 9.7) are owned by the collector that produced the samples. The latest accepted sample per interface is checkpointed to local state every minute, so a restart costs at most one rate interval (RATE-004).

### 6.3 Language-neutral observation boundary

Canonical observation schemas shall be versioned independently from Python classes. The initial Python implementation may use dataclasses or validated models internally, but spool and cross-process envelopes shall contain:

- schema version;
- observation type;
- observation UUID;
- batch UUID;
- collector UUID;
- poll UUID where applicable;
- UTC timestamp;
- payload;
- quality flags;
- checksum/integrity metadata.

The durable spool format shall be length-delimited MessagePack records inside append-only segment files, with a small versioned envelope and per-record checksum. This choice keeps the format compact and implementable in Python and Go. Changing the format requires an ADR and backward-read/migration plan for uncommitted segments.

### 6.4 Backpressure and storage exhaustion

- Every in-memory queue has a configured maximum.
- Queue saturation is observable and causes controlled producer backpressure.
- Completed observations shall reach durable spool before they are considered safely handed off.
- Spool capacity states are `NORMAL`, `WARNING`, `CRITICAL`, and `EXHAUSTED`.
- Default thresholds are 70%, 85%, and 95% of the configured maximum; all are configurable.
- At `CRITICAL`, the system prominently reports capacity degradation and prioritizes replay/retention maintenance.
- At `EXHAUSTED`, the collector shall stop scheduling new normal telemetry work rather than silently lose observations. It may continue a minimal in-memory health check only when that result is explicitly not presented as durable monitoring state.
- Targets not polled because of capacity exhaustion are `COLLECTOR_FAILED_TO_POLL`/`UNKNOWN`, never down.
- If telemetry loss occurs despite safeguards, create an explicit loss incident containing the affected time range, observation classes, and estimated count.

### 6.5 Repository structure

```text
pyproject.toml
uv.lock
README.md
LICENSE
SECURITY.md
CONTRIBUTING.md
CODE_OF_CONDUCT.md
CHANGELOG.md
src/vakr/
  __init__.py
  app/                 # use cases and orchestration
  config/              # parsing, validation, precedence, reload
  credentials/         # resolver ports and providers
  domain/              # entities, value objects, enums, invariants
  ports/               # repository, clock, network, and service protocols
  adapters/
    metadata/
      sqlite/
      postgres/
    telemetry/
      sqlite/
      timescale/
      clickhouse/
    icmp/
    snmp/
  inventory/           # sites, devices, interfaces, tags, policies
  discovery/           # SNMP discovery and reconciliation
  scheduler/           # phases, due-time queue, poll plans, capacity
  collectors/
    icmp/
    snmp/
  telemetry/           # canonical observation models and quality flags
  ingest/              # queues, spool, batching, replay
  query/               # backend-neutral read models and graph queries
  api/                 # versioned HTTP API
  web/                 # templates, static assets, view models
  tui/                 # Textual application
  auth/                # users, sessions, RBAC, CSRF
  health/              # self-monitoring and diagnostics
  cli/                 # administration, validation, import/export, doctor
  service/             # process lifecycle and systemd integration
migrations/
  metadata/
  sqlite_telemetry/
  timescale/
  clickhouse/
tests/
  unit/
  contract/
  integration/
  acceptance/
  performance/
  security/
  ui/
  fixtures/
  simulators/
docs/
  architecture/
  requirements/
  api/
  operations/
  tutorials/
  oid-catalog/
  adr/
  traceability.md
```

Responsibilities shall remain separated even if exact file names change.

---

## 7. Canonical domain model

### 7.1 Inventory and control entities

| Entity | Required fields and purpose |
|---|---|
| Collector | Immutable UUID, name, observation point, enabled state, phase strategy, source addresses, health, clock status. |
| Worker | UUID, collector UUID, process instance, capacity settings, heartbeat, assignment state. |
| Site | Immutable UUID, name, short code, optional region/timezone/description, tags. Provides explicit site browsing without becoming a CMDB. |
| Device | Immutable UUID, display name, poll address, address family/preference, site, enabled/admin state, intervals, credential reference, comments, tags/groups, management source. |
| Interface | Immutable UUID, device UUID, current observed `ifIndex`, discovered attributes, lifecycle, monitoring state, comments, tags, display priority, management source. |
| InterfaceCircuitMetadata | Interface UUID, `is_wan_circuit`, provider/ISP, circuit ID, contracted/committed rate, capacity override, site override, high-priority flag, sticky flag, public-dashboard eligibility. |
| Tag | UUID, name, description. |
| Group | UUID, name, type, membership rules or explicit members. |
| CollectionPolicy | UUID, name, order, scope, match rules, include/exclude actions, intervals, timeout/retry/adaptive settings. |
| Dashboard | UUID, name, visibility, slug, owner, refresh behavior, filters/widgets, field policy. |
| CredentialReference | UUID/name, provider type, non-secret metadata, provider key. |
| LocalUser | UUID, username, role, password-hash metadata, enabled state, timestamps. |
| Session | Opaque session ID/hash, user UUID, expiry, CSRF state, source metadata. |
| AuditEvent | Actor, action, object, timestamp, outcome, request ID, redacted change summary. |

### 7.2 Observation entities

- `DevicePoll`
- `ICMPObservation`
- `SNMPDeviceObservation`
- `InterfaceObservation`
- `DiscoveryRun`
- `InterfaceDiscoveryObservation`
- `CollectorHealthObservation`
- `CollectionCoverageObservation`
- `TelemetryLossIncident`

Observations are immutable facts. Corrections are new facts or rebuilt projections, never in-place rewriting of historical evidence without an audited maintenance operation.

### 7.3 Derived/history entities

- `InterfaceIdentityHistory`
- `DeviceReachabilityPeriod`
- `InterfaceStatePeriod`
- `RestartEvent`
- `PollingHealthState`
- `RollupObservation`
- `CurrentDeviceState`
- `CurrentInterfaceState`
- `DashboardRankingSnapshot` where caching is needed

### 7.4 Required IDs and time fields

- Public entities use UUIDs or documented stable opaque IDs.
- Database row IDs shall not be public contracts.
- Every operational observation includes `collector_uuid`.
- Poll-derived observations include `poll_uuid`.
- Every observation includes an `observation_uuid` for deduplication.
- Persisted time is UTC with sufficient precision to preserve actual sample intervals.
- Human-readable displays use the configured IANA timezone.

### 7.5 Device administrative state

- `ENABLED` — normal scheduling applies.
- `MAINTENANCE` — intentionally not treated as an outage; scheduling behavior is configurable, defaulting to continued collection with maintenance labeling.
- `DISABLED` — no normal collection; shown as offline/gray.
- `RETIRED` — no collection; history retained; hidden from default active views unless requested.

### 7.6 Observed reachability and availability states

**ICMP:** `REACHABLE`, `IMPAIRED`, `UNREACHABLE`, `UNKNOWN`  
**SNMP:** `RESPONDING`, `TIMEOUT`, `ERROR`, `SKIPPED`, `PARTIAL`, `UNKNOWN`  
**Uptime continuity:** `CONTINUOUS`, `RESET_DETECTED`, `UNKNOWN`  
**Poll execution:** `SCHEDULED`, `RUNNING`, `COMPLETE`, `PARTIAL`, `MISSED_OVERRUN`, `FAILED`, `NOT_ATTEMPTED`  
**Persistence:** `QUEUED`, `SPOOLED`, `COMMITTED`, `REPLAYING`, `FAILED`  
**Collector:** `HEALTHY`, `DEGRADED`, `OVERLOADED`, `UNAVAILABLE`  
**Interface lifecycle:** `ACTIVE`, `INACTIVE`, `STALE`, `RETIRED`

Quality flags are combinable. Unknown/missing is never zero.

### 7.7 Device-list presentation buckets

The web and TUI default device list shall sort into these buckets:

1. **Attention** at the top — red for confirmed current failures; amber for impaired, partial, stale, collector-related unknown, or other degraded states.
2. **Active** in the middle — green for fresh, successful reachability with no current critical quality condition.
3. **Offline/retired** at the bottom — gray for administratively disabled or retired devices.

Within a bucket, sort by severity, then most recent state change, then natural display name. Color shall never be the only state indicator; include text, icon, and accessible labels.

---
## 8. Consolidated functional requirements

### 8.1 Product, platform, and scale requirements

| ID | Requirement |
|---|---|
| PROD-001 | The system shall be an open-source, Linux-native performance and reachability monitor replacing the operational roles of MRTG and SmokePing while adding discovery, persistent identity, status, dashboards, API, and TUI capabilities. |
| PROD-002 | The product shall not be designed as a full NMS. |
| PROD-003 | Core v1 shall exclude the non-goals listed in section 2.4. |
| PROD-004 | The product shall publish versioned API and supported read-only data access sufficient for future add-on alerting/notification services. |
| PROD-005 | Ubuntu 26.04 LTS is the primary development and release-validation platform. RHEL-family `.rpm` validation is added after MVP (PKG-006). |
| SCALE-001 | The Enterprise profile shall support 4,000 devices and 100,000 monitored interfaces. |
| SCALE-002 | The default normal polling interval shall be 60 seconds unless overridden by applicable policy. |
| SCALE-003 | Devices with 60-, 120-, and 300-second intervals shall coexist in the same worker pool. |
| SCALE-004 | Work assignment shall use estimated cost when available, not device count alone. |
| ARCH-001 | Domain and application behavior shall be independent of concrete database drivers, web templates, and network libraries. |
| ARCH-002 | The canonical observation contract shall be versioned and language-neutral so a later Go collector does not require API or storage-semantic redesign. |
| ARCH-003 | The implementation shall support all-in-one Lite mode and separated service mode without maintaining separate business logic. |

### 8.2 Metadata, sites, interface annotations, and ownership

| ID | Requirement |
|---|---|
| META-001 | Every device shall have an immutable application UUID independent of hostname, poll address, `sysName`, or `sysObjectID`. |
| META-002 | Every logical interface shall have an immutable application UUID independent of `ifIndex`. |
| META-003 | Devices shall support free-form operator comments. |
| META-004 | Interfaces shall support free-form operator comments. |
| META-005 | Devices and interfaces shall support tags and groups for navigation, dashboards, policy selection, and filtering. |
| META-006 | Devices shall optionally belong to a first-class Site record. Interfaces may override site association only when operationally necessary. |
| META-007 | Interfaces shall support explicit high-priority, WAN-circuit, sticky, provider/ISP, circuit ID, contracted rate, and capacity-override metadata. |
| META-008 | Each configurable record shall identify whether it is file-managed or API-managed. UI/API writes shall reject changes to file-managed fields with a clear explanation. |
| META-009 | Deleting an inventory record shall be a soft lifecycle operation by default; historical telemetry and identity history shall remain available. |
| META-010 | Bulk import/export shall support validated CSV and TOML for device inventory and interface annotations, using stable UUIDs where supplied and deterministic dry-run output. |
| META-011 | Every interface shall have an interest class: `primary` (interfaces of interest such as uplinks and primary-path transit, viewed often) or `secondary` (default; viewed occasionally for one-off questions). `primary` is the same flag as the high-priority annotation, not a second concept; `high_priority = true` is accepted as an alias. Both classes are collected and retained identically. |
| META-012 | Interface descriptions (`ifAlias`) are the first source of classification, because NetOps already put labels such as ISP, WAN, and Uplink in them. vakr shall extract keyword tags from descriptions using a configurable keyword list (defaults in Appendix E.15). Matching is case-insensitive and on whole tokens: a description is split on every non-alphanumeric character, so `WAN-ISP_Carrier` matches `WAN` and `ISP` but `SWANSEA` matches nothing. A `re:` keyword uses the bounded regex rules of POLICY-005. Each keyword maps to a tag and may also set interest `primary`, `wan_circuit`, or `sticky`. (In configuration and annotation files the key is `wan_circuit`, as in section 11.3; `is_wan_circuit` is the name of the domain field in section 7.1.) Description-derived tags are labeled as such and follow the description when it changes. Precedence is: explicit vakr annotation, then description keyword, then policy rule, then the default (`secondary`). The explain output (POLICY-004) shows which source applied. |
| META-013 | Interfaces whose descriptions carry no keyword shall be classifiable in vakr itself: bulk select-and-tag in the web UI, CSV/TOML import, and `vakr interface tag`. Tags added in vakr are stored apart from description-derived tags, and a later description change never removes them. Device pages and the interface list offer a "not yet classified" filter (no annotation and no keyword match), so NetOps can work through the remainder or decide to update device descriptions instead. |

### 8.3 Discovery requirements

| ID | Requirement |
|---|---|
| DISC-001 | A device with no inventory shall receive initial SNMP discovery. |
| DISC-002 | The default rediscovery interval shall be 24 hours and configurable by policy/device. |
| DISC-003 | An admin shall be able to trigger rediscovery through the web UI, API, and CLI. |
| DISC-004 | Discovery shall collect `sysDescr.0`, `sysObjectID.0`, `sysContact.0`, `sysName.0`, `sysLocation.0`, and `sysUpTime.0`. |
| DISC-005 | Discovery shall collect standards-based interface identity, state, capacity, MAC, and IPv4/IPv6 information. |
| DISC-006 | Multiple IPv4 and IPv6 addresses per interface shall be supported. |
| DISC-007 | Discovery shall inventory interfaces even when performance collection is suppressed. |
| DISC-008 | Discovery results shall record completeness and per-subtable quality; one failed subtable shall not erase successful discovery data. |
| DISC-009 | Rediscovery shall update capability knowledge so unsupported counters are not repeatedly requested, while allowing capability re-probe on a configured interval or manual action. |
| DISC-010 | A manual rediscovery shall be asynchronous from the web request, expose progress/result, and never block the API worker for the duration of SNMP collection. |
| DISC-011 | `ifAlias`, `ifName`, and `ifDescr` for known interfaces shall be refreshed with exact GETs every `discovery.alias_refresh_interval` (default 1 h), so description edits on a device reach classification (META-012) within an hour without a full rediscovery. A manual rediscovery applies them immediately. |

### 8.4 Collection policy requirements

| ID | Requirement |
|---|---|
| POLICY-001 | Policies shall support ordered lists of include and exclude patterns. |
| POLICY-002 | Precedence shall be global defaults, group/tag policy, device policy, then interface override, with explicit tie-breaking by priority/order. |
| POLICY-003 | Match fields shall include device identity, site, tags/groups, hostname, `ifName`, `ifDescr`, `ifAlias`, `ifType`, `ifIndex`, addressing, physical/logical characteristics, circuit metadata, and admin/oper state. |
| POLICY-004 | UI/API shall explain why an interface is monitored or suppressed and identify the effective rule. |
| POLICY-005 | Pattern syntax shall be explicit: glob by default, `re:` for bounded regular expressions, and `exact:` for exact matching. Invalid or unsafe patterns shall fail configuration validation. |
| POLICY-006 | Policy evaluation shall be deterministic and exposed through a dry-run/explain command. |
| POLICY-007 | Policies may set polling/discovery intervals, reachability strategy, SNMP timeout/retries, adaptive polling, stale threshold, and interface monitoring behavior within documented limits. |

### 8.5 Interface lifecycle requirements

| ID | Requirement |
|---|---|
| LIFE-001 | Interfaces shall distinguish `ACTIVE`, `INACTIVE`, `STALE`, and `RETIRED`. |
| LIFE-002 | An interface shall not lose history merely because it becomes down, suppressed, stale, or retired. |
| LIFE-003 | The default stale threshold shall be approximately 10 days and configurable. |
| LIFE-004 | Stale interfaces may stop minute-level state polling but remain eligible for periodic discovery and history. |
| LIFE-005 | Returning interfaces shall be reconciled against prior identities before a new UUID is created. |
| LIFE-006 | Lifecycle transitions shall be recorded with reason, effective time, actor/source, and prior state. |
| LIFE-007 | A sticky interface shall appear before non-sticky interfaces on device pages and TUI details; ties use configurable display rank then natural interface order. |

### 8.6 ICMP collection and reachability requirements

| ID | Requirement |
|---|---|
| ICMP-001 | A normal reachability cycle shall send five ICMP Echo probes by default. |
| ICMP-002 | Default spacing shall be approximately 200 ms and configurable. |
| ICMP-003 | Each individual probe RTT shall be preserved. |
| ICMP-004 | Raw no-response values shall be stored as `-1`; successful RTT values are non-negative integer microseconds. APIs may render no-response samples as `null` while documenting the raw storage sentinel. |
| ICMP-005 | `-1` values shall never participate in latency or jitter calculations. |
| ICMP-006 | Under the default `icmp_gated` strategy, two or more successful probes cause SNMP collection to proceed. |
| ICMP-007 | Under `icmp_gated`, zero or one successful probe records ICMP results and skips SNMP for that cycle. |
| ICMP-008 | Native observations shall retain sent, received, lost, loss percentage, minimum, average, median, maximum, and jitter. |
| ICMP-009 | The UI shall provide SmokePing-like latency distribution and loss visualization from individual samples. |
| ICMP-010 | The initial jitter definition shall be population standard deviation of successful RTT samples in the probe group; jitter is unavailable when fewer than two probes succeed. |
| ICMP-011 | IPv4 and IPv6 shall be supported, with address family and source binding recorded in the observation context. |
| ICMP-012 | ICMP shall use unprivileged ICMP datagram sockets by default, so no process needs `CAP_NET_RAW`. Preflight verifies that `net.ipv4.ping_group_range` includes the collector's group, and `vakr doctor` prints the exact fix if it does not. Where unprivileged ICMP cannot be enabled, a documented collector-only unit drop-in may grant `CAP_NET_RAW`. The API/web process shall never receive it. |
| ICMP-013 | An opt-in `snmp_fallback` strategy may attempt a low-cost SNMP Stage 1 request after 0/5 or 1/5 ICMP success. Successful SNMP proves management reachability but does not convert ICMP loss into success. |
| ICMP-014 | An `icmp_only` monitoring mode shall support devices without SNMP credentials; a `snmp_only` mode may support explicitly configured devices that cannot be probed by ICMP. |

### 8.7 SNMP requirements

| ID | Requirement |
|---|---|
| SNMP-001 | Support SNMPv2c and SNMPv3. SNMPv1 is not required. |
| SNMP-002 | SNMPv3 credential records shall include username, security level, authentication protocol/secret, and privacy protocol/secret as applicable. |
| SNMP-003 | SNMPv3 `authPriv` using SHA-2 and AES shall be the recommended posture where device support permits. |
| SNMP-004 | Normal minute-level polling shall not walk the full IF-MIB when known interface indexes are available. |
| SNMP-005 | Stage 1 shall retrieve `sysUpTime.0` and exact `ifAdminStatus`/`ifOperStatus` OIDs for interfaces requiring current state checks. |
| SNMP-006 | Only interfaces returning admin up and oper up shall proceed to normal Stage 2 counter collection, unless a documented diagnostic action explicitly requests otherwise. |
| SNMP-007 | Stage 2 shall prefer 64-bit high-capacity octet and packet counters, using 32-bit fallbacks only where required. |
| SNMP-008 | The collector shall retrieve unicast, multicast, and broadcast packet components needed for aggregate packet totals but persist only aggregate input/output packet counters and rates. |
| SNMP-009 | Stage 2 shall collect input/output errors and `ifCounterDiscontinuityTime` where supported. |
| SNMP-010 | Device/interface counter capabilities shall be remembered to avoid repeatedly requesting unsupported OIDs. |
| SNMP-011 | Default timeout shall be 2 seconds and retries 1, with scoped overrides. |
| SNMP-012 | Default per-device outstanding SNMP PDU concurrency shall be one. |
| SNMP-013 | Any successful SNMP response is evidence that the target was reachable from that collector during the cycle, independent of ICMP quality. |
| SNMP-014 | Partial successful SNMP data shall be retained and quality flagged. |
| SNMP-015 | The implementation shall use current asyncio-capable PySNMP APIs and shall not invoke blocking SNMP operations on the event loop. |
| SNMP-016 | Support configurable UDP port, SNMPv3 context name, IPv4/IPv6 transport, and optional source address. Context engine ID may be added when required by real devices. |
| SNMP-017 | Request batching shall cap varbind count and estimated response size to reduce fragmentation and weak-agent overload. Adaptive batch reduction after size/tooBig errors shall be supported. |
| SNMP-018 | Retries shall be complete-attempt retries with bounded budgets; the observation shall retain attempt count and final error/quality context. |
| SNMP-019 | User-controlled values shall never be interpolated into shell commands. Network targets, ports, OIDs, contexts, and pattern inputs shall be normalized and validated before use. |

### 8.8 Uptime and outage classification requirements

| ID | Requirement |
|---|---|
| UPTIME-001 | Store `sysUpTime.0` from every successful SNMP device observation. |
| UPTIME-002 | After a reachability gap, compare prior uptime, elapsed wall time, and new uptime to classify continuity versus reset/restart evidence. |
| UPTIME-003 | UI and documentation shall state that `sysUpTime.0` is management-subsystem uptime, not absolute proof of chassis/OS uptime. |
| UPTIME-004 | Uptime comparison shall account for TimeTicks wrap and documented tolerance for scheduling/response delay. |
| UPTIME-005 | A restart classification shall record evidence and confidence; it shall not claim a chassis reboot when only management restart evidence exists. |

### 8.9 Counter and rate requirements

| ID | Requirement |
|---|---|
| RATE-001 | Rates shall use actual elapsed observation time, not the configured interval. |
| RATE-002 | Native observations shall retain cumulative counters and derived traffic/packet rates. |
| RATE-003 | Baselines shall be invalidated after first observation, uptime reset, counter discontinuity, unresolved identity change, unexplained decrease, missing prior sample, excessive gap, or source-width change. |
| RATE-004 | An invalid baseline stores the raw sample, marks rates unavailable, establishes a new baseline, and never generates an artificial spike. |
| RATE-005 | A 32-bit wrap may be accepted only when mathematically and operationally plausible and no reset/discontinuity evidence conflicts. |
| RATE-006 | Traffic rate is `delta_octets * 8 / elapsed_seconds`; packet rate is `delta_packets / elapsed_seconds`. |
| RATE-007 | Input/output error deltas and rates shall be derived with the same baseline safety rules so top-error rankings never use raw cumulative counters. |
| RATE-008 | Utilization percentage shall use effective capacity in this order: explicit capacity override, contracted/committed rate when configured as the utilization denominator, normalized `ifHighSpeed`, then `ifSpeed`. The UI shall label which denominator is used. |
| RATE-009 | Rates that exceed a configurable plausibility threshold relative to known capacity shall be quality flagged, not silently clipped. |
| RATE-010 | Missing speed or capacity shall produce absolute rates with utilization shown as unavailable. |

### 8.10 Persistent interface identity requirements

| ID | Requirement |
|---|---|
| IFID-001 | Rediscovery shall reconcile interfaces to persistent UUIDs rather than using `ifIndex` as identity. |
| IFID-002 | Evidence shall include same-device scope, `ifIndex` history, `ifName`, `ifDescr`, `ifAlias`, `ifPhysAddress`, `ifType`, and IP addressing. |
| IFID-003 | Outcomes shall include exact match, confident renumber, probable match, ambiguous, new interface, and missing interface. |
| IFID-004 | Ambiguous or merely probable matches shall not automatically merge history. |
| IFID-005 | Identity history shall retain prior indexes, evidence, score/confidence, decision source, and timestamps. |
| IFID-006 | Automatic reconciliation shall be deterministic and conservative. Exact/confident candidates must be unique and exceed the runner-up by a documented margin. |
| IFID-007 | Admins shall be able to review ambiguous/probable cases and explicitly map to an existing interface or create a new identity; every decision is audited. |
| IFID-008 | Zero, broadcast, multicast, locally duplicated, or otherwise non-unique MAC values shall not be treated as globally strong identity evidence. |
| IFID-009 | Reconciliation unit tests shall use a versioned corpus including physical ports, port-channels, VLANs, loopbacks, subinterfaces, virtual interfaces, ifIndex renumbering, description changes, and duplicate MAC cases. |

### 8.11 Scheduler and poll-planning requirements

| ID | Requirement |
|---|---|
| SCHED-001 | Collector name shall default to the Linux short hostname and be configurable. |
| SCHED-002 | Phase selection and device ordering shall be independent controls. |
| SCHED-003 | Default mode shall derive deterministic phase from short hostname with stable/common device order. |
| SCHED-004 | Support an administrator-defined fixed phase. |
| SCHED-005 | Support opt-in hashed device order using collector plus device identity. |
| SCHED-006 | Support hostname phase combined with hashed order. |
| SCHED-007 | Hash modes shall be documented as collision reduction, not a non-overlap guarantee. |
| SCHED-008 | Work shall be distributed throughout the interval rather than burst at the boundary. |
| SCHED-009 | A poll plan shall model `ICMP -> Stage 1 -> evaluate up/up -> conditional Stage 2`. |
| SCHED-010 | Next due time shall derive from intended cadence, not completion time. |
| SCHED-011 | Internal waits/deadlines shall use monotonic time; persisted timestamps use UTC wall time. |
| SCHED-012 | If a prior poll is active at the next due time, do not overlap; record an overrun/missed occurrence and preserve cadence. |
| SCHED-013 | Mixed intervals shall be interleaved in a common due-time priority model. |
| SCHED-014 | Stable worker affinity should be maintained where practical, with controlled rebalancing. |
| SCHED-015 | Estimated work may include interface count, expected active interfaces, varbind count, polling frequency, observed latency, timeout history, and device behavior. |
| SCHED-016 | Global and per-device concurrency shall be bounded and observable. |
| SCHED-017 | A poll not attempted because of scheduler, collector, or persistence capacity shall be recorded as not attempted/unknown, not failed target reachability. |
| SCHED-018 | Clock steps shall not corrupt monotonic scheduling. Persisted scheduled times shall remain explainable after wall-clock adjustment. |

### 8.12 Adaptive polling requirements

| ID | Requirement |
|---|---|
| ADAPT-001 | Poll-health diagnostics shall always be enabled. |
| ADAPT-002 | Automatic interval changes shall be disabled by default and require explicit policy enablement. |
| ADAPT-003 | The initial ladder should support 60 -> 120 -> 300 seconds with hysteresis on recovery. |
| ADAPT-004 | Store configured and effective intervals and expose the reason for differences. |
| ADAPT-005 | ICMP cadence may remain faster than SNMP interface cadence for adaptively slowed devices. |
| ADAPT-006 | Adaptive changes shall never mask collector overload as a target-specific problem. |
| ADAPT-007 | Every adaptive transition shall be deterministic, time-bounded, observable, and covered by state-machine tests. |

### 8.13 Time and freshness requirements

| ID | Requirement |
|---|---|
| TIME-001 | Canonical stored timestamps shall be UTC. |
| TIME-002 | Display timezone defaults to the Linux host timezone. |
| TIME-003 | An administrator may configure an IANA timezone for web, TUI, reports, and operator-facing event times. |
| TIME-004 | Machine-readable API timestamps shall be ISO 8601 with `Z` or an explicit offset; UTC is preferred. |
| TIME-005 | Current-state read models shall expose observation age and freshness, not just the latest state value. |
| TIME-006 | Freshness thresholds shall derive from effective interval with configurable minimums and multipliers; stale data shall not be presented as current. |

### 8.14 Configuration requirements

| ID | Requirement |
|---|---|
| CONF-001 | Core functionality shall be configurable through validated text files at startup and reload. |
| CONF-002 | Global, group/tag, site, device, and interface overrides shall be supported where defined. |
| CONF-003 | Candidate configuration shall validate before activation; invalid reload preserves active configuration. |
| CONF-004 | Failed reload shall not terminate healthy services. |
| CONF-005 | Human-authored reference configuration shall use TOML. |
| CONF-006 | Precedence shall be built-in defaults < base TOML < lexical `conf.d/*.toml` < explicitly supported environment overrides < CLI one-shot overrides. The effective source of each setting shall be inspectable. |
| CONF-007 | Credentials shall use a separate protected provider; ordinary device definitions contain only references. |
| CONF-008 | File-managed inventory and API-managed inventory shall have explicit ownership and conflict errors, never last-writer-wins ambiguity. |
| CONF-009 | `vakr config validate` shall validate syntax, semantic constraints, references, patterns, paths, permissions, database connectivity options, and unsupported backend combinations without starting collectors. |
| CONF-010 | Reload shall produce a structured diff and reject destructive changes requiring an explicit migration/admin action. |

### 8.15 Storage, ingestion, and retention requirements

| ID | Requirement |
|---|---|
| STORE-001 | Metadata/control shall use PostgreSQL for Enterprise and SQLite for Lite, Compact, and small deployments. |
| STORE-002 | Enterprise operational observations, latest state, health, and rollups shall use ClickHouse. Lite and Compact use SQLite; Standard may use TimescaleDB. |
| STORE-003 | High-frequency collectors shall not write each metric individually to permanent storage. |
| STORE-004 | Completed observations flow through bounded memory, durable spool, and bulk insert. |
| STORE-005 | `/dev/shm` may accelerate but shall never be the sole durability layer. |
| STORE-006 | Spool writes shall be append/sequential, replay after recovery, and never silently discard uncommitted observations. |
| STORE-007 | Unavoidable loss shall create an explicit telemetry-loss incident. |
| STORE-008 | Mutable descriptive metadata shall not be duplicated in every time-series row. |
| STORE-009 | Spool replay shall be idempotent after definite or uncertain backend commit outcomes. |
| STORE-010 | Batch acknowledgement shall occur only after the backend adapter reports durable acceptance according to that backend’s contract. |
| STORE-011 | Batches shall flush on configurable time or row thresholds, initially targeting 5-10 seconds and roughly 10,000-50,000 rows for Enterprise, subject to benchmark. |
| STORE-012 | Queries shall preserve missing intervals and quality flags across native and rollup data. |
| STORE-013 | All backends shall expose equivalent observation IDs and semantics even when physical schemas differ. |
| STORE-014 | Database unavailability shall not block network polling while durable spool capacity remains. |
| RET-001 | Retain at least 30 days of native one-minute telemetry in Enterprise. |
| RET-002 | Retain historical rollups through at least day 400. |
| RET-003 | The reference long-term bucket is 15 minutes for days 31-400. |
| RET-004 | Interface rollups retain maximum and p95 inbound/outbound rates, sample/availability counts, and useful state/quality indicators. |
| RET-005 | Long-term interface rollups may omit packet and error counters after native retention. |
| RET-006 | ICMP rollups retain sent/received/lost counts and RTT/jitter distribution summaries; outage/restart boundaries are not averaged away. |
| RET-007 | Lifecycle alone shall not delete history. Data expires only by retention or explicit audited purge. |
| RET-008 | Aggregate packet loss from underlying counts, never averages of percentages. |
| RET-009 | Rollup jobs shall be restart-safe and detect/report incomplete buckets. |

### 8.16 Current state and query requirements

| ID | Requirement |
|---|---|
| STATE-001 | Operational observation storage is authoritative for current operational state; optimized latest projections are allowed. |
| STATE-002 | Current-state projections shall be rebuildable from observations where practical. |
| STATE-003 | Current state shall include observation time, age, collector, coverage, and quality, not only a status label. |
| STATE-004 | Multi-collector observations shall remain separately queryable; composite state may be derived but shall not erase per-collector evidence. |
| STATE-005 | Query services shall choose native versus rollup resolution automatically and disclose resolution in API responses. |

### 8.17 API requirements

| ID | Requirement |
|---|---|
| API-001 | Provide documented, versioned APIs for inventory, current state, history, dashboards, collector health, and permitted admin actions. |
| API-002 | Public APIs expose stable UUIDs/opaque identifiers, not database row IDs. |
| API-003 | Provide supported read-only integration through API and/or database views. |
| API-004 | The initial API prefix shall be `/api/v1`; incompatible changes require a new major API version or documented transition. |
| API-005 | List endpoints shall support bounded pagination, filtering, stable sorting, and total/continuation metadata. |
| API-006 | History endpoints shall require bounded time ranges and return the actual resolution, timezone, quality fields, and truncation status. |
| API-007 | Errors shall use a stable envelope containing code, message, request ID, and field details without secrets or stack traces. |
| API-008 | OpenAPI documentation and machine-readable schemas shall be generated and checked into release artifacts. |
| API-009 | Current-state updates may use Server-Sent Events for authenticated clients; WebSockets are not required for v1. |
| API-010 | Public dashboard APIs shall be separate, read-only, rate-limited, and restricted to dashboard-approved fields and objects. |
| API-011 | Administrative operations such as rediscovery, import, projection rebuild, and maintenance shall return operation IDs and expose progress/result. |
| API-012 | CSV/JSON export shall be bounded, asynchronous for large requests, and authorized at the same level as the underlying data. |

### 8.18 Web UI requirements

| ID | Requirement |
|---|---|
| UI-001 | Provide device list/detail, interface list/detail/graphs, reachability visualization, dashboards, tag/group/site navigation, admin rediscovery, and collector health. |
| UI-002 | Interface detail shall show inbound/outbound traffic and errors plus identity, description, current values, packets, speed/capacity, and IP addresses. |
| UI-003 | Device detail shall show recent ICMP and SNMP results, uptime, reachability, system information, interfaces, comments, collector, and last poll. |
| UI-004 | Dashboards shall pin interfaces across devices and use tags/groups for simplified views. |
| UI-005 | Default device list shall use Attention -> Active -> Offline/Retired ordering with severity/freshness indicators and sorting/filter controls. |
| UI-006 | Device interfaces shall show sticky/high-priority/WAN circuit interfaces first, then remaining interfaces. |
| UI-007 | Anonymous public dashboards shall require explicit admin publication and shall reveal only approved display labels, metrics, and time-series data. |
| UI-008 | Authenticated default dashboards shall include high-priority interfaces, top utilization, top errors, ISP/provider view, WAN view, and site view. |
| UI-009 | Device status shall be browsable by device, site, tag, group, provider, and lifecycle/admin state. |
| UI-010 | Graphs shall show gaps for missing/invalid data, quality context, actual units, selected timezone, and native/rollup resolution. |
| UI-011 | UI shall meet WCAG 2.2 AA for supported workflows, including keyboard navigation, focus visibility, semantic labels, contrast, and non-color status cues. |
| UI-012 | No runtime asset shall depend on a public CDN. Front-end dependencies are pinned, locally served, licensed, and included in the SBOM. |
| UI-013 | Public and NOC dashboards shall support kiosk mode and configurable auto-refresh without full-page flashing. |
| UI-014 | Every page shall have useful loading, empty, stale, partial, permission-denied, and backend-unavailable states. |
| UI-015 | The UI shall not imply certainty when data is stale, partial, unknown, or from an unhealthy collector. |
| UI-016 | After the device summary, the device page shall offer an interface-graph view with one compact traffic/error graph card per monitored interface, ordered sticky/high-priority/WAN first and lazy-loaded/paginated so large devices remain responsive. |
| UI-017 | Device pages shall render primary-interest interfaces (META-011) in full above the fold. Secondary interfaces shall appear in a searchable, paginated "Other interfaces" section that loads on demand, and any interface's full history shall be reachable within two interactions from global search. |

### 8.19 TUI requirements

| ID | Requirement |
|---|---|
| TUI-001 | `vakr tui` shall launch a read-only terminal interface for technical staff. |
| TUI-002 | The default screen lists devices with Attention at top, Active in middle, and Offline/Retired at bottom. |
| TUI-003 | The list shall support search, filter, sort, paging/virtual scrolling, refresh, and clear text/icon states in addition to color. |
| TUI-004 | Selecting a device shall show basic system information, reachability/SNMP state, uptime, last poll, collector, tags/site, and a table of interfaces and current metrics. |
| TUI-005 | Interface table columns shall include name, description/alias, admin/oper state, in/out rate, utilization where available, packet rate, errors, speed/capacity, and observation age. |
| TUI-006 | Sticky/high-priority/WAN circuit interfaces shall appear first. |
| TUI-007 | TUI data access shall use application/query services or the versioned local API; it shall never issue ad hoc SQL. |
| TUI-008 | The TUI shall remain usable in a 120x30 terminal and degrade gracefully in narrower terminals. |
| TUI-009 | The TUI shall expose collector/database/spool health and clearly identify stale/unknown data. |
| TUI-010 | The TUI shall not display secrets, credential identifiers not needed by operators, or private public-dashboard-hidden fields to anonymous users. |

### 8.20 Dashboard and ranking requirements

| ID | Requirement |
|---|---|
| DASH-001 | A high-priority interface dashboard shall show interfaces with the explicit high-priority flag, current state, utilization, and a compact recent graph. |
| DASH-002 | A WAN circuit dashboard shall show interfaces marked `is_wan_circuit`, grouped/filterable by site and provider. |
| DASH-003 | Top utilization shall default to a 15-minute window and rank by the higher of inbound/outbound p95 utilization over that window; users may select supported windows. |
| DASH-004 | Top errors shall rank by valid input plus output error deltas over the selected window and show raw delta and error-per-million-packets when packet data permits. |
| DASH-005 | Rankings shall exclude unavailable baselines and shall visibly identify incomplete coverage. |
| DASH-006 | Group views shall support ISP/provider, WAN role, site, tag, and group. |
| DASH-007 | Dashboard definitions shall be metadata records with versioned widget schemas and migration support. |
| DASH-008 | Public visibility is per dashboard and per eligible interface; an object must satisfy both to appear anonymously. |
| DASH-009 | Public dashboard slugs shall be non-sequential opaque values and may additionally use IP allowlists or reverse-proxy access control. |
| DASH-010 | A dashboard query shall have bounded object count, time range, and point count to protect the query tier. |

### 8.21 Security and authentication requirements

| ID | Requirement |
|---|---|
| SEC-001 | Device definitions reference credentials by ID/name rather than storing plaintext secrets. |
| SEC-002 | Initial credentials use a protected file provider behind a resolver interface. |
| SEC-003 | Secrets shall never be returned or logged in any presentation, diagnostic, exception, support, or audit payload. |
| SEC-004 | Services run under dedicated unprivileged identities; capabilities are narrowly scoped. |
| SEC-005 | Built-in authentication supports admin and operator/user roles. |
| SEC-006 | Operator/user is read-only; admin controls configuration-changing actions. |
| SEC-007 | Architecture permits later OIDC, SSO, and trusted-proxy authentication without changing collection/storage semantics. |
| SEC-008 | Use separate DB roles for write, read, integration, and migration where supported. |
| SEC-009 | Local passwords shall use Argon2id with versioned parameters and transparent rehash on successful authentication when parameters change. |
| SEC-010 | Sessions shall use opaque server-side records, secure/HTTP-only/SameSite cookies, idle and absolute expiry, rotation after login, and revocation. |
| SEC-011 | All state-changing browser requests require CSRF protection. CORS is disabled by default. |
| SEC-012 | Authentication endpoints shall use rate limiting and progressive delay; security events are audited without recording passwords. |
| SEC-013 | Production TLS shall terminate at a documented trusted reverse proxy or supported application TLS configuration. Forwarded headers are trusted only from configured proxies. |
| SEC-014 | Public dashboards shall use a strict response field allowlist and shall not expose private addresses, credentials, comments, internal IDs beyond public opaque IDs, or links into authenticated pages unless explicitly approved. |
| SEC-015 | Admin-configurable target allow/deny CIDRs shall be supported. Loopback, multicast, unspecified, and known metadata-service addresses are denied by default unless explicitly allowed for a documented deployment. |
| SEC-016 | Credential files shall be rejected or warned according to policy when ownership or permissions are unsafe; strict mode is the production default. |
| SEC-017 | Dependency, secret, license, and static security scanning shall run in CI. Release artifacts include an SBOM. |
| SEC-018 | An audit record shall be created for login/logout, user changes, inventory/policy/dashboard changes, public publication, rediscovery requests, imports, migrations, purges, and identity reconciliation decisions. |
| SEC-019 | Query inputs, export sizes, regexes, page sizes, and time ranges shall be bounded to prevent resource exhaustion. |
| SEC-020 | Support bundles shall use a field allowlist and automated redaction tests. |

### 8.22 Service, health, and operations requirements

| ID | Requirement |
|---|---|
| SVC-001 | Provide a top-level systemd contract for start, stop, restart, reload, status, enable, and disable. |
| SVC-002 | A supervisor or multiple units may implement the contract if behavior is equivalent. |
| SVC-003 | Graceful shutdown stops new polls, handles active work, flushes memory to durable spool, closes resources, and exits within a configured bound. |
| SVC-004 | Services shall support readiness, liveness, and detailed health endpoints/commands that distinguish dependencies. |
| SVC-005 | Startup shall validate configuration, paths, permissions, schema compatibility, spool writability, and required database connectivity before scheduling normal collection. |
| SVC-006 | Logs shall be structured with stable event IDs, request/poll IDs, severity, component, and redacted context; journald is the default sink. |
| SVC-007 | `vakr doctor` shall report configuration, permissions, time sync, database reachability, spool status, service state, and safe network test results. |
| SVC-008 | Backup, restore, upgrade, downgrade limitations, and disaster-recovery runbooks shall be documented and acceptance-tested. |
| SVC-009 | Production services shall set resource limits and restart policies that do not create rapid failure loops or duplicate collector identities. |
| OPS-001 | `vakr check health` shall print a single-line Nagios-plugin-format result with performance data (exit 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN). It covers collector health, schedule lateness, spool state, database availability, replication lag, and update status, so CheckMK/Nagios can monitor vakr itself. |

### 8.23 Multi-poller requirements

| ID | Requirement |
|---|---|
| MPOL-001 | Every operational observation includes collector identity. |
| MPOL-002 | Collector outage yields unknown/unobserved from that collector, not synthetic target-down. |
| MPOL-003 | Same counter observations from multiple collectors are not summed. |
| MPOL-004 | Future composite availability preserves per-collector reachability evidence. |
| MPOL-005 | Reliable time synchronization is assumed and collector clock health should be monitored. |
| MPOL-006 | Collector identity shall be stable across service restarts and unique across active observation points. |
| MPOL-007 | v1 shall expose per-collector history and comparison but need not implement advanced SLA voting/correlation. |

### 8.24 Self-observability requirements

| ID | Requirement |
|---|---|
| OBS-001 | Expose inventory/state counts; ICMP/SNMP counts; poll duration/lateness; queue, spool, batch, replay, ingestion, CPU, memory, event-loop, and database metrics. |
| OBS-002 | Distinguish device failure, SNMP failure, collector failed-to-poll, collector unavailable, database unavailable, partial, stale, and unknown. |
| OBS-003 | Collector overload shall be reported as capacity degradation, never target-down. |
| OBS-004 | Expose p50/p95/p99 poll duration, scheduling lateness, ingestion latency, and query latency. |
| OBS-005 | Provide a Prometheus-format metrics endpoint bound to localhost or otherwise protected by default. |
| OBS-006 | Health views shall show configured versus effective interval and reasons for adaptive or degraded behavior. |
| OBS-007 | Event-loop lag, worker heartbeat, spool oldest age, replay lag, and database merge/maintenance pressure shall be visible. |
| OBS-008 | The platform shall generate a safe diagnostic snapshot suitable for support without secrets. |

## 9. Collection behavior and algorithms

### 9.1 Device poll object

Each scheduled execution creates one logical `DevicePoll` containing at least:

```text
poll_uuid
collector_uuid
worker_uuid
device_uuid
scheduled_time_utc
started_time_utc
completed_time_utc
configured_interval_seconds
effective_interval_seconds
reachability_strategy
execution_state
overall_result
coverage_flags
```

Associated observations are one ICMP observation when ICMP is configured, zero or one SNMP device observation, zero or more interface observations, and collector-health context.

### 9.2 Poll state machine

```text
DEVICE DUE
   │
   ├── administrative state DISABLED/RETIRED
   │       └── no poll; preserve history
   │
   └── create DevicePoll and run configured reachability strategy
           │
           ├── ICMP configured
           │      └── send five probes with bounded spacing/deadline
           │
           ├── default icmp_gated
           │      ├── <2 replies -> persist ICMP -> SNMP SKIPPED -> handoff
           │      └── >=2 replies -> SNMP Stage 1
           │
           ├── snmp_fallback
           │      ├── >=2 replies -> SNMP Stage 1
           │      └── <2 replies -> one bounded SNMP Stage 1 attempt
           │
           ├── icmp_only
           │      └── persist ICMP -> handoff
           │
           └── snmp_only
                  └── SNMP Stage 1
                           │
                           ▼
                  sysUpTime + exact status OIDs
                           │
                           ▼
                   evaluate current up/up
                           │
                           ▼
                   conditional Stage 2 counters
                           │
                           ▼
                    canonical observation handoff
```

A stage failure shall not discard successful earlier-stage evidence.

### 9.3 ICMP math

For successful RTT values `x1..xn` in microseconds:

- `min_rtt_us = min(x)`
- `max_rtt_us = max(x)`
- `avg_rtt_us = arithmetic mean(x)`
- `median_rtt_us = median(x)`
- `jitter_us = sqrt(sum((x - mean)^2) / n)` when `n >= 2`
- `loss_count = sent - received`
- `loss_percent = 100 * loss_count / sent`

Round stored summary values using a documented integer rule. The raw individual values remain authoritative.

State defaults:

- 5/5 through 2/5: `REACHABLE` or `IMPAIRED` based on loss/latency policy; SNMP eligibility under default strategy is true.
- 1/5: `IMPAIRED` or `UNREACHABLE` according to policy, but default SNMP eligibility is false.
- 0/5: `UNREACHABLE`, unless collection was not attempted or collector health makes the result unknown.

The UI shall show the actual received count and shall not reduce these states to a single “ping up/down” boolean. The initial policy defaults, the device status table, and the other default thresholds are in Appendix E.

### 9.4 SNMP discovery catalog

Initial mandatory standards-based objects:

| Object | Numeric OID/base | Use |
|---|---|---|
| `sysDescr.0` | `1.3.6.1.2.1.1.1.0` | Discovery/display |
| `sysObjectID.0` | `1.3.6.1.2.1.1.2.0` | Device identification/capability hints |
| `sysUpTime.0` | `1.3.6.1.2.1.1.3.0` | Every SNMP poll; continuity |
| `sysContact.0` | `1.3.6.1.2.1.1.4.0` | Discovery/display |
| `sysName.0` | `1.3.6.1.2.1.1.5.0` | Discovery/display |
| `sysLocation.0` | `1.3.6.1.2.1.1.6.0` | Discovery/display |
| `ifIndex` | `1.3.6.1.2.1.2.2.1.1` | Observed interface index |
| `ifDescr` | `1.3.6.1.2.1.2.2.1.2` | Identity/display |
| `ifType` | `1.3.6.1.2.1.2.2.1.3` | Identity/policy |
| `ifMtu` | `1.3.6.1.2.1.2.2.1.4` | Discovery |
| `ifSpeed` | `1.3.6.1.2.1.2.2.1.5` | Capacity fallback |
| `ifPhysAddress` | `1.3.6.1.2.1.2.2.1.6` | MAC/identity evidence |
| `ifAdminStatus` | `1.3.6.1.2.1.2.2.1.7` | Stage 1 state |
| `ifOperStatus` | `1.3.6.1.2.1.2.2.1.8` | Stage 1 state |
| `ifLastChange` | `1.3.6.1.2.1.2.2.1.9` | State context |
| `ifInOctets` | `1.3.6.1.2.1.2.2.1.10` | 32-bit fallback |
| `ifInUcastPkts` | `1.3.6.1.2.1.2.2.1.11` | 32-bit packet fallback |
| `ifInErrors` | `1.3.6.1.2.1.2.2.1.14` | Input errors |
| `ifOutOctets` | `1.3.6.1.2.1.2.2.1.16` | 32-bit fallback |
| `ifOutUcastPkts` | `1.3.6.1.2.1.2.2.1.17` | 32-bit packet fallback |
| `ifOutErrors` | `1.3.6.1.2.1.2.2.1.20` | Output errors |
| `ifName` | `1.3.6.1.2.1.31.1.1.1.1` | Identity/display |
| `ifInMulticastPkts` | `1.3.6.1.2.1.31.1.1.1.2` | Packet fallback component |
| `ifInBroadcastPkts` | `1.3.6.1.2.1.31.1.1.1.3` | Packet fallback component |
| `ifOutMulticastPkts` | `1.3.6.1.2.1.31.1.1.1.4` | Packet fallback component |
| `ifOutBroadcastPkts` | `1.3.6.1.2.1.31.1.1.1.5` | Packet fallback component |
| `ifHCInOctets` | `1.3.6.1.2.1.31.1.1.1.6` | Preferred input octets |
| `ifHCInUcastPkts` | `1.3.6.1.2.1.31.1.1.1.7` | Preferred packet component |
| `ifHCInMulticastPkts` | `1.3.6.1.2.1.31.1.1.1.8` | Preferred packet component |
| `ifHCInBroadcastPkts` | `1.3.6.1.2.1.31.1.1.1.9` | Preferred packet component |
| `ifHCOutOctets` | `1.3.6.1.2.1.31.1.1.1.10` | Preferred output octets |
| `ifHCOutUcastPkts` | `1.3.6.1.2.1.31.1.1.1.11` | Preferred packet component |
| `ifHCOutMulticastPkts` | `1.3.6.1.2.1.31.1.1.1.12` | Preferred packet component |
| `ifHCOutBroadcastPkts` | `1.3.6.1.2.1.31.1.1.1.13` | Preferred packet component |
| `ifHighSpeed` | `1.3.6.1.2.1.31.1.1.1.15` | Preferred speed source |
| `ifAlias` | `1.3.6.1.2.1.31.1.1.1.18` | Identity/display |
| `ifCounterDiscontinuityTime` | `1.3.6.1.2.1.31.1.1.1.19` | Baseline validation |
| `ipAddressPrefixTable` | `1.3.6.1.2.1.4.32` | Prefix data |
| `ipAddressTable` | `1.3.6.1.2.1.4.34` | IPv4/IPv6 association |

Maintain a documentation catalog with data type, units, collection stage, fallback behavior, known vendor caveats, and simulator fixture references.

### 9.5 Stage 1 behavior

- Build exact OIDs from the current reconciled interface inventory.
- Query `sysUpTime.0` in every successful SNMP cycle.
- Query state only for interfaces whose lifecycle/policy requires current checking.
- Batch conservatively and split on response-size/agent limitations.
- Retain successful varbinds when other varbinds time out or error.
- Mark an interface’s current state unknown when its OID was not returned; never carry forward a fresh state without age/quality labeling.
- Pass only current admin-up/oper-up interfaces into Stage 2.

### 9.6 Stage 2 behavior

For each eligible interface:

1. Prefer HC octet counters.
2. Prefer HC unicast/multicast/broadcast packet components and sum them into aggregate input/output packet counts.
3. Collect input/output errors.
4. Collect discontinuity time.
5. Record the source width/counter family used.
6. Preserve partial data with per-field quality.
7. Do not persist all six packet component counters in normal telemetry.

Device capability records shall avoid querying unsupported OIDs on every cycle. A capability can be invalidated by rediscovery, explicit admin action, software-version change evidence, or configured aging.

### 9.7 Counter baseline algorithm

A rate calculation receives current and previous observations for the same collector, device, and persistent interface UUID. It shall reject the baseline if any of these apply:

- no previous observation;
- non-positive elapsed time;
- elapsed time exceeds configured maximum gap;
- prior/current observation has invalid identity or collection quality;
- management uptime reset or inconsistent continuity;
- counter discontinuity changed;
- counter family/width changed;
- unexplained counter decrease;
- persistent interface identity is unresolved;
- current or prior counter is missing.

For a valid baseline:

```text
in_bps  = (current_in_octets  - previous_in_octets)  * 8 / elapsed_seconds
out_bps = (current_out_octets - previous_out_octets) * 8 / elapsed_seconds
in_pps  = (current_in_packets - previous_in_packets) / elapsed_seconds
out_pps = (current_out_packets - previous_out_packets) / elapsed_seconds
```

Error deltas/rates use the same pattern. A plausible 32-bit wrap adds `2^32` before subtraction only when uptime/discontinuity/capacity evidence supports it.

### 9.8 Interface reconciliation algorithm

The implementation shall use a deterministic candidate-scoring pipeline:

1. Candidate scope is always one device UUID.
2. Normalize strings without destroying the raw discovered value. Trim protocol padding and compare names case-insensitively where appropriate.
3. Treat unchanged `ifIndex` as strong current evidence but not permanent identity.
4. Treat a unique, nonzero, unicast physical MAC as strong evidence; weaken or ignore duplicated/virtual/invalid MACs.
5. Treat exact `ifName` as strong when unique on the device.
6. Treat overlapping unique IP assignments as strong supporting evidence.
7. Treat `ifDescr`, `ifAlias`, and `ifType` as supporting evidence.
8. Record conflicting evidence explicitly.
9. Require a unique best candidate and a minimum margin over the runner-up for automatic matching.
10. Auto-merge only exact or confident outcomes. Queue probable/ambiguous outcomes for admin review.

Initial scoring values and thresholds shall be isolated in one tested policy module and documented. They shall not be scattered as magic numbers. Before Phase 5 is complete, an ADR shall record the final initial weights based on the reconciliation fixture corpus.

### 9.9 Scheduling algorithm

- Represent due work in a monotonic-time priority queue.
- Calculate each next due point from the previous intended due point, advancing by whole intervals until it is in the future.
- Use deterministic phase within the interval.
- Never start a second poll for a device with an active poll.
- Record missed/overrun occurrences separately from reachability.
- Use stable worker affinity unless rebalancing is needed.
- Plan likely workload, but construct Stage 2 requests only after current Stage 1 state.
- Apply global and per-target semaphores.
- Track scheduled, started, completed, late, missed, canceled, and not-attempted counts.

### 9.10 Adaptive polling state machine

Adaptive polling is off by default. When enabled, a policy may move 60 -> 120 -> 300 seconds after repeated target-specific slow/timeout conditions. Recovery requires sustained healthy cycles and proceeds 300 -> 120 -> 60. The transition counters, windows, and thresholds shall be configurable and exposed.

Collector-wide overload shall not trigger target-specific adaptive penalties. It triggers collector degradation/capacity reporting and possibly workload admission control.

### 9.11 DNS and target addressing

- A device poll address may be an IP literal or DNS name.
- DNS resolution results and errors shall be observed separately from device reachability.
- Resolution shall be bounded and cached with TTL awareness.
- Address-family preference is configurable per device/collector.
- A changed DNS result does not change device UUID.
- Target validation rejects malformed, multicast, unspecified, and policy-denied addresses.
- Source address selection shall be explicit where a collector has multiple network paths.
- VRF/network-namespace deployment is supported operationally by running a collector instance in the appropriate Linux namespace; v1 need not manipulate VRFs itself.

---

## 10. Telemetry schemas and retention behavior

### 10.1 ICMP observation

One native row per device polling cycle contains:

```text
observation_uuid
poll_uuid
collector_uuid
device_uuid
timestamp_utc
address_family
source_address (nullable)
target_address
rtt_1_us ... rtt_5_us   # -1 means no reply in raw storage
sent_count
received_count
lost_count
loss_percent
min_rtt_us
avg_rtt_us
median_rtt_us
max_rtt_us
jitter_us
state
quality_flags
```

Summary RTT/jitter fields are nullable when insufficient successful probes exist.

### 10.2 SNMP device observation

```text
observation_uuid
poll_uuid
collector_uuid
device_uuid
timestamp_utc
snmp_version
request_count
response_count
timeout_count
retry_count
elapsed_ms
sys_uptime_ticks (nullable)
uptime_continuity
availability_state
partial_reason/error_code (nullable)
quality_flags
```

No credential values or community/user secrets are stored.

### 10.3 Interface observation

```text
observation_uuid
poll_uuid
timestamp_utc
collector_uuid
device_uuid
interface_uuid
ifindex_observed
admin_status
oper_status
in_octets
out_octets
in_packets
out_packets
in_errors
out_errors
in_bps
out_bps
in_pps
out_pps
in_error_delta
out_error_delta
in_error_rate
out_error_rate
sample_elapsed_us
counter_family
counter_discontinuity_ticks
baseline_state
quality_flags
```

Mutable names, descriptions, addresses, tags, site names, and circuit labels are not duplicated into each telemetry row.

### 10.4 Collector health observation

Include:

- worker/collector identity and version;
- heartbeat and clock offset/health;
- queue depths/capacities;
- scheduled/running/completed/late/missed counts;
- event-loop lag;
- CPU and memory;
- open file descriptors where available;
- spool bytes, percent, oldest record age, replay rate;
- batch rows/bytes and insert latency;
- metadata and telemetry database latency/availability;
- active device/interface counts;
- error counts by stable event code.

### 10.5 Native retention and rollups

Enterprise reference:

- Days 0-30: native observations.
- Days 31-400: 15-minute rollups.
- After day 400: expire unless policy extends retention.

Interface rollup fields shall include:

- bucket start/end;
- sample count, expected count, valid-rate count;
- availability/coverage counts;
- in/out bps maximum and p95;
- optional minimum/median/average when useful but not at the expense of required measures;
- state/quality summaries;
- outage/state transition references where applicable.

ICMP rollups shall include:

- sent, received, lost totals;
- loss derived from counts;
- RTT min, median, max, p95;
- jitter max and p95;
- SNMP success/failure counts;
- restart evidence counts/references;
- coverage and collector identity.

Do not average across outage or restart boundaries in a way that hides the event.

### 10.6 Graph query resolution

Default user ranges and preferred resolution:

| Range | Preferred data |
|---|---|
| 1 hour | Native |
| 6 hours | Native |
| 24 hours | Native |
| 7 days | Native or server downsampled native |
| 30 days | Native/downsampled native |
| 90 days | 15-minute rollup, with native where available if explicitly requested |
| 400 days | 15-minute rollup |

The query layer shall cap returned points, initially 1,200 points per series, and use deterministic bucket aggregation. Min/max envelopes shall preserve peaks during downsampling. The response identifies the chosen source and bucket width.

### 10.7 Storage sizing baseline

At 100,000 interfaces and 4,000 devices with one-minute polling:

- 30-day native interface observations: approximately 4.320 billion rows.
- 30-day native device/ICMP observations: approximately 172.8 million rows.
- 370-day 15-minute interface rollups: approximately 3.552 billion rows.
- 370-day 15-minute device/ICMP rollups: approximately 142.08 million rows.

A prior planning estimate is roughly 380 GB logical ClickHouse footprint using compact schemas and an operational allowance. This is a planning estimate, not a guarantee. Representative benchmarks shall determine actual hardware and schema. A 1 TB usable SSD-class volume per non-replicated ClickHouse node is a reasonable initial planning baseline; replication and backups require additional capacity.

---

## 11. Configuration model

### 11.1 Reference paths

```text
/etc/vakr/config.toml
/etc/vakr/inventory.toml
/etc/vakr/policies.toml
/etc/vakr/dashboards.toml
/etc/vakr/conf.d/*.toml
/etc/vakr/secrets/credentials.toml
/var/lib/vakr/
/var/lib/vakr/spool/
/var/log/vakr/            # optional file sink; journald is default
/run/vakr/
/run/vakr/api.sock        # local API socket for the TUI and CLI
/etc/vakr/tls/ca.d/       # extra trusted CA certificates, e.g. an enterprise TLS-inspection CA (EGRESS-002)
/opt/vakr/                # everything the package installs (read-only; PKG-002)
/opt/vakr/python/         #   bundled CPython interpreter
/opt/vakr/venv/           #   locked virtual environment containing vakr
/opt/vakr/share/defaults/ #   built-in defaults for every option (CFGM-001)
/opt/vakr/share/release-signers  # trusted release signing keys (UPD-001)
/var/cache/vakr/updates/  # downloaded and previous packages for self-update/rollback
```

Operator files under `/etc/vakr/` hold only the settings an operator has changed. Every option's default ships in `/opt/vakr/share/defaults/`, so a new release's new options take effect with documented defaults without editing operator files (section 28.4).

### 11.2 File versus API ownership

- Service/process, backend, logging, time, spool, security guardrail, and credential-provider settings are file-managed.
- Inventory, sites, policies, interface annotations, and dashboards may be file-managed or API-managed.
- A record created from a file is marked `managed_by=file:<source>` and cannot be edited through UI/API.
- API-managed records remain in metadata storage and are not overwritten by file reload.
- A conflict in natural key or UUID between sources fails validation with a clear message.
- Removing a file-managed device from configuration disables it by default; destructive prune requires an explicit command and dry run.

### 11.3 Reference configuration

Enterprise example of `/etc/vakr/config.toml` (a Compact/MVP example appears in section 27.9):

```toml
config_version = 1  # maintained by `vakr config upgrade`; do not edit (CFGM-002)

[time]
timezone = "America/New_York"

[profile]
name = "enterprise"  # lite | compact | hybrid-small | standard | enterprise

[metadata]
backend = "postgresql"
dsn_secret = "metadata-dsn"

[telemetry]
backend = "clickhouse"
dsn_secret = "telemetry-dsn"
native_retention_days = 30
rollup_retention_days = 400
rollup_interval = "15m"

[poller]
name = ""  # empty means Linux short hostname
schedule_strategy = "hostname-phase"
global_concurrency = 500
per_device_pdu_concurrency = 1

[polling]
default_interval = "60s"
discovery_interval = "24h"
icmp_count = 5
icmp_spacing = "200ms"
reachability_strategy = "icmp_gated"
snmp_timeout = "2s"
snmp_retries = 1
adaptive_polling = false

[spool]
path = "/var/lib/vakr/spool"
max_bytes = 107374182400
warning_percent = 70
critical_percent = 85
exhausted_percent = 95

[security]
trusted_proxy_cidrs = ["127.0.0.1/32", "::1/128"]
allowed_target_cidrs = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
denied_target_addresses = ["169.254.169.254"]
public_dashboards_enabled = false

[[sites]]
name = "nyc01"
display_name = "New York Primary"
region = "US-East"

[[devices]]
name = "router01"
address = "192.0.2.1"
site = "nyc01"
credential = "network-snmpv3"
comment = "Primary WAN router"

[[interface_policy.rules]]
name = "access-switch-uninteresting"
groups = ["access-switches"]
exclude_ifname_patterns = ["Loopback*", "Vlan*", "BVI*", "Tunnel*", "Null*"]

[[interface_annotations]]
device = "router01"
ifname = "GigabitEthernet0/0/0"
high_priority = true
wan_circuit = true
sticky = true
provider = "Example ISP"
circuit_id = "ABC12345"
contracted_bps = 1000000000
public_dashboard_eligible = true
```

Credentials live in a separate protected file, `/etc/vakr/secrets/credentials.toml` (mode `0640`, owner `root:vakr`), not in `config.toml`. Database DSNs referenced by `dsn_secret` are resolved from the same provider.

```toml
[credentials.network-v2c]          # MVP: SNMPv2c
version = "2c"
community = "<secret>"

[credentials.network-snmpv3]       # post-MVP: SNMPv3
version = "3"
username = "netmon"
security_level = "authPriv"
auth_protocol = "SHA256"
auth_secret = "<secret>"
privacy_protocol = "AES128"
privacy_secret = "<secret>"

[secrets]
metadata-dsn = "<secret>"
telemetry-dsn = "<secret>"
```

The actual credentials file shall never be committed. Examples use placeholders only.

### 11.4 Validation rules

Validation shall catch at least:

- unknown keys unless explicitly allowed for forward compatibility;
- invalid durations, ranges, CIDRs, addresses, ports, OIDs, IANA timezones, and pattern syntax;
- duplicate names/UUIDs;
- missing references;
- unsupported storage profile combinations;
- unwritable spool/run paths;
- unsafe credential permissions;
- impossible threshold orderings;
- public dashboards enabled without publication controls;
- retention that exceeds a backend’s configured capacity warning;
- intervals below safe configured minimums;
- conflicting file/API ownership;
- invalid role or session settings.

### 11.5 Reload behavior

1. Parse all candidate files.
2. Resolve includes/`conf.d` and documented environment overrides.
3. Validate syntax and semantics.
4. Build a redacted effective configuration and diff.
5. Classify changes as live-reloadable, restart-required, or migration-required.
6. Apply live changes atomically.
7. Preserve the previous configuration on any failure.
8. Emit structured success/failure events with redacted details.

### 11.6 Configuration across upgrades

Operator configuration survives every upgrade without manual re-entry, even across breaking changes. The mechanism — overrides-only operator files, packaged defaults, versioned config migrations, and new-option reporting — is specified in section 28.4.

---

## 12. Web information architecture and UX specification

### 12.1 Global navigation

Authenticated navigation shall include:

- Overview
- Devices
- Interfaces
- Sites
- Dashboards
- Collectors/Health
- Search
- Admin (admin only)

A global search shall match device name/address/UUID, interface name/alias/description, site, provider, circuit ID, tag, and group. Search results are permission filtered.

### 12.2 Overview dashboard

The authenticated default overview shall contain:

1. Device state summary with counts and freshness.
2. High-priority interfaces.
3. WAN circuits with current utilization/state.
4. Top utilization for the selected window.
5. Top errors for the selected window.
6. Collector/spool/database health.
7. Recent restart, rediscovery, identity-review, and telemetry-loss events.
8. Quick filters for provider, WAN, site, tag, and group.

Widgets shall link to filtered detail views when the user is authorized.

### 12.3 Device list

Required columns:

- state icon/text;
- display name;
- poll address;
- site;
- ICMP result/loss;
- SNMP state;
- uptime/continuity;
- active/monitored interface count;
- last successful observation;
- observation age/freshness;
- collector;
- tags;
- effective interval.

Required controls:

- search;
- status, site, collector, tag, group, and freshness filters;
- sortable columns;
- page size and pagination/virtual scrolling;
- compact/comfortable density;
- saved URL query state.

The default sort is Attention, Active, Offline/Retired as defined in section 7.7.

### 12.4 Device detail

Header:

- display name and state;
- poll address/address family;
- site/tags/groups;
- collector and latest poll age;
- admin state;
- comments;
- admin actions such as rediscover, edit metadata, and change state.

Summary cards:

- recent ICMP sent/received/loss;
- latency and jitter;
- SNMP request/response/timeout/partial status;
- `sysUpTime` with management-uptime disclaimer;
- `sysDescr`, `sysObjectID`, `sysName`, `sysLocation`, `sysContact`;
- configured/effective intervals and reason;
- discovery age/completeness;
- collection/quality warnings.

Content:

- reachability graph;
- an interface table, sticky interfaces first;
- a table/graph view toggle; the graph view renders a compact traffic/error graph for each monitored interface after the summary, with sticky/high-priority/WAN interfaces first and lazy loading/pagination for large devices;
- recent state/restart events;
- discovery/identity review information;
- per-collector comparison when more than one collector has data.

### 12.5 Interface list and detail

Interface list columns:

- sticky/high-priority/WAN indicators;
- name, alias, and description;
- admin/oper state;
- lifecycle/monitoring state;
- inbound/outbound bps and utilization;
- inbound/outbound pps;
- input/output error delta/rate;
- effective capacity and source;
- addresses;
- observation age/quality;
- suppression reason where applicable.

Interface detail contains:

- identity and circuit metadata;
- current values and freshness;
- traffic/utilization graph;
- packet-rate graph;
- error graph;
- state timeline;
- quality/discontinuity markers;
- addressing and MAC details;
- identity history and prior `ifIndex` values;
- policy explanation;
- comments and tags;
- admin edit/reconciliation controls where authorized.

### 12.6 Graph behavior

All graphs shall:

- use the configured display timezone;
- show range presets 1h, 6h, 24h, 7d, 30d, 90d, and 400d;
- support a custom bounded range;
- show cursor time/value and series legend;
- preserve missing data as gaps;
- mark invalid baseline, restart, discontinuity, partial collection, and collector outage where practical;
- display units using SI network conventions (`kbit/s`, `Mbit/s`, `Gbit/s`);
- show exact values in accessible tooltips and a tabular summary;
- identify native versus rollup/downsampled resolution;
- avoid misleading line interpolation across long gaps;
- keep inbound and outbound visually distinct without relying only on hue;
- permit bounded CSV/JSON export for authorized users.

Traffic graphs shall optionally show capacity/contracted-rate reference lines. Latency graphs shall show individual RTT distribution (“smoke”), median/summary line, and loss overlay.

### 12.7 Public dashboards

Public dashboards:

- are disabled globally by default;
- require an admin to publish each dashboard;
- include only interfaces explicitly eligible for public display;
- expose sanitized labels and metrics through separate public endpoints;
- have no authenticated navigation menu or private object links;
- show a clear data-freshness timestamp;
- support kiosk display;
- use cache and rate limiting;
- are covered by negative authorization tests;
- may be restricted by reverse proxy or IP allowlist.

### 12.8 Accessibility and visual semantics

- Red means confirmed attention/failure, amber means degraded/unknown, green means fresh healthy state, gray means administratively offline/retired.
- Every status includes text and icon.
- Keyboard users can reach all controls and graph summaries.
- Tables have proper headings and sortable-state announcements.
- Auto-refresh preserves keyboard focus and does not repeatedly announce unchanged content.
- Motion can be reduced through `prefers-reduced-motion`.
- Graphs include non-visual summaries and downloadable data.

### 12.9 Performance budgets

Under normal query load:

- server-rendered list/detail pages should return first HTML within 1 second p95 excluding intentionally large exports;
- dashboard API responses should be under 2 seconds p95 for bounded default windows;
- initial JS/CSS assets should remain intentionally small and locally cacheable;
- tables shall use server pagination or virtualized rendering rather than returning 100,000 interfaces to a browser;
- graph endpoints shall cap series and points.

---

## 13. Terminal UI specification

### 13.1 Invocation and connection

```text
vakr tui [--api-url URL] [--profile NAME] [--refresh SECONDS]
```

On the local monitoring server, the TUI shall default to a protected localhost/Unix-socket API path and a local read-only authentication mechanism. Remote API use requires explicit URL and credentials/token configuration. The TUI shall never read secret credential files.

### 13.2 Default screen

Header:

- product/version;
- selected collector/profile;
- current timezone/time;
- API/database/spool health summary;
- refresh indicator.

Device table:

- state;
- device;
- site;
- address;
- loss/latency;
- SNMP;
- interface counts;
- last poll age;
- collector.

Keys:

- `/` search;
- `f` filter;
- `s` sort;
- `r` refresh;
- `Enter` open device;
- `h` health;
- `?` help;
- `q` quit;
- arrow/PageUp/PageDown navigation.

### 13.3 Device screen

Show a compact summary followed by an interface table. Sticky/high-priority/WAN interfaces appear first. The interface table supports horizontal/column selection when terminal width is limited. Selecting an interface opens details and a compact Unicode sparkline/history summary; full interactive graphs remain a web feature.

### 13.4 Failure behavior

- API unavailable: show connection failure and local `doctor` command hint; do not show cached state as current without a stale label.
- Telemetry backend unavailable: show metadata plus explicit telemetry unavailability.
- Collector unavailable: show last observation age and unknown state.
- Narrow terminal: hide lower-priority columns while preserving state, name, rate, and age.

---

## 14. API surface

The exact route layout may evolve without changing semantics, but the initial API shall cover:

```text
GET    /api/v1/version
GET    /api/v1/health/live
GET    /api/v1/health/ready
GET    /api/v1/health/detail
GET    /api/v1/devices
POST   /api/v1/devices
GET    /api/v1/devices/{device_uuid}
PATCH  /api/v1/devices/{device_uuid}
POST   /api/v1/devices/{device_uuid}/rediscovery
GET    /api/v1/devices/{device_uuid}/history/reachability
GET    /api/v1/devices/{device_uuid}/interfaces
GET    /api/v1/interfaces
GET    /api/v1/interfaces/{interface_uuid}
PATCH  /api/v1/interfaces/{interface_uuid}
GET    /api/v1/interfaces/{interface_uuid}/history/traffic
GET    /api/v1/interfaces/{interface_uuid}/history/errors
GET    /api/v1/interfaces/{interface_uuid}/identity-history
POST   /api/v1/interfaces/{interface_uuid}/identity-resolution
GET    /api/v1/sites
GET    /api/v1/tags
GET    /api/v1/groups
GET    /api/v1/policies
POST   /api/v1/policies/explain
GET    /api/v1/dashboards
POST   /api/v1/dashboards
GET    /api/v1/dashboards/{dashboard_uuid}
PATCH  /api/v1/dashboards/{dashboard_uuid}
POST   /api/v1/dashboards/{dashboard_uuid}/publish
GET    /api/v1/collectors
GET    /api/v1/collectors/{collector_uuid}/health
GET    /api/v1/operations/{operation_uuid}
GET    /api/v1/events
POST   /api/v1/imports/inventory
GET    /api/v1/exports/{operation_uuid}
GET    /api/v1/stream/current-state       # authenticated SSE
GET    /public/v1/dashboards/{opaque_slug}
GET    /public/v1/dashboards/{opaque_slug}/data
```

Administrative routes require admin authorization and audit events. Read endpoints enforce object visibility and query bounds.

---

## 15. Security architecture and threat controls

### 15.1 Trust boundaries

Trust boundaries include:

- browser/TUI/API clients;
- reverse proxy;
- API/web process;
- collector processes with network access;
- ICMP process with raw-socket capability;
- credential provider;
- metadata database;
- telemetry database;
- local durable spool;
- public dashboard endpoints;
- external integration readers.

Document a threat model covering credential theft, SQL/command injection, SSRF-like target abuse, public data leakage, session attacks, malicious regex/input, denial of service, spool tampering, dependency compromise, and privilege escalation.

### 15.2 Credential handling

- Device records store only credential references.
- Resolve credentials immediately before session creation and keep them in memory for the shortest practical time.
- Credential value objects shall implement redacted representations.
- Never include secrets in dataclass/model default `repr`.
- File provider shall require root/service ownership and restrictive permissions, with configurable strict rejection.
- Future Vault/secret-store providers implement the same resolver port.
- Rotation shall not require rewriting device records.

### 15.3 Process privilege separation

- API/web, ingestion, and maintenance run without raw-socket capability.
- By default no component receives `CAP_NET_RAW`; ICMP uses unprivileged datagram sockets (ICMP-012). Where the documented fallback is used, only the collector unit receives it.
- Services use dedicated users/groups and restrictive filesystem permissions.
- The spool directory is writable only by ingestion/collector identities that require it.
- Database credentials are scoped per service role.

### 15.4 Web controls

- Secure cookies, CSRF, content security policy, frame restrictions, MIME sniffing protection, referrer policy, and safe cache headers.
- HTML auto-escaping remains enabled.
- State-changing methods are not exposed as GET.
- Public pages use a separate template/context allowlist.
- Reverse-proxy header trust is explicit.
- No dynamic external script execution or public CDN dependency.

### 15.5 Network target guardrails

Because admins intentionally configure network destinations, polling is a controlled network-capable function. The platform shall nevertheless:

- validate address/port syntax;
- apply configured CIDR allow/deny policy;
- reject multicast/unspecified/broadcast targets;
- deny known metadata-service addresses by default;
- cap per-target and global request rates;
- record who added/changed a target;
- avoid invoking shell commands with target input;
- make any future device-CLI function a separately reviewed non-goal/extension.

---

## 16. Operations, packaging, backup, and recovery

### 16.1 Development and build tools

- Python, latest stable (3.15 at this revision), pinned in `.python-version`.
- Go, latest stable (1.27 at this revision), only for components admitted under section 24 and for build tooling such as `nfpm`.
- Astral `uv` (latest) for environment, dependency locking, build, and command execution.
- `ruff format` and `ruff check` (latest).
- `ty` (latest) for static type checking.
- `pytest` for tests.
- `nfpm` for `.deb` (and later `.rpm`) packaging from one spec (PKG-001).
- `tools/check-updates` to report newer Python, Go, tool, dependency, GitHub Action, and vendored-asset versions before each release (TOOLS-001).
- Property-based tests for calculation/reconciliation modules where valuable.
- A pinned front-end dependency lock or vendored artifact manifest.
- Secret, dependency, license, and SBOM tooling in CI.

Direct dependencies shall be pinned in `uv.lock`. Add a dependency only when it is maintained, narrowly scoped, licensed compatibly, and simpler/safer than implementing the behavior locally.

### 16.2 Packaging

Release artifacts shall include:

- the `.deb` package for Ubuntu 26.04 LTS amd64 (MVP), and later the `.rpm` (PKG-006);
- `SHA256SUMS` and its detached signature `SHA256SUMS.sig`, which self-update verifies (UPD-001, ADR-026);
- Python wheel and source distribution;
- locked dependency metadata;
- systemd unit files;
- sample configuration;
- migration bundles;
- static web assets;
- SBOM;
- checksums;
- release notes and upgrade instructions.

A development Compose environment may start PostgreSQL, TimescaleDB, ClickHouse, and simulators. Native systemd installation remains the production reference.

### 16.3 Startup preflight

Before normal discovery/polling, validate:

- configuration and references;
- service user and directory ownership;
- credential file permissions;
- spool writability, free space, and segment compatibility;
- metadata schema compatibility;
- telemetry schema compatibility;
- required database connectivity for the selected profile;
- collector UUID uniqueness;
- time synchronization health;
- ICMP capability;
- bind addresses/ports;
- static asset and template availability.

A failed preflight exits before discovery and writes a clear operator-facing error to stderr/journald without secrets.

### 16.4 Graceful shutdown

1. Mark service draining.
2. Stop scheduling new polls.
3. Allow active polls a configured grace period.
4. Cancel remaining network operations safely.
5. Convert completed results to canonical observations.
6. Flush bounded memory queues to durable spool.
7. Finish or checkpoint active batch writes.
8. Close network/database resources.
9. Exit within the systemd stop timeout.

### 16.5 Backup scope

Backups shall cover:

- PostgreSQL or SQLite metadata/control database;
- configuration files;
- protected credentials using an operator-controlled secure method;
- ClickHouse/TimescaleDB telemetry according to deployment policy;
- dashboard definitions and audit history;
- spool only when required during an active outage or migration.

Documentation shall state that a metadata backup without credentials cannot poll devices, and a credential backup requires stronger handling than ordinary metadata.

### 16.6 Restore and disaster recovery

Required runbooks:

- restore metadata to a clean installation;
- restore/reconnect telemetry storage;
- recover/replay valid spool segments;
- rebuild current-state projections;
- rotate lost credentials;
- replace a collector while preserving or intentionally changing collector identity;
- recover from partial migration;
- verify data and identity consistency after restore.

### 16.7 Upgrade policy

- Run validation and backup checks before migration.
- Stop or drain affected writers when required.
- Apply metadata and telemetry migrations in documented order.
- Preserve spool readability across supported upgrades.
- Run post-upgrade consistency and smoke tests.
- Publish downgrade limitations; destructive schema downgrades are not assumed possible.

---
## 17. Coding, maintainability, and teaching standards

### 17.1 Core design rules

- **DRY:** one canonical implementation for counter math, status derivation, policy evaluation, authorization, time conversion, repository mapping, and graph aggregation. Do not duplicate these rules in API, web, and TUI layers.
- **SRP:** modules, classes, and functions have one reason to change. Network transport, parsing, domain decisions, persistence, and presentation remain separate.
- **KISS:** prefer explicit data flow and small typed functions over metaprogramming, implicit global state, framework magic, or premature distributed infrastructure.
- Favor pure functions for calculations, reconciliation scoring, state derivation, policy matching, and aggregation.
- Use dependency injection through constructors/factories, not service-locator globals.
- Use explicit enums/value objects instead of stringly typed state.
- Use bounded queues/semaphores and context managers for resources.
- No blocking network or database calls on an asyncio event loop.
- Use monotonic clocks for deadlines/sleeps and UTC wall time for observations.
- Do not catch broad exceptions without re-raising, translating to a typed error, or recording a justified terminal outcome.
- Do not use exceptions for normal state transitions.
- Do not log and re-raise the same error at every layer; log once at the owning boundary with stable context.
- Use f-strings, modern type hints, `pathlib`, dataclasses/value objects where appropriate, and explicit return types.
- Avoid mutable default arguments, hidden I/O, and import-time side effects.

### 17.2 File size and complexity

- Authored production files under `src/` and `tools/` shall target no more than 300 physical lines, including docstrings and comments.
- A CI check shall fail files over 300 lines unless the path is listed in `docs/architecture/loc-exceptions.md` with reason, owner, decomposition analysis, and expiry/review milestone.
- Generated artifacts, vendored assets, and migration snapshots may be excluded but shall be clearly identified.
- Functions should normally remain under 50 lines and cyclomatic complexity at or below 10.
- A long function is a design smell even if its file is under 300 lines.
- Tests should also be split for readability; fixture data files may be larger.

### 17.3 Module-level docstrings

Every authored Python module shall start with a Google-style docstring containing the items below. Authored shell scripts, JavaScript modules, and other executable support scripts shall begin with an equivalent comment header covering purpose, network/operational context, dependencies, safety considerations, and a usage example.

Python module docstrings shall contain:

1. High-level purpose.
2. Real-world network engineering context.
3. Architectural boundary/responsibility.
4. Important dependencies and why they are used.
5. Safety/quality considerations where relevant.
6. A small usage example that is valid for the module’s intended audience.

Template:

```python
"""Collect and normalize ICMP reachability samples for managed devices.

This module is used by network monitoring workers to send a bounded group of
ICMP Echo probes to a router or switch management address. It converts raw
probe results into the canonical reachability observation consumed by storage,
web, and TUI components. Keeping transport details separate from latency math
allows the calculations to be tested without network access.

Dependencies:
    asyncio: Coordinates probe deadlines without blocking other device polls.
    vakr.telemetry: Provides the backend-neutral observation model.

Network safety:
    Target addresses are validated before a socket is opened, and probe
    concurrency is bounded so an inventory mistake cannot create an
    uncontrolled traffic burst.

Example:
    >>> result = await collector.collect(device_ip="192.0.2.10")
    >>> result.received_count
    5
"""
```

### 17.4 Function and method docstrings

Every authored function, async function, method, constructor with behavior, callback, and test helper shall have a detailed Google-style docstring. It shall explain behavior in plain language and include `Args`, `Returns`, and `Raises` sections. When a section has no entries, state `None` rather than omitting it. Generator/coroutine behavior shall be explained.

Network-facing argument descriptions shall state the operational meaning, for example:

```python
def calculate_rate(
    current_octets: int,
    previous_octets: int,
    elapsed_seconds: float,
) -> float:
    """Calculate an interface traffic rate from two valid SNMP counters.

    The function converts the change in a router or switch interface octet
    counter into bits per second. Callers must validate reboot, discontinuity,
    and counter-width conditions before using this function so a reset is not
    rendered as a bandwidth spike.

    Args:
        current_octets (int): Current cumulative octet counter read from the
            monitored network interface.
        previous_octets (int): Prior cumulative octet counter for the same
            persistent interface identity and collector.
        elapsed_seconds (float): Actual time between the two successful SNMP
            observations, not the configured polling interval.

    Returns:
        float: Calculated traffic rate in bits per second.

    Raises:
        ValueError: If the elapsed time is not positive or a counter is
            negative.
    """
```

Docstrings shall not claim an exception that the function cannot raise, and they shall be updated with behavior changes.

### 17.5 Class and data-model docstrings

Every public class, protocol, enum, exception, and dataclass shall document:

- its network/domain meaning;
- ownership/lifecycle;
- important invariants;
- whether instances are mutable;
- units and sentinel/null semantics for fields;
- thread/async safety where relevant.

### 17.6 “What and why” comments

Inline comments shall explain non-obvious intent and guardrails, not narrate syntax.

Good examples:

```python
# Use the persistent interface UUID rather than ifIndex so a router reboot that
# renumbers interfaces does not split one circuit's history into two graphs.
```

```python
# Bound per-device SNMP concurrency to one PDU because many access switches have
# weak agents that time out or drop replies when parallel requests arrive.
```

```python
# The context manager closes the SNMP transport even when a timeout is raised,
# preventing file-descriptor leaks during a widespread network outage.
```

```python
# Normalize surrounding whitespace from operator-managed inventory before
# validation so an accidental trailing space does not become a different DNS
# name or credential reference.
```

Bad examples:

```python
# Loop over devices.
# Add one to the counter.
# Return the result.
```

Comments shall explain why `try/except`, a context manager, a bounded queue, a lock, a data structure, or a validation step matters to network reliability or software correctness.

### 17.7 Error handling

- Network timeouts, connection drops, malformed SNMP values, partial responses, and database outages are expected typed outcomes.
- Catch the narrowest practical exception at the boundary that can make a domain decision.
- Translate library errors into stable internal error codes without losing safe diagnostic context.
- Preserve successful partial data.
- Never include credential objects or raw secret-bearing command/config text in exceptions.
- Retry only idempotent operations with bounded attempts, deadlines, and jitter/backoff where appropriate.
- Use context managers to close sockets, files, database sessions, and temporary resources.

### 17.8 Type, lint, and code-quality gates

Required CI gates:

- `ruff format --check`
- `ruff check`
- `ty check`
- docstring presence/style check
- source line-count check
- cyclomatic-complexity check
- `pytest` test suites by layer
- coverage report
- secret scan
- dependency vulnerability scan
- license policy scan
- SBOM generation validation
- configuration example validation
- migration tests
- documentation link/example tests

Overall line coverage shall be at least 90%. Critical pure logic—counter math, interface reconciliation, scheduling calculations, policy precedence, auth decisions, and spool replay—shall target at least 95% branch coverage. Coverage numbers do not replace scenario-based tests.

### 17.9 Logging rules

Structured log records shall include, when applicable:

- stable event ID;
- component;
- collector/worker/device/interface/poll/operation UUIDs;
- safe target descriptor;
- attempt number;
- duration;
- outcome/quality;
- request ID;
- exception class and redacted message.

Do not log every successful metric or varbind at normal levels. DEBUG logging must remain bounded and redact secrets.

### 17.10 Educational codebase requirements

The repository shall include:

- a glossary of SNMP, ICMP, counters, rates, jitter, rollups, and scheduling terms;
- architecture diagrams and data-flow explanations;
- a “build a small collector” tutorial using the same domain models;
- deterministic network simulators and sample captures/fixtures;
- annotated examples of asyncio concurrency and cancellation;
- tutorials explaining counter wrap/discontinuity and interface identity;
- a Lite-profile lab that runs without enterprise databases;
- exercises with expected tests rather than hidden production-only knowledge;
- documentation that distinguishes protocol evidence from assumptions.

---

## 18. Documentation requirements

### 18.1 Required documents

- `README.md` — mission, quick start, scope, screenshots, supported profiles.
- `docs/architecture/overview.md` — processes, boundaries, data flow.
- `docs/architecture/storage.md` — repository ports and backend semantics.
- `docs/architecture/scheduler.md` — phases, cadence, no-overlap behavior.
- `docs/architecture/identity.md` — device/interface identity and reconciliation.
- `docs/architecture/quality-states.md` — missing, stale, partial, unknown, and failure semantics.
- `docs/operations/install.md` — supported Ubuntu installation.
- `docs/operations/configuration.md` — every option, default, precedence, ownership, and reload behavior.
- `docs/operations/credentials.md` — provider, permissions, rotation, redaction.
- `docs/operations/systemd.md` — units, lifecycle, logs, limits.
- `docs/operations/backup-restore.md` — all supported backends.
- `docs/operations/upgrade.md` — migrations and rollback limitations.
- `docs/operations/capacity.md` — scale model, storage sizing, metrics, warning signs.
- `docs/operations/troubleshooting.md` — device versus collector versus database failures.
- `docs/api/` — OpenAPI, examples, pagination, history semantics, public API.
- `docs/oid-catalog/` — OIDs, types, stages, fallback, caveats.
- `docs/tutorials/` — teaching labs.
- `docs/adr/` — decisions.
- `docs/traceability.md` — requirement -> code -> tests -> docs.
- `SECURITY.md` — reporting and supported security releases.
- `CONTRIBUTING.md` — setup, style, testing, docstring/comment standards.

### 18.2 ADR format

Every ADR shall include:

- title/status/date;
- current requirement/context;
- decision;
- alternatives;
- rationale;
- benefits;
- risks;
- security impact;
- performance impact;
- compatibility/migration impact;
- test changes;
- documentation changes;
- rollback/revisit criteria.

### 18.3 Operator-language requirements

Documentation and UI text shall explicitly explain:

- reachability versus SNMP availability;
- management uptime versus chassis uptime;
- current state versus stale/unknown state;
- device failure versus collector failure;
- missing data versus zero;
- native versus rollup resolution;
- capacity denominator used for utilization;
- why a rate is unavailable after a reset/discontinuity;
- why an interface is suppressed;
- why public dashboards expose less information.

---
## 19. Required test plan and progress gates

A test is mandatory when marked in the tables below. Tests shall be deterministic unless explicitly classified as performance/endurance. Simulator seeds, time sources, and database fixtures shall be reproducible. A requirement is not complete until its mapped tests pass in CI and, where applicable, on the supported Ubuntu validation hosts.

### 19.1 Foundation, code quality, and documentation tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| QG-001 | CI | Format and lint all authored Python | `ruff format --check` and `ruff check` pass with no unexplained global ignores | Coding 17.1, 17.8 |
| QG-002 | CI | Static typing | `ty check` passes for production and typed test support code | ARCH-001, Coding 17.8 |
| QG-003 | CI | Module/function/class docstrings | Every authored module and function/method/class has required Google-style sections; examples parse | Sections 17.3-17.5 |
| QG-004 | CI | Production file length | No unapproved authored `src/` file exceeds 300 physical lines | Section 17.2 |
| QG-005 | CI | Complexity | No unapproved function exceeds configured complexity; report is stored as artifact | Section 17.2 |
| QG-006 | CI | Unit/branch coverage | Overall >=90% line coverage and critical modules >=95% branch coverage | Section 17.8 |
| QG-007 | CI | Secret scan | Repository history/current tree and test artifacts contain no real secrets | SEC-003, SEC-017 |
| QG-008 | CI | Dependency/license/SBOM | No unapproved critical vulnerability or incompatible license; SBOM is generated | SEC-017 |
| QG-009 | Docs | Documentation links/examples | Internal links resolve; shell/Python/TOML examples validate or execute in test mode | DOC requirements |
| QG-010 | Build | Clean locked install | A clean Ubuntu runner installs from lockfile and builds wheel/sdist reproducibly | PROD-005, Section 16 |
| QG-011 | Packaging | Release artifact contents | Units, migrations, static assets, sample config, SBOM, checksums, and notices are present | Section 16.2 |
| QG-012 | Traceability | Requirement mapping | Every mandatory requirement maps to at least one code location, test, and document or an approved deferred decision | Section 1, completion criteria |

### 19.2 Configuration, inventory, credential, and security tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| CFG-001 | Unit | Configuration precedence | Defaults, base file, lexical `conf.d`, allowed environment, and CLI resolve in documented order; source is inspectable | CONF-006 |
| CFG-002 | Integration | Invalid reload | Invalid candidate is rejected; active configuration and healthy processes remain unchanged | CONF-003, CONF-004, TEST-008 |
| CFG-003 | Integration | Atomic live reload | Reloadable settings change together or not at all; poll scheduling remains coherent | CONF-003, CONF-010 |
| CFG-004 | Unit | Reference integrity | Missing site, credential, policy, dashboard, group, or device references fail with field/location detail | CONF-009 |
| CFG-005 | Unit | Pattern safety | Invalid glob/regex, oversized pattern, or match timeout is rejected/contained | POLICY-005, SEC-019 |
| CFG-006 | Integration | File/API ownership conflict | API cannot modify file-managed fields; duplicate source ownership fails validation | META-008, CONF-008 |
| CFG-007 | Integration | File removal behavior | Removing a file-managed device disables rather than deletes it; explicit prune dry-run lists history impact | META-009, CONF-008 |
| CFG-008 | CLI | Config validation | `vakr config validate` checks syntax, semantics, permissions, paths, and selected backend combination without polling | CONF-009 |
| CFG-009 | CLI | Policy explain | Given device/interface fixtures, explain output identifies every matched rule and final action deterministically | POLICY-004, POLICY-006 |
| CFG-010 | Import | Inventory dry-run | CSV/TOML import reports creates/updates/conflicts/errors without changing storage | META-010 |
| CFG-011 | Import | Idempotent import | Reapplying the same import creates no duplicates or unintended changes | META-010 |
| CRED-001 | Security | Credential file permissions | Unsafe owner/mode is rejected in strict production mode and clearly warned in development mode | SEC-002, SEC-016 |
| CRED-002 | Security | Redaction corpus | Secrets do not appear in repr, logs, exceptions, API, diagnostics, audit, or support bundle under forced failures | SEC-003, SEC-020 |
| AUTH-001 | Unit | Role authorization matrix | Admin/operator/anonymous decisions match every protected use case | SEC-005, SEC-006, UI-007 |
| AUTH-002 | Integration | Session security | Login rotates session, cookies have required flags, idle/absolute expiry and logout/revocation work | SEC-009, SEC-010 |
| AUTH-003 | Security | CSRF | Missing/invalid CSRF blocks every browser state-changing operation | SEC-011 |
| AUTH-004 | Security | Login throttling | Repeated failures trigger configured rate limit/progressive delay and audit without leaking account existence | SEC-012 |
| AUTH-005 | Security | Public dashboard isolation | Anonymous client cannot enumerate private dashboards, private devices, hidden fields, or authenticated links | SEC-014, DASH-008, DASH-009 |
| AUTH-006 | Security | Forwarded-header trust | Spoofed headers from untrusted source do not alter client scheme/IP/auth assumptions | SEC-013 |
| NETSEC-001 | Security | Target address policy | Denied CIDRs, multicast, unspecified, broadcast, and metadata-service targets are rejected unless explicitly allowed | SEC-015 |
| NETSEC-002 | Security | Command injection guard | Malicious address/name/OID/pattern values never reach a shell; validation returns safe error | SNMP-019, SEC-019 |
| AUDIT-001 | Integration | Administrative audit | All required admin/security actions create complete redacted audit events with actor, object, result, request ID | SEC-018 |

### 19.3 Metadata and telemetry adapter contract tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| DB-001 | Contract | Metadata CRUD parity | SQLite and PostgreSQL adapters produce equivalent domain behavior for devices, interfaces, sites, tags, groups, policies, and dashboards | STORE-001, ARCH-001 |
| DB-002 | Contract | Transaction rollback | Failed unit of work commits no partial metadata changes on SQLite or PostgreSQL | Section 5.2 |
| DB-003 | Contract | Optimistic conflict | Concurrent metadata edits produce a defined conflict rather than silent overwrite | META-008, Audit |
| DB-004 | Contract | Telemetry write/query parity | SQLite, TimescaleDB, and ClickHouse preserve IDs, nulls, sentinels, quality, gaps, and ordering | STORE-002, STORE-013 |
| DB-005 | Contract | Current-state semantics | Each backend selects the same latest valid observation and reports age/quality | STATE-001, STATE-003 |
| DB-006 | Contract | Native/rollup boundary | Query spanning day-30 boundary returns ordered non-duplicated points and discloses resolution | STATE-005, STORE-012 |
| DB-007 | Contract | Public identifiers | No API read model exposes backend row IDs | API-002 |
| DB-008 | SQLite | WAL/single-writer contention | Concurrent readers plus bounded writer complete without corruption or unbounded lock failures | Section 5.3 |
| DB-009 | SQLite | 24-hour retention | Lite purge removes only expired telemetry, preserves metadata/current behavior, and reports reclaimed range | Lite profile, RET-007 |
| DB-010 | Timescale | Hypertable/retention/compression | Migrations create expected hypertables/policies and queries preserve required semantics | Section 5.4 |
| DB-011 | ClickHouse | Bulk insert shape | Enterprise writer uses batches, not per-metric writes; part/insert counts stay within benchmark limits | STORE-003, STORE-011 |
| DB-012 | ClickHouse | Latest-state rebuild | Drop/rebuild projection from authoritative observations yields equivalent current state | STATE-002 |
| DB-013 | Integration | Read-only roles | Reader/integration roles cannot mutate; writer cannot perform migration/admin operations | SEC-008 |
| MIG-001 | Migration | Fresh install | Every selected profile migrates empty databases to current schema and starts | Section 5.6 |
| MIG-002 | Migration | Supported upgrade paths | Each supported prior schema upgrades with preserved IDs/data and documented duration | Section 5.6 |
| MIG-003 | Migration | Interrupted migration | Restart detects/resumes or clearly blocks partial migration without silent corruption | Section 5.6 |
| MIG-004 | Migration | Future schema | Older binary refuses unsupported future schema with actionable message | Section 5.6 |
| MIG-005 | Migration | Destructive gate | Destructive operation requires backup confirmation and explicit operator action | Section 5.6 |

### 19.4 ICMP, SNMP, rate, uptime, and discovery tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| ICMP-T01 | Unit | Five successful probes | Raw RTTs and min/avg/median/max/jitter/loss are exact | ICMP-001 through ICMP-010 |
| ICMP-T02 | Unit | 2/5 success | `-1` excluded; SNMP eligible in default strategy; loss and state correct | ICMP-004 through ICMP-008 |
| ICMP-T03 | Unit | 1/5 success | Observation persists; default SNMP is skipped; summary nullable where required | ICMP-007, ICMP-010 |
| ICMP-T04 | Unit | 0/5 success | Five `-1` values, 100% loss, no latency/jitter summary, correct state | ICMP-004, ICMP-005 |
| ICMP-T05 | Integration | `snmp_fallback` | 0/5 ICMP followed by SNMP success remains ICMP failed/impaired but SNMP reachable | ICMP-013, SNMP-013 |
| ICMP-T06 | Integration | IPv4 and IPv6 | Both families collect with source/target context and equivalent math | ICMP-011 |
| ICMP-T07 | Security | Capability isolation | Only ICMP service has raw-socket capability; API/web cannot open raw socket | ICMP-012, SEC-004 |
| SNMP-T01 | Simulator | SNMPv2c discovery | Mandatory system/interface/IP fields are parsed and stored with completeness | SNMP-001, DISC-004 through DISC-008 |
| SNMP-T02 | Simulator | SNMPv3 authPriv discovery | SHA-2/AES fixture succeeds; secrets are absent from diagnostics | SNMP-002, SNMP-003, SEC-003 |
| SNMP-T03 | Simulator | Stage 1 exact OIDs | Known interfaces use exact state OIDs and no full IF-MIB walk | SNMP-004, SNMP-005 |
| SNMP-T04 | Simulator | Stage 2 eligibility | Only current up/up interfaces receive counter requests | SNMP-006 |
| SNMP-T05 | Simulator | HC preferred/fallback | HC counters are used when available; 32-bit fallback used and recorded when not | SNMP-007, SNMP-010 |
| SNMP-T06 | Unit/Simulator | Packet aggregation | Six component counters produce exact aggregate in/out totals; components are not persisted | SNMP-008 |
| SNMP-T07 | Simulator | Unsupported OID memory | Unsupported capability is cached and not queried each cycle; re-probe path works | SNMP-010, DISC-009 |
| SNMP-T08 | Simulator | Timeout and one retry | Attempt count, latency, timeout state, and final quality match configured budget | SNMP-011, SNMP-018 |
| SNMP-T09 | Simulator | Partial response | Successful varbinds persist and missing fields are individually quality flagged | SNMP-014 |
| SNMP-T10 | Simulator | tooBig/fragmentation | Batch is reduced and retried within budget without per-device parallel PDUs | SNMP-012, SNMP-017 |
| SNMP-T11 | Unit | Transport/config normalization | Port, address family, context, and source address validate and reach adapter correctly | SNMP-016 |
| SNMP-T12 | Async | Event-loop non-blocking | Slow SNMP fixture does not block independent timers/tasks beyond threshold | SNMP-015, OBS-007 |
| DISC-T01 | Integration | Initial discovery | Device without inventory receives discovery and persistent interface UUIDs | DISC-001, META-002 |
| DISC-T02 | Integration | Scheduled rediscovery | 24-hour default and override schedule produce correct due times | DISC-002 |
| DISC-T03 | Integration | Manual rediscovery operation | API returns operation ID immediately; progress/result and audit are correct | DISC-003, DISC-010 |
| DISC-T04 | Simulator | Partial IP-MIB failure | Interface inventory remains; address completeness is partial, not empty success | DISC-008, SNMP-014 |
| DISC-T05 | Integration | Suppressed interface discovery | Suppressed interface remains in inventory with effective rule explanation | DISC-007, POLICY-004 |
| UP-T01 | Unit | Continuous uptime after gap | Prior uptime + elapsed time yields `CONTINUOUS` within tolerance | UPTIME-002 |
| UP-T02 | Unit | Management reset | Lower/inconsistent uptime yields `RESET_DETECTED` with evidence | UPTIME-002, UPTIME-005 |
| UP-T03 | Unit | TimeTicks wrap | Legitimate wrap is classified without false restart | UPTIME-004 |
| RATE-T01 | Unit | Normal 64-bit rate | Actual elapsed time yields exact bps/pps/error rate | RATE-001, RATE-006, RATE-007 |
| RATE-T02 | Unit | First sample | Raw counters stored; all derived rates unavailable; baseline established | RATE-003, RATE-004 |
| RATE-T03 | Unit | Counter discontinuity/reboot | Rate unavailable and no spike | RATE-003, RATE-004 |
| RATE-T04 | Unit | Plausible 32-bit wrap | Correct delta accepted only with consistent evidence | RATE-005 |
| RATE-T05 | Unit | Implausible decrease | Baseline invalidated; no wrap/spike | RATE-003, RATE-005 |
| RATE-T06 | Unit | Excessive sample gap | Rate unavailable and quality reason recorded | RATE-003 |
| RATE-T07 | Unit | Counter family switch | HC/legacy change invalidates one interval | RATE-003 |
| RATE-T08 | Unit | Utilization denominator | Override/contract/ifHighSpeed/ifSpeed precedence and label are correct | RATE-008, RATE-010 |
| RATE-T09 | Unit | Implausible over-capacity | Value retained and quality flagged according to policy, not silently clipped | RATE-009 |

### 19.5 Interface identity and lifecycle tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| IFID-T01 | Unit | Unchanged interface | Same UUID retained with exact outcome | IFID-001 through IFID-003 |
| IFID-T02 | Unit | ifIndex renumber | Strong name/MAC/IP evidence retains UUID and records prior index | IFID-001, IFID-005 |
| IFID-T03 | Unit | Description/alias change | Cosmetic metadata change does not split identity | IFID-002 |
| IFID-T04 | Unit | Duplicate/invalid MAC | MAC is weakened/ignored; no false merge | IFID-008 |
| IFID-T05 | Unit | Ambiguous candidates | No auto-merge; review record created with evidence | IFID-004, IFID-007 |
| IFID-T06 | Unit | Probable candidate | Historical data remains separate until admin confirms | IFID-003, IFID-004 |
| IFID-T07 | Integration | Admin resolution | Confirm/new decision updates mapping/history and creates audit event | IFID-007, SEC-018 |
| IFID-T08 | Integration | Interface disappearance/reappearance | Missing lifecycle/history retained; returning interface reconciles correctly | LIFE-005, IFID-001 |
| IFID-T09 | Unit | Candidate tie/margin | Unique-best and runner-up margin are enforced deterministically | IFID-006 |
| IFID-T10 | Regression | Versioned device corpus | Physical/logical interface fixture corpus produces approved stable outcomes | IFID-009 |
| LIFE-T01 | Unit | Active -> inactive -> stale | Thresholds and polling eligibility transition correctly | LIFE-001, LIFE-003, LIFE-004 |
| LIFE-T02 | Integration | Retire and history | Retired interface leaves default polling but all history remains queryable | LIFE-002, RET-007 |
| LIFE-T03 | UI/TUI | Sticky ordering | Sticky/high-priority/WAN ordering is consistent across web, API read model, and TUI | LIFE-007, UI-006, TUI-006 |

### 19.6 Scheduler, queue, spool, and ingestion tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| SCHED-T01 | Unit | Hostname phase determinism | Same short hostname/config yields same phase; DNS suffix is excluded | SCHED-001, SCHED-003 |
| SCHED-T02 | Unit | Fixed and hash modes | All four modes produce documented independent phase/order behavior | SCHED-002 through SCHED-007 |
| SCHED-T03 | Unit | Anchored cadence | Poll completing late still has next due based on intended slot | SCHED-010 |
| SCHED-T04 | Unit | Monotonic wall-clock step | Forward/backward wall-clock change does not create drift or duplicate polls | SCHED-011, SCHED-018 |
| SCHED-T05 | Integration | No overlap | Slow poll crossing next due creates `MISSED_OVERRUN`; no second target session starts | SCHED-012 |
| SCHED-T06 | Integration | Mixed intervals | 60/120/300-second devices interleave correctly in one worker pool | SCHED-013, SCALE-003 |
| SCHED-T07 | Performance | Interval distribution | Start times are spread across interval and do not form an avoidable boundary herd | SCHED-008 |
| SCHED-T08 | Unit | Poll dependency chain | Stage 2 plan is conditional on current Stage 1, not stale static up state | SCHED-009 |
| SCHED-T09 | Integration | Worker affinity/rebalance | Stable assignment persists; controlled rebalance has no duplicate active poll | SCHED-014 |
| SCHED-T10 | Integration | Capacity not attempted | Work rejected by capacity is `NOT_ATTEMPTED`/unknown and not device down | SCHED-017, OBS-003 |
| SCHED-T11 | Unit | Estimated cost ordering | Higher-cost fixtures influence assignment without starving low-cost devices | SCALE-004, SCHED-015 |
| ADAPT-T01 | Unit | Disabled default | No interval changes occur when diagnostics show slow devices but adaptive is off | ADAPT-001, ADAPT-002 |
| ADAPT-T02 | Unit | Degrade ladder | Repeated qualifying target conditions produce 60->120->300 with reasons | ADAPT-003, ADAPT-004 |
| ADAPT-T03 | Unit | Recovery hysteresis | One good poll does not restore 60; sustained health steps 300->120->60 | ADAPT-003, ADAPT-007 |
| ADAPT-T04 | Integration | Collector overload | Global overload degrades collector and does not penalize individual target interval/state | ADAPT-006, OBS-003 |
| QUEUE-T01 | Unit | Bounded queue | Producer blocks/backs off at configured capacity; memory remains bounded | STORE-004, Architecture 6.4 |
| SPOOL-T01 | Unit | Segment write/read | Versioned length-delimited records round-trip with checksum validation | Architecture 6.3 |
| SPOOL-T02 | Failure | Torn/corrupt tail | Restart recovers complete records, quarantines corruption, and reports affected range | STORE-006, SVC-005 |
| SPOOL-T03 | Integration | ClickHouse outage | Polling continues, observations reach durable spool, and health shows backlog | STORE-014, TEST-006 |
| SPOOL-T04 | Integration | Recovery replay | Oldest valid batches replay after recovery and backlog reaches zero | STORE-006, STORE-009 |
| SPOOL-T05 | Failure | Uncertain commit | Replayed batch produces no unintended logical duplicate | STORE-009, STORE-010 |
| SPOOL-T06 | Failure | Process kill after fsync | Committed-to-spool records survive abrupt process termination | STORE-006 |
| SPOOL-T07 | Capacity | Warning/critical/exhausted | Threshold states and operator events are correct; exhausted pauses normal polling | Architecture 6.4 |
| SPOOL-T08 | Failure | Unavoidable loss | Explicit telemetry-loss incident identifies range/classes/estimate | STORE-007 |
| INGEST-T01 | Integration | Time threshold flush | Small workload flushes within configured maximum latency | STORE-011 |
| INGEST-T02 | Integration | Row threshold flush | Large workload flushes at row limit without oversized memory | STORE-011 |
| INGEST-T03 | Integration | Partial backend failure | Failed batch remains replayable; acknowledged batches are not replayed unnecessarily | STORE-010 |
| INGEST-T04 | Performance | Batch efficiency | Enterprise benchmark meets insert/part/latency thresholds with measured row/byte batches | STORE-003, STORE-011 |
| ROLL-T01 | Unit | Interface p95/max rollup | Known samples produce exact max/p95 and coverage counts | RET-004 |
| ROLL-T02 | Unit | ICMP aggregate loss | Loss is derived from summed sent/received, not averaged percentages | RET-006, RET-008 |
| ROLL-T03 | Integration | Restart-safe rollup | Interrupted job resumes without missing or duplicate buckets | RET-009 |
| ROLL-T04 | Integration | TTL/retention | Native expires only after rollup coverage exists according to policy | RET-001 through RET-003 |
| ROLL-T05 | Query | Gap preservation | Missing samples remain visible as coverage gaps in rollups/graphs | STORE-012 |

### 19.7 API, web, dashboard, and TUI tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| API-T01 | Contract | OpenAPI schema | Required routes/models/errors exist and schema artifact matches runtime | API-001, API-008 |
| API-T02 | Contract | Pagination/sort/filter | Stable page traversal has no duplicates/missing items under fixed snapshot | API-005 |
| API-T03 | Contract | Error envelope | Validation/auth/not-found/conflict/backend errors use stable redacted envelope and request ID | API-007 |
| API-T04 | Contract | Bounded history | Oversized range/point request is rejected or reduced per documented rule; response reports resolution | API-006, SEC-019 |
| API-T05 | Contract | UUID-only public contracts | No database IDs leak in JSON, URLs, or OpenAPI examples | API-002 |
| API-T06 | Integration | Async admin operation | Rediscovery/import/export returns operation ID and observable completion/failure | API-011 |
| API-T07 | Integration | SSE authorization/reconnect | Authorized stream resumes safely; unauthorized/public client is denied | API-009 |
| API-T08 | Contract | Read-only integration | Integration credentials can read supported views/endpoints but cannot mutate | API-003, SEC-008 |
| API-T09 | Export | CSV/JSON export | Data, timezone, units, quality, and bounds are correct; large export is asynchronous | API-012 |
| WEB-T01 | UI | Device default ordering | Attention devices appear first, Active next, Offline/Retired last; ties follow rules | UI-005 |
| WEB-T02 | UI | Device detail completeness | Required system/reachability/SNMP/uptime/interface/freshness information renders | UI-003 |
| WEB-T03 | UI | Interface sticky order | Circuit/sticky interfaces render first and detail links preserve filters | UI-006 |
| WEB-T04 | UI | Traffic graph gaps/reset | Missing and invalid-baseline intervals are gaps/markers, never zero/spikes | UI-010, RATE-004 |
| WEB-T05 | UI | Latency smoke graph | Individual RTT distribution and packet loss match source data | ICMP-009 |
| WEB-T06 | UI | Resolution/timezone | Range switch uses correct native/rollup source and configured timezone | TIME-003, STATE-005 |
| WEB-T07 | UI | Partial/backend failure states | Pages distinguish stale, partial, collector unavailable, and DB unavailable | UI-014, UI-015 |
| WEB-T08 | Accessibility | Keyboard and semantics | Automated checks pass and manual keyboard workflow reaches all core functions | UI-011 |
| WEB-T09 | Accessibility | Non-color state | State remains unambiguous in grayscale/color-blind simulation and screen-reader labels | UI-011 |
| WEB-T10 | Security | Local assets/CSP | No external runtime asset requests; CSP and headers pass assertions | UI-012, SEC-013 |
| WEB-T11 | UI/Performance | Device interface graph view | Compact graph cards render in required order, lazy-load in bounded pages, and do not issue one unbounded query for all interfaces | UI-016, UI-006 |
| DASH-T01 | Unit | Top utilization ranking | Default 15-minute p95 directional utilization and denominator are exact | DASH-003, RATE-008 |
| DASH-T02 | Unit | Top error ranking | Valid error deltas/ppm rank correctly; raw cumulative values never rank | DASH-004 |
| DASH-T03 | Unit | Incomplete coverage | Ranking excludes/labels invalid baselines and partial coverage | DASH-005 |
| DASH-T04 | UI | Provider/WAN/site grouping | Fixtures appear in correct groups and filters | DASH-006 |
| DASH-T05 | Security | Public dual eligibility | Dashboard publication plus interface eligibility are both required | DASH-008 |
| DASH-T06 | Load | Query bounds/cache | Public/kiosk refresh stays within rate/point/object limits and cache behavior | DASH-010, UI-013 |
| TUI-T01 | UI | Default device screen | Ordering, state text/icons, search/filter/sort, and refresh work | TUI-001 through TUI-003 |
| TUI-T02 | UI | Device/interface detail | Required summary and interface metrics render with sticky interfaces first | TUI-004 through TUI-006 |
| TUI-T03 | UI | 120x30 and narrow terminal | Core data remains usable at 120x30; narrower screen degrades predictably | TUI-008 |
| TUI-T04 | Failure | API/telemetry/collector outage | Correct stale/unavailable messages appear; cached data is never presented as fresh | TUI-009 |
| TUI-T05 | Architecture | No direct SQL | Static/contract test confirms TUI depends only on query/application ports/API | TUI-007, ARCH-001 |

### 19.8 Service, backup, upgrade, and observability tests

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| SVC-T01 | System | systemd lifecycle | Start/stop/restart/reload/status/enable/disable work through top-level target | SVC-001, SVC-002 |
| SVC-T02 | System | Startup preflight failure | Unwritable spool, unsafe secrets, schema mismatch, or bad config fails before discovery with safe error | SVC-005 |
| SVC-T03 | System | Graceful shutdown | No new polls start; completed data reaches spool; resources close within timeout | SVC-003 |
| SVC-T04 | System | Abrupt collector restart | Stable collector identity and no overlapping duplicate poll ownership after recovery | MPOL-006 |
| SVC-T05 | System | Service restart policy | Repeated fatal startup failure does not enter uncontrolled rapid restart loop | SVC-009 |
| LOG-T01 | Integration | Structured event schema | Required identifiers/event codes are present and secret fields are absent | SVC-006, SEC-003 |
| OBS-T01 | Integration | Metrics completeness | Required inventory, protocol, scheduler, queue, spool, DB, CPU/memory, and latency metrics exist | OBS-001, OBS-004, OBS-007 |
| OBS-T02 | Integration | Prometheus protection | Metrics bind/protection defaults prevent unintended public exposure | OBS-005 |
| OBS-T03 | Failure | Collector overload semantics | UI/API/metrics show collector degraded and target state unknown/not attempted | OBS-002, OBS-003 |
| OBS-T04 | Failure | Collector outage | Existing targets are unknown from that collector, not down | MPOL-002 |
| OBS-T05 | Integration | Support bundle | Bundle includes useful diagnostics and passes redaction allowlist test | OBS-008, SEC-020 |
| BAK-T01 | Operations | Metadata backup/restore | Restored metadata preserves UUIDs, users, dashboards, policies, and identity history | SVC-008 |
| BAK-T02 | Operations | Telemetry backup/restore | Selected backend restores queryable native/rollup observations and current projection can rebuild | SVC-008, STATE-002 |
| BAK-T03 | Operations | Spool disaster recovery | Valid uncommitted segments replay after clean restore; corrupt segments are isolated/reported | STORE-006 |
| BAK-T04 | Operations | Credential rotation after restore | Rebound credential provider resumes polling without device-record rewrite | SEC-001, credentials architecture |
| UPG-T01 | Operations | In-place supported upgrade | Backup, migration, service start, smoke tests, and data checks succeed | Section 16.7 |
| UPG-T02 | Operations | Unsupported downgrade | Tool refuses unsafe downgrade with clear recovery path | Section 16.7 |
| DOC-T01 | Acceptance | New operator runbook | A tester unfamiliar with code installs Lite profile, imports fixtures, views graphs, and diagnoses a simulated failure using docs only | Teaching/Docs |
| DOC-T02 | Acceptance | Developer tutorial | A learner implements a small tested calculation/adapter change while following repository documentation | Educational goals |

### 19.9 Formal enterprise acceptance tests

These retain the canonical `TEST-*` IDs.

| Test ID | Scenario | Required pass condition |
|---|---|---|
| TEST-001 | Reference workload | 4,000 devices and 100,000 monitored interfaces at 60-second default polling execute in the Enterprise profile using representative simulators. |
| TEST-002 | Schedule accuracy | At least 99% of healthy-device polls start within approximately +/-5 seconds of scheduled slot. |
| TEST-003 | Cadence/no overlap | No systematic drift and no overlapping polls against the same device. |
| TEST-004 | Poll completion | Healthy-device completion p95 <15 seconds and p99 <30 seconds. |
| TEST-005 | Observation-to-query latency | Normal <10 seconds; p95 <15 seconds; p99 <30 seconds. |
| TEST-006 | Controlled ClickHouse outage | Polling continues while spool capacity permits; data spools durably; replay succeeds; no silent loss or unintended logical duplicates. |
| TEST-007 | Failure corpus | Covers ICMP 0/5, 1/5, 2/5, intermittent loss; SNMP timeout, partial, unsupported OID; reboot/uptime reset; wrap/discontinuity; ifIndex renumber; disappearance/reappearance; persistence outage/recovery. |
| TEST-008 | Invalid reload | Candidate is rejected without terminating healthy running processes or replacing active configuration. |
| TEST-009 | Observable capacity | Metrics prove the workload is not operating at an unobservable saturation point; p50/p95/p99, queues, event loop, spool, DB, CPU, and memory are captured. |
| TEST-010 | Public dashboard isolation | Under load and malicious enumeration attempts, anonymous users can retrieve only explicitly published allowlisted data. |
| TEST-011 | Multi-backend semantics | Representative query/result fixtures are equivalent across SQLite, TimescaleDB, and ClickHouse for shared behavior. |
| TEST-012 | 24-hour endurance | Enterprise profile runs for 24 hours at reference workload without unbounded memory/file-descriptor growth, cadence drift, silent data loss, or projection divergence. |
| TEST-013 | Recovery endurance | A prolonged telemetry-backend outage fills a meaningful spool backlog, then replay catches up without starving current ingestion beyond documented bounds. |
| TEST-014 | Backup/restore acceptance | A clean environment restores metadata and representative telemetry, rebuilds current state, and resumes collection with stable identities. |
| TEST-015 | Accessibility acceptance | Core authenticated and public workflows pass automated WCAG checks and documented manual keyboard/screen-reader review. |

---
## 20. Vertical implementation roadmap

Each phase produces a runnable increment, tests, documentation, and traceability updates. A later phase may begin only when its hard dependencies pass. Work may be parallelized only across truly independent components.

**MVP sequencing (ADR-022).** Until MVP acceptance (section 27.6), work follows the milestone order in section 27.8. That order builds a thin walking skeleton through every layer, with the release, update, and field-data tooling first, and then thickens it. The phases below remain the full-v1 map: each MVP milestone delivers the MVP-scoped portion of the phases it names. Phases or parts of phases outside the MVP scope (section 27.4) resume after MVP.

| Phase | Outcome/demo | Mandatory gates before completion |
|---:|---|---|
| 1. Repository and CI foundation | Clean `uv` install; package/CLI starts; CI enforces formatting, types, docstrings, LOC, tests, security scans, docs, and traceability skeleton. | QG-001 through QG-012 applicable gates |
| 2. Configuration, logging, and credential resolver | Validated TOML, precedence/explain, redacted structured logs, protected file credentials, reload skeleton, `config validate`. | CFG-001 through CFG-009, CRED-001/002 |
| 3. Persistence ports and migrations | SQLite/PostgreSQL metadata adapters and SQLite telemetry adapter pass contract tests; migration framework works. | DB-001 through DB-009, MIG-001 through MIG-005 applicable |
| 4. Inventory, sites, policies, and import | Devices/sites/tags/groups/policies/interface annotations can be loaded/imported, queried, explained, and audited. | CFG-004 through CFG-011, DB contracts, AUDIT-001 |
| 5. SNMP discovery | Simulated SNMPv2c/v3 devices discover system/interface/IP data with partial quality and capability memory. | SNMP-T01/T02, DISC-T01 through DISC-T05 |
| 6. Persistent interface identity/lifecycle | Rediscovery preserves interface UUIDs, queues ambiguity, supports admin resolution, and manages lifecycle. | IFID-T01 through IFID-T10, LIFE-T01/T02 |
| 7. ICMP collection | IPv4/IPv6 five-probe collection, exact math, strategies, raw capability separation, and Lite storage/view. | ICMP-T01 through ICMP-T07 |
| 8. SNMP Stage 1/2 and rate math | Exact state polling, conditional counters, capability fallback, safe rates/errors, uptime continuity. | SNMP-T03 through SNMP-T12, UP-T01 through UP-T03, RATE-T01 through RATE-T09 |
| 9. Scheduler and worker model | Anchored due-time scheduling, phases, mixed intervals, no overlap, capacity/worker metrics. | SCHED-T01 through SCHED-T11 |
| 10. Durable spool and backend ingestion | Versioned spool, bulk writers for SQLite/TimescaleDB/ClickHouse, outage/replay/idempotency. | SPOOL-T01 through SPOOL-T08, INGEST-T01 through INGEST-T04, DB-004/011 |
| 11. Current-state/query services and API | Versioned API exposes inventory/current/history/health with auth, bounds, OpenAPI, and operations. | API-T01 through API-T09, AUTH-001 through AUTH-004 |
| 12. Web UI core | Device/interface pages and traffic/latency graphs show correct freshness, gaps, quality, timezone, and sticky order. | WEB-T01 through WEB-T10, LIFE-T03 |
| 13. TUI | Terminal device list/detail and health workflows operate through query services/API. | TUI-T01 through TUI-T05 |
| 14. Dashboards and public publication | High-priority/WAN/top-utilization/top-error/site/provider dashboards and secure anonymous publication. | DASH-T01 through DASH-T06, AUTH-005, TEST-010 |
| 15. Retention and rollups | Native/rollup lifecycle, p95/max aggregation, gap preservation, and backend policies. | ROLL-T01 through ROLL-T05, DB-006 |
| 16. Poll-health and adaptive scheduling | Diagnostic metrics always on; optional ladder/hysteresis; overload semantics correct. | ADAPT-T01 through ADAPT-T04, OBS-T01 through OBS-T04 |
| 17. Operational hardening | systemd, preflight, graceful shutdown, support bundle, backup/restore, upgrades, security headers/roles. | SVC-T01 through SVC-T05, BAK/UPG tests, SEC tests |
| 18. Standard and Enterprise backend completion | TimescaleDB and ClickHouse adapters, roles, migrations, projections, performance schema benchmark. | DB-010 through DB-013, TEST-011 |
| 19. Scale, failure, endurance, and accessibility acceptance | Full 4,000/100,000 workload and failure/endurance suites pass; WCAG acceptance complete. | TEST-001 through TEST-015 |
| 20. Multi-poller groundwork and v1 release | Stable collector identity, per-collector views, integration docs, complete traceability, release artifacts and runbooks. | MPOL tests, QG/packaging/docs, final checklist |

### 20.1 Required implementation-agent work record

For every vertical slice, the implementation agent shall provide:

- requirement IDs addressed;
- files/modules changed;
- architectural boundary used;
- tests added/updated and their results;
- documentation/traceability updates;
- migration/security/performance impact;
- known limitations or follow-up issues;
- confirmation that no source file exceeded 300 lines without an approved exception;
- confirmation that secrets and private data were not added to fixtures/logs.

---

## 21. Component completion checklists

A component is not complete until every applicable checkbox is satisfied.

### 21.1 Repository, build, and CI

- [ ] `pyproject.toml`, `uv.lock`, package layout, and supported Python versions are defined.
- [ ] Clean Ubuntu 26.04 installations pass.
- [ ] Format, lint, type, docstring, LOC, complexity, test, coverage, security, license, and SBOM gates run in CI.
- [ ] Test suites are separated by unit, contract, integration, acceptance, performance, security, and UI layers.
- [ ] Deterministic clocks, fixtures, and simulators are available.
- [ ] Release build produces wheel, sdist, units, migrations, assets, SBOM, checksums, and notices.
- [ ] `README`, contributing, security, code of conduct, and license files exist.
- [ ] Traceability matrix exists and CI checks for unmapped mandatory requirements.
- [ ] No unapproved source file exceeds 300 lines.

### 21.2 Configuration and credential subsystem

- [ ] TOML schema covers all service/runtime settings and supported profiles.
- [ ] Precedence and effective-source inspection are implemented.
- [ ] Candidate validation is complete and field/location-specific.
- [ ] Reload is atomic and preserves healthy active config on failure.
- [ ] File-managed versus API-managed ownership is enforced.
- [ ] Pattern syntax and safety limits are implemented.
- [ ] Protected file credential provider resolves v2c/v3 credentials.
- [ ] Unsafe permissions are rejected/warned according to mode.
- [ ] Credential objects and errors are redacted by construction.
- [ ] `config validate`, `config show-effective --redacted`, and policy explain commands exist.
- [ ] Configuration documentation lists every key, default, scope, precedence, and reload class.

### 21.3 Metadata persistence

- [ ] Repository ports expose domain/read models rather than driver models.
- [ ] SQLite and PostgreSQL adapters pass the same contract suite.
- [ ] Unit-of-work transaction and conflict behavior are defined.
- [ ] Versioned migrations support fresh install and all supported upgrades.
- [ ] Separate database roles are documented and tested.
- [ ] UUIDs remain stable through backup/restore and migration.
- [ ] Soft lifecycle behavior preserves history.
- [ ] Audit events cover all configured administrative actions.
- [ ] No presentation/collector module imports DB drivers.

### 21.4 Telemetry persistence adapters

- [ ] SQLite, TimescaleDB, and ClickHouse implement the common writer/query/current-state ports.
- [ ] Observation IDs, nulls, sentinels, quality, and ordering are semantically equivalent.
- [ ] Lite 24-hour retention is implemented and tested.
- [ ] Timescale hypertables/policies/aggregates are migration-managed.
- [ ] ClickHouse schema, ordering, partitioning, codecs, and projections have benchmark evidence and ADR.
- [ ] Current state can be rebuilt where required.
- [ ] Native/rollup boundary queries have no duplicate/missing points.
- [ ] Read-only integration roles/views are documented.
- [ ] Backend outage errors are typed and do not masquerade as target failures.

### 21.5 Inventory, sites, tags, groups, and circuit metadata

- [ ] Device/site/tag/group entities and APIs are implemented.
- [ ] Device/admin state and management source are explicit.
- [ ] Interface high-priority, WAN, sticky, provider, circuit ID, contracted rate, capacity override, and public eligibility fields exist.
- [ ] CSV/TOML import has validation, dry run, idempotency, and audit.
- [ ] Search and filters cover operational identifiers.
- [ ] Deletion defaults to disable/retire rather than historical erasure.
- [ ] Public identifiers are stable UUIDs/opaque IDs.
- [ ] Site/circuit metadata remains focused and does not grow into a general CMDB.

### 21.6 Collection policy

- [ ] Global, group/tag, site, device, and interface scopes are implemented.
- [ ] Specificity and priority/order tie-breaking are deterministic.
- [ ] Include/exclude lists support glob, exact, and bounded regex.
- [ ] Policies can set permitted interval, timeout, retry, strategy, stale, and adaptive fields.
- [ ] Explain output identifies every match and final decision.
- [ ] Suppressed interfaces remain inventoried.
- [ ] UI/API show suppression/collection reason.
- [ ] Policy evaluation is pure and comprehensively tested.

### 21.7 SNMP discovery

- [ ] SNMPv2c and SNMPv3 auth/noAuth/authPriv fixtures pass.
- [ ] Mandatory system and interface OIDs are collected.
- [ ] IPv4/IPv6 multi-address association works.
- [ ] Discovery completeness and per-subtable quality are retained.
- [ ] Unsupported capability memory and aging/re-probe work.
- [ ] Initial, scheduled, and manual discovery are supported.
- [ ] Manual discovery is an asynchronous operation with status/audit.
- [ ] Partial data remains available after subtable failure.
- [ ] OID catalog and vendor caveats are documented.
- [ ] No secrets appear in SNMP exceptions/logs.

### 21.8 Interface identity and lifecycle

- [ ] Persistent interface UUID is independent of `ifIndex`.
- [ ] Reconciliation uses the documented evidence set and deterministic scoring.
- [ ] Exact/confident/probable/ambiguous/new/missing outcomes exist.
- [ ] Only exact/confident outcomes auto-merge.
- [ ] Duplicate/invalid MAC values are not over-trusted.
- [ ] Prior `ifIndex` and evidence history are stored.
- [ ] Admin review/resolve workflow and audit exist.
- [ ] Disappearance/reappearance fixture passes.
- [ ] Active/inactive/stale/retired transitions and thresholds work.
- [ ] History persists across all lifecycle transitions.
- [ ] Versioned reconciliation corpus passes without unreviewed result changes.

### 21.9 ICMP collection

- [ ] Five probes and configurable spacing/deadline are implemented.
- [ ] Raw RTT sentinel and integer-microsecond semantics are correct.
- [ ] Loss, min/avg/median/max, and jitter calculations pass fixtures.
- [ ] IPv4/IPv6 and source binding work.
- [ ] `icmp_gated`, `snmp_fallback`, `icmp_only`, and `snmp_only` behavior is explicit.
- [ ] Raw-socket capability is isolated from API/web.
- [ ] Timeout/cancellation closes resources through context managers.
- [ ] Collector overload/not-attempted is not reported as packet loss.
- [ ] Teaching documentation explains each metric and sentinel.

### 21.10 SNMP Stage 1/Stage 2 collection

- [ ] Current asyncio PySNMP API is used.
- [ ] Stage 1 uses exact known OIDs, not minute-level full walks.
- [ ] Stage 2 is conditional on current up/up state.
- [ ] HC counters are preferred; fallback source is recorded.
- [ ] Packet components are summed but not individually persisted.
- [ ] Errors and discontinuity are collected.
- [ ] Request sizes and per-device concurrency are bounded.
- [ ] tooBig/fragmentation adaptation works.
- [ ] Partial results and retries preserve quality/attempt context.
- [ ] Event loop remains responsive under slow/timeouting targets.
- [ ] Transport options (port, family, context, source) validate.

### 21.11 Uptime, counters, and rate math

- [ ] Uptime continuity, reset, unknown, and TimeTicks wrap are tested.
- [ ] Documentation states management-uptime limitation.
- [ ] Actual elapsed time is used.
- [ ] Every baseline-invalidating condition produces unavailable rate and new baseline.
- [ ] Plausible 32-bit wrap is conservative.
- [ ] Traffic, packet, and error rates are calculated from raw deltas.
- [ ] Utilization denominator precedence and label are correct.
- [ ] Missing capacity leaves utilization unavailable.
- [ ] Implausible rates are flagged rather than silently clipped.
- [ ] Graph fixtures prove no artificial reset spikes.

### 21.12 Scheduler and workers

- [ ] All four phase/order modes pass deterministic tests.
- [ ] Monotonic due-time priority queue is implemented.
- [ ] Next due is anchored to intended schedule.
- [ ] Mixed intervals coexist in a worker.
- [ ] No overlapping same-device polls occur.
- [ ] Overruns/missed work are recorded separately from target state.
- [ ] Work is spread across the interval.
- [ ] Global/per-target concurrency is bounded.
- [ ] Worker affinity and controlled rebalance work.
- [ ] Estimated cost metrics are used where available.
- [ ] Clock changes do not create duplicate/drifting schedules.
- [ ] Capacity rejection produces not-attempted/unknown semantics.

### 21.13 Durable spool and ingestion

- [ ] Bounded memory queues enforce backpressure.
- [ ] Versioned MessagePack segment format and checksums are documented.
- [ ] Append/fsync policy is implemented and benchmarked.
- [ ] Torn/corrupt tails recover safely and visibly.
- [ ] Batch IDs/observation IDs provide idempotent replay.
- [ ] Time and row threshold flushes work.
- [ ] ClickHouse outage does not stop polling while spool capacity permits.
- [ ] Replay prioritization catches up without starving current data beyond bounds.
- [ ] Warning/critical/exhausted thresholds and actions work.
- [ ] Exhaustion pauses normal collection rather than silently dropping.
- [ ] Unavoidable loss creates an explicit incident.
- [ ] Spool health is present in API/web/TUI/metrics.

### 21.14 Query, current state, and rollups

- [ ] Latest-state selection is deterministic and includes age/quality/collector.
- [ ] Projections rebuild from authoritative observations.
- [ ] Native/rollup selection and disclosure work.
- [ ] Point caps and min/max-preserving downsampling work.
- [ ] Missing intervals remain gaps.
- [ ] Interface rollups contain max/p95 and coverage.
- [ ] ICMP loss aggregates from counts.
- [ ] Restart/outage events are not averaged away.
- [ ] Rollup jobs are restart-safe.
- [ ] Retention never deletes history solely due to lifecycle.

### 21.15 API

- [ ] `/api/v1` routes cover required inventory, state, history, dashboards, health, and admin operations.
- [ ] OpenAPI and stable error envelope are complete.
- [ ] Pagination, filtering, sorting, and query bounds work.
- [ ] Public IDs are UUID/opaque values only.
- [ ] Role checks occur in application/API logic.
- [ ] Long operations expose operation IDs and progress.
- [ ] SSE current-state updates are authorized and reconnectable.
- [ ] Exports are bounded and audited.
- [ ] Integration access is read-only and documented.
- [ ] Backend failures produce truthful typed API states.

### 21.16 Web UI and graphs

- [ ] Authenticated navigation and global search are implemented.
- [ ] Device list default ordering and filters match specification.
- [ ] Device detail shows all required evidence and freshness.
- [ ] Sticky/high-priority/WAN interfaces appear first.
- [ ] Device detail offers a lazy-loaded/paginated compact graph card for every monitored interface after the summary.
- [ ] Traffic, packet, error, state, and latency/loss graphs are accurate.
- [ ] Gaps, resets, discontinuities, partial results, and collector outages are visible.
- [ ] Native/rollup resolution and timezone are shown.
- [ ] Loading/empty/stale/permission/backend-error states exist.
- [ ] Assets are local and pinned.
- [ ] Core workflows meet WCAG 2.2 AA.
- [ ] No state relies only on color.
- [ ] Page/query performance budgets pass.

### 21.17 Dashboards and public access

- [ ] High-priority and WAN dashboards exist.
- [ ] Top utilization uses documented p95/denominator/window.
- [ ] Top errors uses deltas/rates, not cumulative values.
- [ ] ISP/provider, WAN, site, tag, and group views work.
- [ ] Incomplete coverage is excluded/labeled.
- [ ] Public dashboards are globally disabled by default.
- [ ] Publication requires admin action and audit.
- [ ] Both dashboard and interface public eligibility are required.
- [ ] Public field allowlist and separate endpoints are enforced.
- [ ] Opaque slugs, rate limits, cache, and optional network restrictions work.
- [ ] Public negative-authorization tests pass.
- [ ] Kiosk refresh preserves focus and avoids flashing.

### 21.18 TUI

- [ ] `vakr tui` connects through query/API boundary.
- [ ] Default device order and state semantics match web.
- [ ] Search, filter, sort, paging, refresh, and help work.
- [ ] Device summary and interface metrics are complete.
- [ ] Sticky/high-priority/WAN ordering works.
- [ ] Health screen covers collector, DB, and spool.
- [ ] Stale/unavailable data is explicit.
- [ ] 120x30 layout is usable and narrower layout degrades gracefully.
- [ ] No secrets or ad hoc SQL are present.

### 21.19 Authentication and security

- [ ] Admin, operator/user, and anonymous authorization matrix passes.
- [ ] Argon2id password lifecycle and rehash work.
- [ ] Opaque session lifecycle, expiry, rotation, and revocation work.
- [ ] CSRF, rate limiting, secure headers, proxy trust, and CORS defaults pass.
- [ ] Public data isolation passes malicious enumeration tests.
- [ ] Target allow/deny guardrails work.
- [ ] Service privilege/capability separation is validated.
- [ ] DB roles enforce least privilege.
- [ ] Audit coverage is complete.
- [ ] Secret/redaction/support-bundle tests pass.
- [ ] Dependency/security/license scans have no unapproved release blockers.
- [ ] Threat model and security operations documentation are current.

### 21.20 Observability and adaptive polling

- [ ] Required metrics and p50/p95/p99 distributions are exported.
- [ ] Prometheus endpoint is protected by default.
- [ ] Health distinguishes target, protocol, collector, database, partial, stale, and unknown.
- [ ] Queue, event-loop, spool, replay, clock, CPU, memory, and DB pressure are visible.
- [ ] Adaptive polling is off by default.
- [ ] Ladder and recovery hysteresis are deterministic.
- [ ] Configured/effective interval and reason are visible.
- [ ] Collector overload cannot create false down states or target-specific penalties.
- [ ] Support diagnostic snapshot is useful and redacted.

### 21.21 systemd, operations, backup, and upgrade

- [ ] Top-level systemd contract works on Ubuntu 26.04 LTS (and on the RHEL family once PKG-006 is active).
- [ ] Startup preflight catches config/permissions/path/schema/connectivity issues before discovery.
- [ ] Graceful shutdown drains and spools within bound.
- [ ] Service restart policies do not create identity conflicts or restart storms.
- [ ] `vakr doctor` covers all documented checks.
- [ ] Structured journald events have stable IDs and safe context.
- [ ] Metadata and telemetry backup/restore are tested.
- [ ] Spool recovery and projection rebuild are tested.
- [ ] Credential rotation/rebind after restore is documented/tested.
- [ ] Upgrade and unsupported downgrade behavior is tested.
- [ ] Capacity, troubleshooting, disaster recovery, and rollback-limit runbooks are complete.

### 21.22 Performance and release readiness

- [ ] Enterprise schema benchmark/ADR is approved.
- [ ] TEST-001 through TEST-015 pass.
- [ ] No systematic schedule drift or same-target overlap exists.
- [ ] No silent telemetry loss or unintended logical duplication exists within tested capacity.
- [ ] No false target-down caused by overload/collector outage remains.
- [ ] 24-hour endurance has no unbounded resource growth.
- [ ] Storage/query sizing is documented from measured data.
- [ ] Requirements traceability is complete.
- [ ] All mandatory documentation and tutorials are complete.
- [ ] No known critical/high security issue or secret leak remains.
- [ ] Release artifacts install, self-update, and roll back on Ubuntu 26.04 LTS (and on the RHEL family once PKG-006 is active).
- [ ] Final product name and license are confirmed.

---
## 22. Previously overlooked or ambiguous areas and their resolution

The source drafts were strong on polling, identity, scheduling, ClickHouse durability, and core web navigation. The following areas required clarification to make an implementation agent less likely to guess incorrectly:

| Area | Why clarification was needed | Resolution in this blueprint |
|---|---|---|
| Multi-database telemetry | The original fixed architecture assigned telemetry exclusively to ClickHouse, while the added requirements requested SQLite and PostgreSQL time-series options. | Defined four deployment profiles and common repository contracts; only PostgreSQL + ClickHouse carries the enterprise scale commitment. |
| Configuration source of truth | Text configuration and admin-managed metadata can become competing writers. | Every record is explicitly file-managed or API-managed; conflicts fail rather than silently overwrite. |
| Site/circuit semantics | Site, ISP, WAN, high-priority, and sticky dashboards need structured metadata, not informal display logic. | Added Site and typed interface circuit metadata while retaining a narrow non-CMDB scope. |
| Public dashboard security | “No authentication” did not define what data may be exposed or whether private navigation remains available. | Added explicit publication, dual eligibility, public field allowlist, opaque slugs, rate limits, and negative authorization tests. |
| Device status colors | Red/green/gray alone can conflate target failure, unknown data, collector outage, and administrative offline. | Added Attention (red/amber), Active (green), and explicit Offline/Retired (gray) buckets with text/icons/freshness. |
| ICMP-filtered devices | Strict ICMP gating prevents SNMP collection from devices that intentionally block ping. | Preserved `icmp_gated` as default and added opt-in `snmp_fallback`, `icmp_only`, and `snmp_only` strategies. |
| Ranking definitions | “Top talkers” and “top errors” were not mathematically defined. | Defined 15-minute p95 directional utilization and valid error-delta/error-ppm ranking with coverage rules. |
| Utilization denominator | `ifSpeed`/`ifHighSpeed` may differ from contracted circuit capacity. | Defined override -> contracted rate -> `ifHighSpeed` -> `ifSpeed` precedence and required labeling. |
| Interface ambiguity workflow | The sources prohibited ambiguous auto-merge but did not define operator resolution. | Added review, explicit map/new decision, evidence display, and audit. |
| Data freshness | A latest value can be old but look current. | Current-state models and UI/TUI always expose age, freshness, collector, and quality. |
| Storage exhaustion | The drafts required no silent loss but did not fully define collector behavior when spool is full. | Added capacity states and a fail-safe pause of normal polling with unknown/not-attempted semantics. |
| Front-end stack and accessibility | Framework choice, asset delivery, public kiosk behavior, and accessibility were open. | Adopted server-rendered FastAPI/Jinja/HTMX/ECharts, local assets, WCAG 2.2 AA, and kiosk requirements. |
| TUI data boundary | Direct database access would bypass application semantics and backend abstraction. | TUI uses application/query services or the local versioned API only. |
| Backup/restore and upgrades | Completion criteria mentioned docs but not executable recovery behavior. | Added backup, restore, spool recovery, projection rebuild, migration, and upgrade acceptance tests. |
| Open-source teaching outcome | Documentation rules did not define labs, examples, or learner acceptance. | Added Lite labs, simulators, tutorials, annotated concurrency/counter/identity material, and documentation acceptance tests. |
| License and naming | Open-source intent existed without a license or buildable package name. | Adopted working name `vakr` and recommended Apache-2.0, both requiring owner confirmation before public v1. |

No additional answer is required to begin implementation because this document supplies defaults. The owner confirmations in section 1.3 remain release decisions rather than initial blockers.

### 22.1 Corrections and clarifications in revision 1.1

| Area | Problem in 1.0-draft | Resolution in 1.1 |
|---|---|---|
| Naming | Working name `netperfmon` pending owner decision. | Renamed to `vakr` throughout; ADR-019 accepted. |
| License | Apache-2.0 only recommended. | Apache-2.0 confirmed; ADR-018 accepted. |
| Supported releases | Sections 21.21 and 21.22 referred to "both supported Ubuntu releases", but only 26.04 LTS was listed; PROD-005 grammar implied several. | Ubuntu 26.04 LTS is the single supported release; RHEL-family `.rpm` follows MVP (PKG-006). |
| Go policy | Section 1.2 allowed Go in a garbled sentence that contradicted section 24. | Go permitted where measured performance requires it, under section 24. |
| No operational milestone | Appendix D was scaffolding only and section 26 was enterprise v1, with nothing in between for running on a real network. | Section 27 defines the MVP, its scope, acceptance, and milestone order. |
| Small production retention | Every profile with long history needed ClickHouse or PostgreSQL; Lite kept 24 hours. | Added the Compact profile: SQLite with 30-day native and 400-day rollup retention, benchmark-gated (ADR-021). |
| ICMP privilege model | Section 15.3 isolated `CAP_NET_RAW` to an "ICMP component" that section 6.2 never defined, while ICMP gating needs ICMP and SNMP in one poll. | Unprivileged ICMP datagram sockets; no raw-socket capability by default (ICMP-012, ADR-023). |
| Process contract | Poll-plan distribution, spool ownership (6.2 vs 15.3), reload propagation, worker rebalancing, and rate-baseline ownership were unspecified. | Inter-process contract added to section 6.2. |
| Credentials example | Section 11.3 placed credentials in the main config, contradicting section 11.1. | Split into `/etc/vakr/secrets/credentials.toml`; DSN secrets use the same provider; v2c example added. |
| Unset defaults | ICMP impaired thresholds, the device status table, freshness, rate gap and plausibility limits, uptime tolerance, lifecycle triggers, rounding, and spool fsync were "configurable" with no defaults, so tests could not be written. | Appendix E. |
| Interface classes | High-priority, sticky, and WAN flags existed, but not the owner's "interfaces of interest" vs secondary distinction. | META-011, META-012, UI-017. |
| Delivery to a separate production site | No release, update, configuration-merge, or production-data feedback path. | Section 28. |
| Monitoring vakr itself | Alerting is external, but there was no way for the team's CheckMK/Nagios to watch vakr. | OPS-001 Nagios-format health check. |
| Hash determinism | Scheduler hashing did not name a function; Python's `hash()` is salted per process and differs from Go's. | SHA-256 named in Appendix E.11. |

---

## 23. Network simulator and performance harness

The repository shall include deterministic simulators so correctness and enterprise load do not depend on 4,000 physical devices.

### 23.1 Simulated device capabilities

The simulator shall support configurable:

- IPv4/IPv6 ICMP response, loss, RTT distribution, delay, and burst loss;
- SNMPv2c and SNMPv3 authentication/security levels;
- system OIDs and uptime progression/reset/wrap;
- interface count and table contents;
- HC and legacy counter support;
- counter rates, wraps, resets, and discontinuities;
- interface state changes;
- partial table/varbind responses;
- unsupported OIDs;
- tooBig responses and response fragmentation pressure;
- timeout, retry, late response, and connection/transport errors;
- ifIndex renumbering;
- interface disappearance/reappearance;
- changing names, aliases, descriptions, MACs, and IPs;
- slow/weak SNMP agent behavior;
- device restart and management-subsystem restart;
- DNS changes/failures where the harness controls resolution.

### 23.2 Load profiles

Provide at least:

- **Unit-small:** 5 devices / 50 interfaces.
- **Lite-reference:** 20 devices / 500 interfaces / 24-hour-retention behavior.
- **Standard-benchmark:** configurable, with published hardware and results rather than a fixed support claim.
- **Enterprise-reference:** 4,000 devices / 100,000 monitored interfaces / mixed intervals.
- **Failure-storm:** correlated ICMP loss and SNMP timeout across a large percentage of devices.
- **Database-outage:** telemetry backend unavailable with sustained spool growth and later recovery.
- **High-churn discovery:** renumbering/disappearance/reappearance and metadata changes.

### 23.3 Reproducibility

Every performance report shall record:

- source revision and schema versions;
- simulator seed/profile;
- host CPU, memory, storage, kernel, and Python version;
- database versions/configuration;
- process/worker/concurrency configuration;
- data volume and retention state;
- p50/p95/p99 results;
- CPU, memory, event-loop lag, queue/spool/database metrics;
- errors, drops, late/missed/not-attempted counts;
- test duration and warm-up period.

A performance result without this context is not an acceptance result.

---

## 24. Rules for a future Go component

Python is the required initial implementation. If profiling and an approved ADR authorize a Go collector or ingestion component:

- It shall consume/produce the same versioned observation and spool contracts.
- It shall not change public API, identity, quality, scheduling, retention, or data-loss semantics.
- Go code shall use current supported Go releases selected by ADR and a pinned module graph.
- `gofmt`, `go vet`, tests, race detection where applicable, vulnerability scanning, and a configured linter shall pass.
- Go files shall follow the same SRP/KISS and approximately 300-line decomposition goal.
- Exported packages, types, and functions shall have clear Go documentation explaining network context and invariants.
- Context cancellation and deadlines shall propagate through network and storage operations.
- Goroutines, channels, queues, and per-target concurrency shall be bounded and observable.
- Cross-language golden fixtures shall prove byte/schema and semantic compatibility.
- The Python implementation shall remain the reference semantics until an ADR explicitly changes that role.

---

## 25. AI implementation protocol

When an AI coding agent receives a task under this blueprint, it shall follow this sequence:

1. Identify requirement and test IDs.
2. Inspect current architecture, tests, and traceability before editing.
3. State any conflict or required ADR; do not silently reinterpret behavior.
4. Design the smallest vertical change at the correct layer.
5. Add/update tests first or in the same change.
6. Implement with typed boundaries, required docstrings, and why-focused comments.
7. Keep files under 300 lines by decomposing responsibilities.
8. Run the narrow test set, then all affected quality gates.
9. Update configuration reference, operator/developer docs, changelog, and traceability.
10. Report exact tests run and any limitation.

The agent shall not:

- bypass repository ports with direct SQL in feature code;
- add unbounded queues, retries, queries, exports, or concurrency;
- substitute zero for missing data;
- convert collector/storage failures to target-down state;
- weaken authentication/authorization because the UI hides an action;
- add a framework or distributed service without measured need and an ADR;
- use real network credentials or sensitive production data in tests;
- disable lint/type/security rules globally to make CI pass;
- leave a phase marked complete with stubbed persistence, fake auth, or untested failure paths;
- rewrite existing correct logic merely for stylistic preference.

### 25.1 Task definition of done

A task is done only when:

- behavior satisfies the cited requirement IDs;
- tests prove normal, edge, and applicable failure behavior;
- typing/lint/docstring/LOC/security gates pass;
- migrations and compatibility impact are addressed;
- logs/errors are safe and operator-meaningful;
- documentation and traceability are current;
- no known silent data loss, false state, or secret exposure was introduced.

---

## 26. Project completion criteria

The project may be declared v1 complete only when all of the following are true:

1. All mandatory requirement IDs are implemented or explicitly deferred by an approved owner decision/ADR.
2. TEST-001 through TEST-015 pass with retained reports.
3. PostgreSQL + ClickHouse passes the 4,000-device / 100,000-interface design point.
4. SQLite Lite mode and PostgreSQL/TimescaleDB Standard mode pass their functional contract suites.
5. Requirements traceability is complete.
6. There is no known secret leakage, silent telemetry loss within supported capacity, schedule drift, same-target overlap, artificial reset spike, or false target-down caused by collector overload/outage.
7. Public dashboard isolation and core authorization tests pass.
8. Core web and public workflows meet the accessibility acceptance criteria.
9. Installation, configuration, credential rotation, operations, troubleshooting, capacity, backup/restore, upgrade, API, TUI, web, security, and teaching documentation are complete.
10. Supported Ubuntu installation and upgrade paths pass.
11. Release artifacts, checksums, SBOM, license notices, and migrations are complete.
12. Final product name and open-source license are confirmed.
13. Known limitations and support boundaries for Lite, Standard, and Enterprise profiles are published.
14. The codebase remains modular, typed, comprehensively documented, and within the file-size policy except for approved exceptions.

---

## 27. MVP definition

### 27.1 Purpose

The MVP is the first release that NetOps runs in production alongside MRTG and SmokePing, and it must be good enough to retire them. Its scale is much smaller than the enterprise design point. It still uses the same architecture, ports, observation contracts, and quality semantics, so later capabilities extend it rather than replace it. MVP code shall not take shortcuts the full design would have to undo, such as SQL in request handlers, zero stored for missing data, or `ifIndex` used as identity.

### 27.2 Reference workload

| Item | MVP value |
|---|---|
| Devices | 100 Cisco IOS XE devices on recent releases: 80 routers, 20 switches |
| Active (up/up) interfaces | About 10 per router and 40 per switch, so about 1,600 |
| Discovered interfaces | More than the active count (unused switch ports, SVIs, tunnels, subinterfaces). All are inventoried (DISC-007) and status-checked per policy; counters are collected only for up/up interfaces. |
| Interest classes | Primary (uplinks, primary-path transit) and secondary (META-011) |
| SNMP | v2c |
| Polling | 60 s default for ICMP and interfaces; rediscovery every 24 h |
| Positions | One required; two preferred (east and west, 55–70 ms RTT apart) |
| Retention | 30 days native; 15-minute rollups through day 400 |
| Profile | Compact (SQLite metadata and telemetry) |

The load per position is about 24,000 SNMP varbinds and 500 ICMP probes per minute. That is roughly 1/50 of the enterprise design point and well within one Python asyncio process.

### 27.3 Storage sizing (planning estimate)

| Data | Rows per collector |
|---|---|
| Native interface observations, 30 days | 1,600 × 1,440 × 30 ≈ 69.1 million |
| Native ICMP and SNMP device observations, 30 days | 2 × 100 × 1,440 × 30 ≈ 8.6 million |
| Interface rollups, days 31–400 at 15 minutes | 1,600 × 96 × 370 ≈ 56.8 million |
| ICMP rollups, days 31–400 | 100 × 96 × 370 ≈ 3.6 million |
| **Total** | **≈ 138 million** |

With two positions, each node also stores its peer's observations, about 276 million rows per node. At an assumed 80–150 bytes per row including the clustered key, that is roughly 22–42 GB per node. Provision at least 100 GB of SSD-class storage for `/var/lib/vakr` on each node. The ADR-021 benchmark replaces this estimate with measured values.

### 27.4 Scope

**In the MVP:**

- **Inventory:** manual device entry (web, CLI, TOML/CSV import); sites, tags, and groups; interface annotations (interest class, sticky, WAN circuit, provider, circuit ID, contracted rate, capacity override, comments); policy-assigned annotations (META-012); file/API ownership (META-008).
- **Discovery:** SNMPv2c system, IF-MIB, and IP-MIB; scheduled and manual rediscovery; capability memory.
- **Identity:** persistent interface UUIDs, reconciliation, and admin review of ambiguous cases. Weights are calibrated on IOS XE field captures (ADR-015).
- **ICMP:** five-probe reachability over IPv4, and IPv6 where management addressing uses it. `icmp_gated` is the default; `snmp_fallback` and `icmp_only` are opt-in.
- **SNMP collection:** Stage 1 and Stage 2, HC counters, rates, errors, and uptime continuity.
- **Scheduler:** hostname phase, anchored cadence, no overlap, and poll-health diagnostics.
- **Storage:** spool, ingestion, the Compact profile, rollups, and retention.
- **API and auth:** the API v1 subset needed by the web UI, TUI, CLI, and replication; local auth with admin and operator roles; sessions, CSRF protection, and Argon2id.
- **Web UI:** overview; device list (Attention/Active/Offline); device detail; interface detail with traffic, packet, error, and state graphs; SmokePing-style latency/loss graphs; a primary-interfaces dashboard; WAN/provider and site views; top utilization and top errors; collector health; global search.
- **TUI:** device list, device detail, and health.
- **CLI:** `setup`, `config validate|show-effective|upgrade|new-options`, `doctor`, `check health`, `self-update`, `field collect`, import/export, `policy explain`.
- **Two-position operation** (section 27.5), in milestone M5.
- **Delivery tooling** (section 28).
- **Self-metrics** endpoint and `vakr check health` (OPS-001).

**Deferred past MVP.** The design stays in this document, but these are not implemented in the MVP:

- SNMPv3.
- PostgreSQL, TimescaleDB, and ClickHouse adapters. The ports and contract tests exist so these can be added without redesign.
- Public anonymous dashboards.
- Adaptive interval changes (poll-health diagnostics stay on).
- Server-Sent Events (the MVP uses HTMX polling).
- OIDC/SSO.
- Multiple worker processes per collector.
- Asynchronous bulk export.
- `.rpm` packaging.
- Go components.
- Enterprise scale and endurance acceptance (TEST-001 to TEST-005, TEST-009, TEST-012, TEST-013).
- A formal WCAG audit (TEST-015). Pages are still built to UI-011 from the start.

### 27.5 Two-position design (ADR-024)

Goal: during a network partition, each position keeps collecting from every device it can still reach. Once the partition heals, both positions end up with complete history.

1. Each position runs a complete vakr node: collector, Compact storage, API/web, and TUI. Each node polls the full inventory from its own vantage point under its own collector UUID. Every device is therefore watched twice; the extra SNMP load is a few PDUs per device per minute, which is negligible at MVP scale.
2. One node is the metadata primary (`[node] role = "primary"`). The peer (`role = "peer"`) pulls inventory, policies, annotations, and users from the primary's API every 60 seconds. If the primary is unreachable, the peer keeps polling with its last-known metadata. A peer refuses metadata edits with a message that names the primary. Promoting a peer is a documented manual command (`vakr node promote`).
3. Each observation belongs to the collector that made it, so replication has no write conflicts. Each node pulls the other's observations from `/api/v1/replication/observations` in bounded batches. The per-collector spool sequence serves as the cursor, and observation UUIDs make inserts idempotent. A partition only delays replication; after it heals, the backlog drains oldest-first without starving local ingestion.
4. Peer traffic uses HTTPS (directly or through the documented reverse proxy) with a dedicated replication credential. That credential can reach only the replication and metadata-read endpoints.
5. Views show per-position state side by side. The composite device status (Appendix E.2) is "Down" only when every fresh collector reports the device down. A device that is down from one position and up from the other shows amber "Reachable from <position> only", which is often the first sign of a partition.
6. Latency, loss, and jitter are never merged across collectors, because the 55–70 ms between positions makes a merged RTT meaningless; graphs show one series per position. Counter values from different collectors are never summed (MPOL-003). Traffic graphs default to the local collector and can switch to the peer.
7. Replication lag, backlog, and the last successful exchange are visible on the health page, in metrics, in `vakr check health`, and in the TUI header.
8. The collector name defaults to the short hostname (SCHED-001). `[node] position = "east"` sets the label shown in the UI.

| ID | Requirement |
|---|---|
| MPOS-001 | vakr shall run with one position and should support two positions running concurrently. |
| MPOS-002 | Each position shall operate as a complete node that keeps collecting, storing, and serving views while isolated from the other position. |
| MPOS-003 | A peer shall pull metadata from the primary, continue on last-known metadata during isolation, and reject local metadata edits with a clear message. |
| MPOS-004 | Nodes shall replicate each other's observations by idempotent, cursor-based pull; replays shall create no logical duplicates. |
| MPOS-005 | After a partition heals, replication shall backfill every peer observation the source still retains, oldest first, within documented time bounds. |
| MPOS-006 | Replication shall use HTTPS and a dedicated least-privilege credential. A replication or peer failure is reported as node health, never as target state. |
| MPOS-007 | Device views shall show per-position state and a composite status that preserves per-collector evidence (MPOL-004). |
| MPOS-008 | Latency, loss, jitter, and counter-derived values shall never be merged or summed across collectors. |

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| MPOS-T01 | System | Two nodes in separate network namespaces. Cut traffic between the nodes, and between west and half the simulated devices, for 30 minutes. | Each node keeps polling everything it can reach. West shows the cut-off devices as unreachable from west only, and the composite status reads "Reachable from east only". | MPOS-001, MPOS-002, MPOS-007 |
| MPOS-T02 | System | Restore connectivity. | Replication backfills in both directions, oldest first. Per-collector row counts match the source, with no duplicate observation UUIDs. | MPOS-004, MPOS-005 |
| MPOS-T03 | System | Primary unreachable; an operator edits metadata on the peer. | The peer keeps polling with last-known inventory and refuses the edit, naming the primary. | MPOS-003 |
| MPOS-T04 | System | 60 ms emulated inter-position RTT (netem). | Latency graphs show separate east and west series, with no merged statistics. | MPOS-008 |

### 27.6 MVP acceptance criteria

| ID | Criterion |
|---|---|
| MVP-ACC-01 | Each position collects the reference workload at 60 s, with at least 99% of healthy-device polls starting within ±5 s of schedule, no same-device overlap, and no systematic drift over 7 days. |
| MVP-ACC-02 | Every monitored interface has traffic, packet, and error graphs at the 1 h, 24 h, 7 d, 30 d, and 400 d ranges. Graphs show gaps for missing data and no spikes across device reloads or counter discontinuities. |
| MVP-ACC-03 | During the parallel run, vakr's 5-minute average traffic rates agree with MRTG's for the same interfaces within ±2%, after aligning sample windows. Differences outside that tolerance are explained, not tuned away. |
| MVP-ACC-04 | From the same vantage point, vakr's median RTT agrees with SmokePing's within ±1 ms or ±5% (whichever is larger), and its loss counts are consistent with SmokePing's. |
| MVP-ACC-05 | 30 days of native data and 400 days of rollups are retained within the storage budget measured by ADR-021, and 400-day graphs render in under 2 s. |
| MVP-ACC-06 | Collection survives a service restart, a host reboot, and telemetry-store unavailability with no silent loss. An abrupt power loss loses no more than the documented fsync window (Appendix E.8) and records a telemetry-loss incident. |
| MVP-ACC-07 | Using only the install guide, a clean Ubuntu 26.04 host goes from `.deb` to collecting in under 15 minutes. `vakr self-update` moves between consecutive releases, including config upgrades, without manual edits, and rollback works. |
| MVP-ACC-08 | If two positions are deployed, MPOS-T01 through MPOS-T04 pass. One position is enough to satisfy the MVP. |
| MVP-ACC-09 | CheckMK or Nagios monitors vakr itself through `vakr check health` (OPS-001). |
| MVP-ACC-10 | A parallel run of at least 14 consecutive days alongside MRTG/SmokePing shows no unexplained gaps, and NetOps confirms that vakr answers its routine questions: uplink utilization, errors, and latency/loss history. |

Passing MVP acceptance allows the owner to retire MRTG and SmokePing and starts RPM packaging (PKG-006).

### 27.7 Post-MVP direction

After MVP, work continues toward full v1 in this order unless the owner reprioritizes:

1. SNMPv3.
2. RPM packaging.
3. PostgreSQL metadata and ClickHouse telemetry (the Enterprise profile), with the ADR-016 benchmark.
4. Public dashboards.
5. Adaptive polling.
6. Multi-worker collectors.
7. SSE.
8. Enterprise acceptance (section 19.9).

Compatibility hardening, which ends the ADR-031 policy, starts when the project moves toward third-party and enterprise readiness.

### 27.8 Milestones (ADR-022)

From M1 onward, each milestone ends with a signed GitHub release that the production operator installs with `vakr self-update`, so production always runs the latest increment.

| Milestone | Delivers | Exit gate |
|---|---|---|
| M0 — Foundation docs | Repository, license, `VERSION`, blueprint 1.1, `CLAUDE.md`, `tools/md2html`, and the GitHub Pages workflow | Pages build passes the link check |
| M1 — Delivery pipeline | `pyproject.toml` taking its version from `VERSION`; CI gates QG-001 to QG-005, QG-007, and QG-009; a minimal `vakr` CLI (`--version`, plus skeletons of `doctor` and `check health`); `.deb` build with nfpm and a bundled interpreter; the signed release tool; `vakr self-update` with verify, install, config upgrade, restart, and rollback, plus the optional timer; the config layering and upgrade framework; field kit v1 (host facts, IOS XE SNMP walks, position RTT, GitHub egress); `tools/check-updates` | The operator installs v0.1.x on a production host and self-updates to the next release, and the first field bundle is received and decrypted on the development host |
| M2 — Core | Domain model, ports, and observation envelope; configuration and the v2c credential provider; Compact SQLite metadata and telemetry adapters with contract tests; ICMP math, rate, uptime, and policy logic; the ADR-021 benchmark | Unit and contract tests pass, and the benchmark meets budget |
| M3 — Collection | Unprivileged ICMP adapter; SNMP discovery and Stage 1/2 (PySNMP); scheduler; spool and ingestion; simulator built from field captures (snmpsim) | The production node collects the real inventory headlessly, and `vakr tui` and the CLI show current state |
| M4 — Viewing | Query services, the API v1 subset, local auth, web pages and graphs, TUI, identity reconciliation and review, rollups and retention, `vakr check health` | The parallel run with MRTG/SmokePing begins |
| M5 — Second position | Node roles, metadata pull, observation replication, per-position views, partition tests | MPOS-T01 to MPOS-T04 pass (if two positions are deployed) |
| M6 — Acceptance | Fixes from the parallel run, documentation, and MVP acceptance evidence | MVP-ACC-01 to MVP-ACC-10 pass |

### 27.9 Compact (MVP) configuration example

```toml
config_version = 1

[profile]
name = "compact"

[node]
role = "primary"            # primary | peer
position = "east"           # label shown in the UI
# primary_url = "https://vakr-east.example.net"   # required when role = "peer"

[polling]
default_interval = "60s"
reachability_strategy = "icmp_gated"

[telemetry]
native_retention_days = 30
rollup_retention_days = 400
rollup_interval = "15m"

# Description keywords (META-012). The packaged defaults already cover ISP, WAN,
# and UPLINK; this adds a site-specific keyword.
[[interface_classification.keywords]]
keyword = "TRANSIT"
tag = "transit"
interest = "primary"

[network]
# https_proxy = "http://proxy.example.net:3128"   # or use HTTPS_PROXY in the unit environment
# Enterprise TLS-inspection CA: install with `vakr tls trust-ca` (EGRESS-003)

[update]
repository = "robert-patrick-texas/vakr"
channel = "prerelease"      # stable | prerelease; MVP iterations are pre-releases
# hold_version = "0.4.2"    # pin; the update timer will not go past it
```

Every omitted option takes its packaged default (CFGM-001). Options appear in the packaged catalog when the milestone that implements them lands. Until then, validation rejects them as unknown (for example `[polling]` before M3), so this example is valid only once M3 is complete.

---

## 28. Delivery, update, and lifecycle tooling

### 28.1 Separating development from production

Development happens on the development host, which holds this repository. Production runs at a separate site operated by NetOps. Only two things cross between them: signed releases go to production, and encrypted field bundles come back. Nothing in production depends on reaching the development host.

```text
development host                                   production site
----------------                                   ---------------
code -> tools/release -> GitHub Release  --HTTPS-->  vakr self-update -> apt install .deb
                         (.deb, SHA256SUMS, .sig)     (optional daily timer)

inbox/ <- tools/field-inbox <-- HTTPS URL or scp <--  vakr field collect -> bundle.tar.gz.p7m
```

### 28.2 Single version source

| ID | Requirement |
|---|---|
| VER-001 | The project version shall be defined only in the repository-root `VERSION` file. Python package metadata (dynamic version), `vakr --version`, Go builds (`-ldflags -X`), package versions (nfpm), release tags (`v<version>`), and documentation footers all derive from it. |
| VER-002 | `VERSION` holds a Semantic Versioning string, `MAJOR.MINOR.PATCH`, optionally followed by a `-rc.N` pre-release suffix. The release tool derives the PEP 440 form (`0.2.0rc1`) and the Debian/RPM form (`0.2.0~rc.1`) mechanically. |
| VER-003 | CI shall fail when a version string is hard-coded anywhere other than `VERSION`. Changelog headings and migration identifiers are allowlisted. |
| VER-004 | Before MVP acceptance, minor releases may contain breaking changes (ADR-031). The changelog marks each one and names the automatic migration that handles it. |

### 28.3 Self-update

| ID | Requirement |
|---|---|
| UPD-001 | `vakr self-update`, run as root, shall find the newest release in the configured repository and channel and download its `.deb`, `SHA256SUMS`, and `SHA256SUMS.sig`. It verifies the signature against the release signers file shipped in the installed package, then the checksum. Any failure stops the update before the system changes. |
| UPD-002 | After verification, self-update shall: <br>1. back up the metadata database, operator configuration, and unit drop-ins; <br>2. cache the currently installed package for rollback; <br>3. install the new package with `apt-get install`, so dependencies resolve; <br>4. run `vakr config upgrade` (section 28.4) and the database migrations; <br>5. restart services and run post-update health checks; <br>6. print a summary with a changelog excerpt and the new configuration options. |
| UPD-003 | An optional `vakr-update.timer`, disabled by default and enabled with `systemctl enable --now vakr-update.timer`, shall run `vakr self-update --yes --if-newer` daily with a randomized delay of up to 4 hours. |
| UPD-004 | Other modes: <br>• `vakr self-update --check` only reports whether an update exists. <br>• `--version X` installs a specific release. <br>• `--file PKG --sums SUMS --sig SIG` installs verified artifacts that were transferred by hand to a host without GitHub access. <br>Proxies and extra trusted CAs follow EGRESS-001 and EGRESS-002. |
| UPD-005 | `vakr self-update --rollback` shall reinstall the cached previous package and restore the pre-update metadata backup and configuration. Telemetry migrations during the MVP shall be additive, so rollback needs no telemetry restore. Any exception is flagged in the release notes and blocks timer-driven updates. |
| UPD-006 | If post-update health checks fail, self-update shall: <br>• leave services in their previous state where possible; <br>• report the failure clearly and exit non-zero; <br>• record the outcome where `vakr check health` reports it. <br>The timer shall not retry a failed version until a newer release appears or an operator intervenes. |
| UPD-007 | A lock prevents concurrent updates. Every step emits structured journald events with stable IDs, and secrets never appear in update logs. |
| UPD-008 | `[update] hold_version` pins the deployment; timer-driven updates will not go past it. |

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| UPD-T01 | Packaging | Install release N on a clean Ubuntu 26.04 container, then self-update to N+1 | Services restart healthy, configuration is upgraded, and the summary is printed | UPD-001, UPD-002 |
| UPD-T02 | Security | Tampered `.deb`, tampered `SHA256SUMS`, or a bad or missing signature | Update refused before any system change | UPD-001 |
| UPD-T03 | Failure | Forced post-update health failure | Previous package, metadata, and configuration are restored; exit is non-zero; the timer does not retry the same version | UPD-005, UPD-006 |
| UPD-T04 | Offline | `--file` install of verified artifacts with no network | Succeeds | UPD-004 |
| UPD-T05 | Timer | Timer enabled; newer release present, then absent, then beyond `hold_version` | Updates exactly once, then no-ops, and respects the hold | UPD-003, UPD-008 |

### 28.3.1 Outbound HTTPS, proxies, and enterprise TLS inspection

Production hosts reach GitHub either directly or through an enterprise proxy. Some enterprises decrypt outbound traffic and re-sign it with their own CA, so vakr must be able to trust that CA. Verification is never turned off.

| ID | Requirement |
|---|---|
| EGRESS-001 | All outbound HTTPS shall use one shared client configuration. That covers release checks and downloads, the field kit's reachability test, and peer replication. Proxies come from `[network] https_proxy` and `no_proxy`, falling back to the standard `HTTPS_PROXY` and `NO_PROXY` environment variables. Proxy credentials come from the credential provider and never from plain configuration. |
| EGRESS-002 | The trust store shall be the OS store plus every PEM certificate in `/etc/vakr/tls/ca.d/`. The OS store is `/etc/ssl/certs/ca-certificates.crt` on Ubuntu and `/etc/pki/tls/certs/ca-bundle.crt` on the RHEL family. The bundled interpreter shall not rely on its compiled-in CA paths. |
| EGRESS-003 | `vakr tls trust-ca FILE [--name NAME] [--system]` shall: <br>• check that the file is a PEM X.509 certificate with `basicConstraints CA:TRUE` that is within its validity period; <br>• print its subject, SHA-256 fingerprint, and expiry so the operator can compare them with an out-of-band source; <br>• after confirmation (or `--yes`), install it into `/etc/vakr/tls/ca.d/` as root-owned, mode `0644`. <br>`--system` also adds it to the OS store (`update-ca-certificates` or `update-ca-trust`). `vakr tls list` and `vakr tls remove NAME` manage installed CAs. Every change is audited. |
| EGRESS-004 | There shall be no configuration key, flag, or environment variable that disables certificate or hostname verification. |
| EGRESS-005 | `vakr doctor` and `vakr self-update --check` shall diagnose egress step by step: DNS, proxy connect, TLS handshake, and the GitHub API and release-asset hosts. When the presented certificate's issuer is untrusted, the output names that issuer and suggests `vakr tls trust-ca`. |
| EGRESS-006 | A trusted CA expiring within 30 days shall make `vakr check health` report WARNING; an expired one reports CRITICAL. |

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| EGRESS-T01 | Integration | A TLS-intercepting proxy re-signs traffic with a test CA | Update check fails with an issuer-naming diagnosis, then succeeds after `vakr tls trust-ca` | EGRESS-002, EGRESS-003, EGRESS-005 |
| EGRESS-T02 | Security | Leaf, expired, or non-PEM certificate given to `trust-ca` | Rejected with a clear reason; nothing installed | EGRESS-003 |
| EGRESS-T03 | Unit | Proxy settings from config, environment, and `no_proxy` | Precedence and bypass match documentation | EGRESS-001 |
| EGRESS-T04 | Static | Search for code that disables verification (`CERT_NONE`, `check_hostname = False`, `verify=False`) | None found outside tests that prove rejection | EGRESS-004 |

### 28.4 Configuration layering and upgrade merge

| ID | Requirement |
|---|---|
| CFGM-001 | Every option's default shall ship in `/opt/vakr/share/defaults/` as documented, read-only, package-owned TOML. Operator files under `/etc/vakr/` hold only overrides, so options added in a new release take effect with their documented defaults without operator action. |
| CFGM-002 | Operator files carry `config_version`. Each release ships ordered, declarative config migrations: rename, move, remove, change type or units, split, and value transforms. `vakr config upgrade` applies them in order, keeps the operator's comments and formatting, writes a timestamped backup, and prints a diff. Self-update runs it automatically. |
| CFGM-003 | `vakr config upgrade --dry-run` shows the diff without writing. Any migration or validation failure leaves operator files untouched and aborts the update before services restart. |
| CFGM-004 | `vakr config new-options [--since VERSION]` lists options added since the deployment's previous version, with defaults and descriptions. `--annotate` appends them as commented-out entries to the relevant operator file, so operators find them where they edit. |
| CFGM-005 | The package shall not ship operator-edited files as dpkg conffiles, because that causes interactive upgrade prompts. `postinst` creates `/etc/vakr/config.toml` from a template only when it does not exist. |
| CFGM-006 | After an upgrade, an unknown key is a validation error. The error names the file, line, and key, and suggests the closest valid key or the migration that removed it. |
| CFGM-007 | Inventory, policy, and dashboard records managed as files (META-008) use the same `config_version` and migration mechanism. |

| Test ID | Layer | Scenario | Required pass condition | Primary requirements |
|---|---|---|---|---|
| CFGM-T01 | Integration | Upgrade to a release that adds options | New options apply their defaults with no operator edits | CFGM-001 |
| CFGM-T02 | Unit | Rename and move migrations | Values and operator comments are preserved; a backup and diff are produced | CFGM-002 |
| CFGM-T03 | Failure | A migration or validation fails | Operator files are byte-identical to before; the update aborts before restart | CFGM-003 |
| CFGM-T04 | CLI | `new-options` and `--annotate` | The output lists exactly the added options; annotation is idempotent | CFGM-004 |
| CFGM-T05 | Integration | Skip several releases in one upgrade | Every intermediate migration applies in order | CFGM-002 |

### 28.5 Field data from production

Whenever development needs information from production, a collection tool is built or extended first, so the operator runs one command instead of following manual steps.

| ID | Requirement |
|---|---|
| FIELD-001 | `vakr-fieldkit` shall use only the Python standard library and run on a production host without vakr installed. It ships as one self-contained zipapp (`vakr-fieldkit.pyz`) attached to every GitHub release, and runs on the project's Python version (3.15), obtained independently of the system Python. Before vakr is installed, the operator runs it with `uv run --python 3.15 vakr-fieldkit.pyz`. Once vakr is installed, `vakr field collect` runs it with the bundled interpreter. External commands such as `snmpwalk` and `journalctl` are called with argument lists only, never through a shell. |
| FIELD-002 | The data-driven collection profiles are: <br>• `host`: OS, kernel, CPU, memory, disks, time sync, `ping_group_range`, Python version, and HTTPS reachability to GitHub; <br>• `snmp`: bounded walks of the system, IF-MIB, IP-MIB, and any named extra subtrees for listed devices, rate-limited to protect device control planes; <br>• `paths`: ICMP RTT and loss to the peer position and to sample devices; <br>• `vakr`, when installed: version, redacted effective configuration, `doctor` output, health snapshot, recent journald events, schema versions, and storage statistics. <br>A new need adds a profile, not a new tool. |
| FIELD-003 | The output shall be one bundle, `vakr-field-<host>-<UTC timestamp>.tar.gz`. Its `manifest.json` lists the tool version, profiles, non-secret parameters, timings, and errors, plus a SHA-256 for every member. |
| FIELD-004 | SNMP communities and other credentials shall be read from a protected local file or an interactive prompt, never from command-line arguments, and shall never be written into the bundle. A redaction test corpus proves this. |
| FIELD-005 | `--pseudonymize` shall replace IP addresses, MAC addresses, hostnames, `sysContact`, `sysLocation`, and free-text descriptions with consistent pseudonyms. It uses a keyed HMAC whose key stays on the production host, and it preserves structure so captures remain valid simulator fixtures. |
| FIELD-006 | Bundles are encrypted by default to the development host's recipient certificate, which ships with the tool. The output is a standard CMS AuthEnvelopedData file (RFC 5083) named `*.tar.gz.p7m`, using AES-256-GCM content encryption (RFC 5084) and an RSA-3072 recipient key with RSA-OAEP/SHA-256 key transport. Encryption runs `openssl cms`, called with an argument list; OpenSSL 3.0 or newer is required and preflight checks for it. Only the development host can decrypt the bundle, so the transfer location doesn't need to be trusted, and GCM authentication means a modified bundle fails to decrypt. `--no-encrypt` exists only for bundles that stay on the production host. |
| FIELD-007 | For transfer, the operator either places the encrypted bundle at any HTTPS URL this host can reach (`tools/field-inbox fetch <url>`) or copies it with `scp` into `~/vakr-inbox/` on this host. |
| FIELD-008 | `tools/field-inbox` shall decrypt with the recipient private key stored outside the repository (`~/.config/vakr-field/recipient-key.pem`, mode `0600`), verify the manifest checksums, extract into the git-ignored `inbox/<bundle-id>/`, and print a summary. It rejects archive members with absolute paths, `..` components, or links. |
| FIELD-010 | The field kit's GitHub reachability test (FIELD-002 `host` profile) shall accept the same proxy and CA options as vakr (`--proxy`, `--ca-file`). It shall report the issuer of the certificate presented for `github.com`, so the development side learns whether an enterprise TLS-inspection proxy is in the path before vakr is installed. |
| FIELD-009 | The field kit shall change nothing on the production host, finish within bounded time, and end by printing one line that names the output file and the next step. |

### 28.6 Packaging

| ID | Requirement |
|---|---|
| PKG-001 | The MVP shall ship as `vakr_<version>_amd64.deb` for Ubuntu 26.04 LTS, built with nfpm from a packaging spec that will also produce the later `.rpm`. |
| PKG-002 | The package installs its files under `/opt/vakr`: a relocatable CPython build of the project's pinned Python version (`python/`), a locked virtual environment (`venv/`), defaults (`share/defaults/`), release signers (`share/release-signers`), unit-file sources (`share/systemd/`), and documentation (`share/doc/`). The only files outside `/opt/vakr` are the integration points the OS requires to be in system paths: systemd units in `/usr/lib/systemd/system/`, `sysusers.d` and `tmpfiles.d` entries in `/usr/lib/`, and the `/usr/bin/vakr` symlink. Operator configuration (`/etc/vakr`), state (`/var/lib/vakr`), and cache (`/var/cache/vakr`) are created by the package but never overwritten by it. |
| PKG-003 | Maintainer scripts create the `vakr` system user and group, `/var/lib/vakr`, `/var/cache/vakr`, and `/etc/vakr` with `secrets/` at mode `0750`. On first install, collection does not start until `vakr setup` has created the first admin and set the node role and position. On upgrade, only services that were running are restarted. |
| PKG-004 | Package builds are reproducible from the lockfile in a clean `ubuntu:26.04` container. They produce no `lintian` errors except tags overridden with a written justification in the package's lintian overrides, such as `dir-or-file-in-opt` (the owner chose `/opt/vakr`) and bundled-interpreter tags. |
| PKG-005 | `apt remove` keeps configuration and data. `apt purge` removes configuration but keeps the telemetry history in `/var/lib/vakr`, and prints a notice. Deleting that history requires `vakr data purge`. |
| PKG-006 | RHEL-family `.rpm` packaging (AlmaLinux, Rocky Linux, RHEL) starts only after the MVP reaches production-level robustness and can replace basic MRTG and SmokePing functionality (MVP-ACC-10). |

### 28.7 Release process

1. Run `tools/check-updates` (TOOLS-001). Apply the updates, or record each deferral in the changelog.
2. Bump `VERSION`, and update `CHANGELOG.md` with breaking changes, migrations, and new options.
3. Confirm CI is green on `main`.
4. Run `tools/release`. It builds the `.deb` in a clean container, writes `SHA256SUMS`, signs it with the release SSH key (`ssh-keygen -Y sign -n vakr-release`), tags `v<version>`, and creates the GitHub release. Releases are marked pre-release until MVP acceptance unless declared stable.
5. Smoke-test a self-update from the previous release in a container (UPD-T01).
6. Documentation publishes to GitHub Pages automatically.

**Release trust (ADR-026).** The release signing key:

- is passphrase-protected and held on the development host, not in GitHub secrets;
- is listed in the package in `allowed_signers` format, so verification needs only `ssh-keygen`, which is present on every supported host;
- is rotated by shipping the new signer in a release signed with the old key.

CI may build artifacts for testing but cannot sign releases.

### 28.8 Tool and dependency currency

| ID | Requirement |
|---|---|
| TOOLS-001 | `tools/check-updates` shall report newer versions of Python, Go, uv, ruff, ty, nfpm, locked Python dependencies, Go modules, pinned GitHub Actions, and vendored front-end assets (ECharts, HTMX), with links to release notes. |
| TOOLS-002 | Each release runs `tools/check-updates`, and each available update is either applied or deferred with a reason in the changelog. Automated update PRs (Dependabot or Renovate) may supplement this but do not replace it. |
| TOOLS-003 | Moving to a new Python minor version is a normal release change. `.python-version`, `requires-python`, the bundled interpreter, and the CI matrix change together, and the release notes say so. |

### 28.9 Documentation publishing

| ID | Requirement |
|---|---|
| DOCPUB-001 | Every `.md` file in the repository shall be published as HTML to GitHub Pages on each push to `main`, using `tools/md2html`. |
| DOCPUB-002 | The Pages build shall fail on broken internal links or anchors (`md2html --check`). This also implements QG-009 for Markdown. |
| DOCPUB-003 | Generated HTML shall be self-contained (no CDN), readable at phone width, and support light and dark themes. Each page links to its source on GitHub. |
| DOCPUB-004 | Every page shall show a link to the GitHub repository at the top. Every page shall also have a navigation index down the left side that lists all published pages, grouped by directory, with the current page marked. At phone width the index collapses behind a toggle and works without JavaScript. |

---

## 29. Portable scaffolding tools

### 29.1 Rule

Tools that support development and operations, rather than network monitoring itself, shall be built so another project can reuse them with configuration changes only. Each such tool shall:

1. import nothing from vakr's domain, application, or adapter packages;
2. take project-specific values (names, repository, paths, key files, profiles) from arguments or a small config file, never from constants;
3. have its own README, its own tests, and an Apache-2.0 header;
4. be a small self-contained directory that another project can vendor by copying it: one entry-point script, plus sibling modules and templates where the 300-line limit (section 17.2) requires them. Development tools declare dependencies with PEP 723 inline metadata in the entry point. Tools that run on production hosts use only the Python standard library. All tools target the project's Python version and get it from `uv` or the bundled interpreter, never from the OS, so no tool supports an older Python;
5. move to its own repository once a second project adopts it (ADR-030).

An import-boundary test in CI enforces rule 1 for code under `src/vakr/lifecycle/` and `tools/`.

### 29.2 Inventory

| Tool | Purpose | Runs on | Location | Form |
|---|---|---|---|---|
| md2html | Markdown → HTML site with link checking | Development host, CI | `tools/md2html/` | Entry script, render module, and template |
| check-updates | Reports newer toolchain and dependency versions | Development host | `tools/check-updates/` | Entry script plus sibling modules, config-driven |
| check-version | Enforces the single version source (VER-003) | CI | `tools/check-version/` | Single file |
| release | Builds, checksums, signs, tags, and publishes | Development host | `tools/release/` | Single file, config-driven |
| fieldkit | Collects production data into one encrypted bundle | Production | `tools/fieldkit/`, also `vakr field collect` | Single file, standard library only |
| field-inbox | Fetches, decrypts, verifies, and extracts bundles | Development host | `tools/fieldkit/` | Single file |
| self-update | Verified GitHub-release updater with pre- and post-install hooks | Production | `src/vakr/lifecycle/update/` | Library plus CLI, parameterized by repository, package, signers, and hooks |
| config-upgrade | Overrides-only layering, versioned TOML migrations, new-option reporting | Production | `src/vakr/lifecycle/configmerge/` | Library; migrations are data |

---

## Appendix A. Terminology

| Term | Meaning |
|---|---|
| Collector | A monitoring host or logical observation point with stable identity. |
| Worker | A process that schedules and performs polling within a collector. |
| DevicePoll | One scheduled attempt to observe one device. |
| Stage 1 SNMP | `sysUpTime` plus exact interface admin/oper state checks. |
| Stage 2 SNMP | Counter collection for interfaces confirmed up/up. |
| Native telemetry | Full-resolution observations retained during the native window. |
| Rollup | Aggregated historical observation for a longer bucket. |
| Current state | Latest derived operational state, including age and quality. |
| Stale data | Data older than the freshness threshold; not equivalent to down. |
| Stale interface | Interface lifecycle state after prolonged inactivity, distinct from data staleness. |
| Quality flag | Explicit evidence about completeness, baseline validity, collector/storage condition, or confidence. |
| Circuit interface | An interface explicitly annotated as a WAN/service-provider circuit endpoint for monitoring views. |
| Sticky interface | An interface intentionally displayed at the top of a device’s interface list. |
| Observation point | Logical network vantage point represented by a collector identity. |
| Not attempted | Work the collector did not execute; it is unknown, not a target failure. |

## Appendix B. Requirements traceability template

```markdown
| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| ICMP-004 | Implemented | src/vakr/... | ICMP-T03, ICMP-T04 | docs/... | — |
```

Statuses: `Not started`, `In progress`, `Implemented`, `Verified`, `Deferred-approved`, `Not applicable-approved`.

## Appendix C. Initial ADR backlog

| ADR | Decision | Initial status |
|---|---|---|
| ADR-001 | Focused MRTG/SmokePing successor; not a full NMS | Accepted baseline |
| ADR-002 | Python asyncio initial implementation; Go only after profiling | Accepted baseline |
| ADR-003 | Persistence profiles and repository abstraction | Accepted by this consolidation; owner review recommended |
| ADR-004 | Immutable device/interface UUIDs; `ifIndex` observed only | Accepted baseline |
| ADR-005 | Two-stage SNMP status then conditional counters | Accepted baseline |
| ADR-006 | Aggregate packet components; persist only total packet counters/rates | Accepted baseline |
| ADR-007 | Scheduler phase/order modes | Accepted baseline |
| ADR-008 | Durable append spool; volatile memory not sole durability | Accepted baseline |
| ADR-009 | Enterprise 30-day native and 15-minute rollup through day 400 | Accepted baseline |
| ADR-010 | Local auth with admin and operator/user roles | Accepted baseline |
| ADR-011 | Alerting remains external | Accepted baseline |
| ADR-012 | Adaptive diagnostics always on; interval changes opt-in | Accepted baseline |
| ADR-013 | FastAPI/Jinja/HTMX/ECharts web stack and Textual TUI | Proposed default; approve in Phase 1 |
| ADR-014 | MessagePack append-segment spool envelope | Proposed default; validate in Phase 10 |
| ADR-015 | Interface reconciliation weights and auto-match thresholds | Required before Phase 6 completion |
| ADR-016 | ClickHouse physical schema, ordering, partitioning, codecs, projections | Required from benchmark before Enterprise release |
| ADR-017 | `snmp_fallback`/`snmp_only` reachability strategies | Proposed extension; included opt-in in MVP; owner confirmation before v1 |
| ADR-018 | Apache-2.0 license | Accepted 2026-10-19 |
| ADR-019 | Product name `vakr` | Accepted 2026-10-19 |
| ADR-020 | Public dashboard exposure posture and optional network restrictions | Owner decision before production publication; deferred past MVP |
| ADR-021 | Compact profile: SQLite telemetry with time partitioning for 30/400-day retention | Proposed; benchmark at 2× the MVP workload (two positions replicated) required before milestone M3 |
| ADR-022 | MVP walking-skeleton milestone order (section 27.8) supersedes the phase order until MVP acceptance | Accepted 2026-10-19 |
| ADR-023 | Unprivileged ICMP datagram sockets; no `CAP_NET_RAW` by default | Accepted 2026-10-19 |
| ADR-024 | Multi-position: independent full nodes, metadata primary, pull replication of observations | Proposed; validate in milestone M5 |
| ADR-025 | Packaging with `nfpm`; all installed files under `/opt/vakr` (bundled relocatable interpreter, venv, defaults, signers), with only OS integration points outside it | Accepted 2026-10-20 (owner: install under `/opt/vakr`); validate in milestone M1 |
| ADR-026 | Release trust: `SHA256SUMS` signed with an SSH signing key held off GitHub, verified with `ssh-keygen -Y verify` | Proposed; validate in milestone M1 |
| ADR-027 | Configuration layering and upgrade merge (overrides-only operator files, packaged defaults, versioned config migrations) | Accepted 2026-10-19 |
| ADR-028 | Field data bundles: stdlib-only collector; CMS AuthEnvelopedData with AES-256-GCM and RSA-OAEP via `openssl cms`; transfer over HTTPS or scp. The Python `cryptography` module was evaluated: its PKCS#7 API (50.0.2) supports only AES-CBC EnvelopedData, so it cannot produce or read GCM CMS. vakr uses it for certificate inspection (EGRESS-003) instead. | Accepted 2026-10-20 (owner: openssl cms / cryptography instead of age) |
| ADR-029 | Single `VERSION` file as the only version source | Accepted 2026-10-19 |
| ADR-030 | Portable scaffolding tools and their reuse boundary (section 29) | Accepted 2026-10-19 |
| ADR-031 | Compatibility policy: breaking changes accepted until post-MVP enterprise readiness; config and data migrations remain mandatory | Accepted 2026-10-19 |
| ADR-032 | Outbound HTTPS through enterprise proxies and TLS-inspection CAs; verification never disabled (section 28.3.1) | Accepted 2026-10-20 |
| ADR-033 | Interface classification from description keywords, with vakr-side tags for the rest (META-012, META-013) | Accepted 2026-10-20 |

## Appendix D. Recommended first implementation milestone

> **Revision 1.1:** For the MVP, this milestone's content is distributed across milestones M1 and M2 in section 27.8. M1 additionally ships an installable, self-updating `.deb` before any monitoring feature exists. The list below remains the foundation checklist.

The first milestone shall produce a repository that:

- installs with `uv` on Ubuntu;
- passes format, lint, type, docstring, LOC, unit, security, and docs gates;
- loads and validates the reference TOML;
- resolves a protected example credential without exposing it;
- creates/migrates a Lite SQLite database through repository ports;
- imports a small simulated inventory;
- starts a minimal authenticated API/web health page and `vakr doctor`;
- includes the requirements traceability skeleton and first teaching tutorial.

It shall not claim network-monitoring completion until the later collection phases pass their tests.

## Appendix E. Default values and decision tables

These are the initial defaults for behavior the requirements call "configurable". Tests are written against them. Changing a default is a normal reviewed change: update this appendix, the packaged defaults (CFGM-001), and the affected tests together.

### E.1 ICMP reachability state (five probes)

| Replies received | Default state | SNMP under `icmp_gated` |
|---|---|---|
| 5 | `REACHABLE` | Eligible |
| 4 | `REACHABLE`, quality flag `PARTIAL_LOSS` | Eligible |
| 3 or 2 | `IMPAIRED` | Eligible |
| 1 | `IMPAIRED`, quality flag `SINGLE_REPLY` | Skipped |
| 0 | `UNREACHABLE`; `UNKNOWN` if the poll was not attempted or the collector was unhealthy | Skipped |

Keys: `icmp.impaired_loss_percent = 40` (loss at or above this is `IMPAIRED`), and `icmp.impaired_rtt_ms`, which is unset by default. When set, a median RTT above it makes the state `IMPAIRED`.

Rationale: Cisco IOS XE Control Plane Policing commonly rate-limits ICMP to the route processor, so losing one probe in five is routine and should not turn a device amber. `UNREACHABLE` is reserved for zero replies, because a single reply proves the device is reachable.

### E.2 Device status (first matching row wins)

| # | Condition | Bucket and color | Label |
|---|---|---|---|
| 1 | Admin state `DISABLED` or `RETIRED` | Offline, gray | Disabled / Retired |
| 2 | Admin state `MAINTENANCE` | Offline, gray | Maintenance (collection continues) |
| 3 | No fresh observation (E.3), last poll `NOT_ATTEMPTED`, or collector `DEGRADED`, `OVERLOADED`, or `UNAVAILABLE` | Attention, amber | Unknown — reason |
| 4 | ICMP `UNREACHABLE` and SNMP not `RESPONDING` | Attention, red | Down |
| 5 | ICMP `UNREACHABLE` and SNMP `RESPONDING` (`snmp_fallback`) | Attention, amber | ICMP lost, SNMP OK |
| 6 | ICMP `IMPAIRED` | Attention, amber | Packet loss / Latency |
| 7 | ICMP `REACHABLE` and SNMP `TIMEOUT` or `ERROR` | Attention, amber | SNMP failing |
| 8 | SNMP `PARTIAL`, incomplete discovery, or pending identity review | Attention, amber | Partial data |
| 9 | Uptime `RESET_DETECTED` within `status.recent_restart_window = "60m"` | Attention, amber | Restarted |
| 10 | Otherwise | Active, green | OK |

Rows referring to ICMP are skipped for `snmp_only` devices, and rows referring to SNMP are skipped for `icmp_only` devices.

**Composite status across positions (MPOS-007):**

- Red "Down" only when every collector with a fresh observation evaluates to row 4.
- Amber "Reachable from <position> only" when at least one collector evaluates to row 4 and another evaluates to a reachable state.
- Otherwise, the most severe per-collector result.

### E.3 Freshness

An observation is stale when its age exceeds `max(2.5 × effective_interval, effective_interval + 30 s)`, which is 150 s at the 60 s default. A single late or missed poll does not make data stale; two do.

### E.4 Counter baselines and plausibility

- Maximum baseline gap: `5 × effective_interval` for 64-bit (HC) counters. For 32-bit counters it is the smaller of that and half the wrap time at effective capacity. A longer gap invalidates the baseline (RATE-003).
- Plausibility: a derived rate above `1.10 × effective capacity` is kept and flagged `RATE_EXCEEDS_CAPACITY` (RATE-009).
- Per-device SNMP request sizing: start at 30 varbinds per PDU and halve after a `tooBig` or a timeout that suggests size trouble, down to 5 (SNMP-017).

### E.5 Uptime continuity

With `prev` and `curr` in TimeTicks (hundredths of a second) and `elapsed` the wall-clock seconds between the two successful observations:

- `expected = (prev + 100 × elapsed) mod 2^32`
- `tolerance = max(10 s, 1% of elapsed)`, expressed in ticks
- If `|curr − expected| ≤ tolerance`, the result is `CONTINUOUS`. This also covers a legitimate wrap, every ~497 days.
- Otherwise, if `curr < expected − tolerance`, the result is `RESET_DETECTED`, with the evidence recorded.
- Otherwise the result is `UNKNOWN` with quality flag `UPTIME_AHEAD`, which points to a device or collector clock problem.

### E.6 Interface lifecycle triggers

| State | Entered when |
|---|---|
| `ACTIVE` | Present in the latest discovery, and admin up and oper up at the last Stage 1 poll |
| `INACTIVE` | Present but not up/up (admin down, or oper `down`, `lowerLayerDown`, `notPresent`, or `dormant`), or missing from the latest discovery (reason `NOT_DISCOVERED`). Stage 1 status checks continue per policy. |
| `STALE` | `INACTIVE` continuously for `lifecycle.stale_after = "10d"`. Status checks drop to the discovery cadence (LIFE-004). |
| `RETIRED` | Admin action only in the MVP. Automatic retirement is off. |

An interface returns to `ACTIVE` on the first up/up Stage 1 result, or when rediscovery reconciles it to an existing identity.

### E.7 ICMP summary rounding

Summary values (min, average, median, max, jitter) are computed in floating point from integer-microsecond samples and stored as integer microseconds, rounded half to even. Loss percent is stored to one decimal place, also rounded half to even.

### E.8 Spool durability

- Group commit: `fsync` every `spool.fsync_interval = "1s"` or every 1,000 records, whichever comes first.
- An observation counts as durably handed off only after the `fsync` that covers it.
- Segments rotate at 64 MiB.
- Replay acknowledges whole batches by batch UUID.
- An abrupt power loss can therefore lose at most about one second of completed observations. Recovery detects the lost range and records a telemetry-loss incident (STORE-007).

### E.9 Collection timing

| Setting | Default |
|---|---|
| ICMP probes / spacing / per-probe timeout | 5 / 200 ms / 1 s (cycle completes within about 2 s) |
| SNMP timeout / retries | 2 s / 1 (SNMP-011) |
| Per-device outstanding SNMP PDUs | 1 (SNMP-012) |
| Discovery walk GETBULK max-repetitions | 25 |
| Rediscovery interval | 24 h (DISC-002) |
| Capability re-probe interval | 7 d (DISC-009) |

### E.10 Rollups and retention

- Bucket width: 15 minutes, aligned to UTC.
- A bucket is complete once its end is more than 10 minutes in the past.
- Native rows expire only after a complete rollup covers them (ROLL-T04).
- Compact native partitions are dropped one UTC day at a time, after day 30.

### E.11 Scheduler hashing

Phase and order hashes use the first 8 bytes of SHA-256 over UTF-8 input (`collector_name` or `collector_name + "/" + device_uuid`), read as a big-endian integer, so Python and Go produce identical schedules. Python's built-in `hash()` shall not be used, because it is salted per process.

### E.12 Authentication and sessions

| Setting | Default |
|---|---|
| Password hashing | Argon2id using `argon2-cffi` `PasswordHasher` defaults (RFC 9106 low-memory profile). Parameters are stored in each hash, and a hash is rehashed on the next login after parameters change (SEC-009). |
| Session idle / absolute expiry | 30 minutes / 12 hours |
| Kiosk sessions | Separate operator-role kiosk tokens; idle expiry disabled; absolute expiry 30 days |
| Login throttling | Progressive delay starting at the 5th consecutive failure per account and source address, capped at 30 s |
| Local TUI/CLI authorization | Unix socket `/run/vakr/api.sock` with `SO_PEERCRED`: root and members of `vakr-admin` get admin; members of `vakr` get operator; everyone else is refused. |

### E.13 Replication (two positions)

| Setting | Default |
|---|---|
| Observation pull interval / batch | 30 s / at most 10,000 observations |
| Metadata pull interval | 60 s |
| Replication lag warning / critical | 5 minutes / 30 minutes |
| Backfill pacing | Uses no more than 50% of ingestion capacity while local ingestion has a backlog |

### E.14 Updates

| Setting | Default |
|---|---|
| Update timer | Disabled; when enabled, runs daily with `RandomizedDelaySec=4h` |
| Channel | `prerelease` until MVP acceptance, then `stable` |
| Cached previous packages for rollback | 2 |

### E.15 Interface description keywords (META-012)

| Keyword | Tag | Interest | WAN circuit |
|---|---|---|---|
| `ISP` | `isp` | primary | yes |
| `WAN` | `wan` | primary | yes |
| `UPLINK` | `uplink` | primary | no |

Operators add keywords in `[[interface_classification.keywords]]`. Setting `interface_classification.use_default_keywords = false` replaces the defaults instead of extending them. Keywords match whole tokens case-insensitively; `re:`-prefixed keywords follow POLICY-005.
