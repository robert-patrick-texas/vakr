# vakr features

vakr is a network performance and reachability monitor for Linux. It does the jobs of MRTG (interface traffic history) and SmokePing (latency and loss history) in one product, and adds automatic SNMP discovery, interface identity that survives `ifIndex` changes, a web UI with sign-in, a terminal UI, a JSON API, and collection that keeps running through database outages and network partitions.

This page lists what the current release does, grouped by functional area, and ends with a [comparison with MRTG and SmokePing](#compared-with-mrtg-and-smokeping). Each area links to the guide that explains it. What changed in each release is in the [changelog](../CHANGELOG.md).

The current release is the MVP (blueprint [section 27](../tool-blueprint.md#27-mvp-definition)). It is built and tested for:

| | |
|---|---|
| Devices | Cisco IOS XE over SNMPv2c. Other devices with standard IF-MIB and IP-MIB support are polled the same way, but are not validated yet. |
| Scale | 100 devices and about 1,600 active interfaces per node |
| Platform | Ubuntu 26.04 LTS on amd64, as one `.deb` |
| Storage | SQLite (the Compact profile) |
| Positions | One node, or two nodes (for example east and west) that keep collecting through a partition |

Items marked **Planned** are designed in the blueprint but come after the MVP. See [Planned after the MVP](#planned-after-the-mvp).

## Contents

- [Reachability and latency (ICMP)](#reachability-and-latency-icmp)
- [SNMP discovery and interface identity](#snmp-discovery-and-interface-identity)
- [Interface traffic, errors, and state](#interface-traffic-errors-and-state)
- [Data you can trust](#data-you-can-trust)
- [Inventory and classification](#inventory-and-classification)
- [Storage, retention, and durability](#storage-retention-and-durability)
- [Web UI and graphs](#web-ui-and-graphs)
- [Dashboards, rankings, and kiosk displays](#dashboards-rankings-and-kiosk-displays)
- [Terminal UI and command line](#terminal-ui-and-command-line)
- [API and integrations](#api-and-integrations)
- [Two positions](#two-positions)
- [Accounts and security](#accounts-and-security)
- [Installation, updates, and operations](#installation-updates-and-operations)
- [Planned after the MVP](#planned-after-the-mvp)
- [Out of scope](#out-of-scope)
- [Compared with MRTG and SmokePing](#compared-with-mrtg-and-smokeping)

## Reachability and latency (ICMP)

Guide: [ICMP](operations/icmp.md).

- **SmokePing-style probe groups.** Every poll sends five ICMP Echo probes 200 ms apart and waits up to 1 second for each reply. Each probe's round-trip time is kept, not just an average.
- **Latency, jitter, and loss** for every poll: median round-trip time, jitter, and packet loss.
- **Reachability states.** A device is reachable, impaired (40% loss or more by default, or above an optional latency limit), or unreachable.
- **IPv4 and IPv6**, following the device's management address.
- **Three reachability strategies.** `icmp_gated` (the default) polls SNMP only after ICMP replies. `snmp_fallback` and `icmp_only` are available for devices that need them.
- **No root needed.** vakr uses unprivileged ICMP sockets (`net.ipv4.ping_group_range`), with `CAP_NET_RAW` for the collector as a fallback.
- **`vakr icmp probe`** runs one reachability cycle by hand and shows exactly what the collector would see from this host.

## SNMP discovery and interface identity

Guides: [SNMP](operations/snmp.md), [inventory](operations/inventory.md#interface-identity-after-rediscovery).

- **Automatic discovery** over SNMPv2c of the system group, every interface (name, description, type, speed, MAC, state), and interface IP addresses (IF-MIB and IP-MIB).
- **Scheduled rediscovery** every 24 hours by default, spread across the day, plus rediscovery on demand from the web UI or `vakr inventory rediscover`.
- **Persistent interface identity.** Each interface keeps one identity across `ifIndex` renumbering and reboots. vakr matches it by name, `ifIndex`, MAC, and addresses, so its history continues.
- **No silent merges.** When rediscovery can't match an interface with certainty, the case goes to an admin's identity review in the web UI or `vakr interface review`, and nothing is merged until it is decided.
- **Capability memory.** vakr remembers what each device can't do, such as interfaces without 64-bit counters, and asks again every 7 days.
- **Careful with devices.** GETBULK walks are bounded. Request size halves after a `tooBig` answer, and every retry is counted.
- **`vakr snmp discover` and `vakr snmp poll`** check a device from the command line. A preparation guide for Cisco IOS XE is included.

## Interface traffic, errors, and state

Guides: [collector](operations/collector.md), [web UI](operations/web-ui.md#graphs).

- **Traffic, packets, and errors**, inbound and outbound, from 64-bit (HC) counters where the device has them.
- **Polled every 60 seconds by default.** A device or a policy rule may set another interval, from 30 seconds to 1 hour.
- **Rates from the real time between samples**, not the nominal interval.
- **No artificial reset spikes.** After a counter reset, an agent restart, or the first sample, the rate is left out instead of drawn as a spike. `sysUpTime` continuity detects agent restarts.
- **Utilization** against the interface's speed, or a capacity override, with the contracted rate shown for WAN circuits.
- **Status every poll.** Stage 1 reads uptime and interface status on every poll, and Stage 2 reads counters for interfaces that are up/up. A link that goes down shows as inactive within one poll.
- **State timeline.** The interface page shows admin and operational state over the last 7 days of the range, with a table of every change.
- **Interface lifecycle.** Each interface is active, inactive, stale (down for 10 days by default), or retired. Stale and retired interfaces keep their history.

## Data you can trust

Guides: [quality states](architecture/quality-states.md), [status and TUI](operations/status-and-tui.md#reading-stale-unknown-and-missing-values).

- **Missing data is never zero.** A missing or invalid value is a break in the graph line, never a drop to zero, and the same holds in exports, rankings, and metrics.
- **Quality markers on graphs.** Shaded stretches with a letter mark each problem: G gap, B invalid counter baseline, R agent restart, D counter discontinuity, P partial collection, C collector trouble, O above capacity.
- **Collector trouble is never device trouble.** If the collector, the spool, or the database has a problem, the device shows as unknown, never as down.
- **Freshness.** A value older than its freshness threshold (150 seconds at the default interval) is marked stale and never shown as current.
- **Device states that say what is wrong.** The device list separates Attention, Active, and Offline/Retired. vakr tells a device outage apart from an SNMP failure, collector overload or outage, stale data, and database trouble.
- **Polls on a fixed schedule.** Each device is polled on its own anchored slot, spread by hostname, and polls never overlap. Poll-health diagnostics show late and skipped polls.

## Inventory and classification

Guides: [inventory](operations/inventory.md), [policies](operations/policies.md).

- **Devices from a file under version control.** `/etc/vakr/inventory.toml` is checked with `vakr inventory validate`, shown as a plan with `plan`, and applied with `apply`.
- **Devices by hand** in the web UI, through the API, or with `vakr device`, including retiring a device.
- **Bulk import and export** of devices and interface annotations as CSV or TOML (`vakr inventory import` and `export`), with a plan before anything changes.
- **Sites, tags, and groups** for browsing and filtering. Each site can have its own time zone.
- **Interface annotations:** interest class (high priority), sticky ordering, WAN circuit, provider, circuit ID, contracted rate, capacity override, and comments.
- **Classification from descriptions.** Policy rules match interface descriptions and decide whether an interface is graphed, how important it is, and which tags it gets. `vakr policy explain` shows why a rule applied.
- **Suppressed interfaces stay known.** An interface excluded from collection stays in the inventory, keeps its history, and is still rediscovered.

## Storage, retention, and durability

Guides: [database](operations/database.md), [spool](operations/spool.md), [rollups](operations/rollups.md).

- **Two SQLite files** in `/var/lib/vakr`: `metadata.db` for inventory, accounts, and dashboards, and `telemetry.db` for observations.
- **A durable spool on local disk.** Every observation is written to the spool before the database, so polling carries on while the database is slow, locked, full, or being restored. The default 4 GiB spool holds about 40 hours of the MVP workload.
- **Power-cut safe.** The spool syncs to disk at least every second. After an abrupt stop, the collector recovers the spool and records an incident that bounds what may be missing.
- **30 days of one-minute data** for every interface and device.
- **400 days of 15-minute rollups.** Each rollup keeps the minimum, mean, 95th percentile, and maximum of traffic, so a 30-second spike is still visible as the maximum after a year. Latency keeps the minimum, median, 95th percentile, and maximum of every reply. Loss is computed from the summed probe counts, not averaged percentages.
- **Coverage in every rollup.** A quarter-hour with 10 of 15 samples shows as thin, not as complete. Native data is removed only once a rollup covers it.
- **Schema migrations** run on every install and upgrade. `vakr db status`, `vakr db backup`, and integrity checks come with the package.

## Web UI and graphs

Guides: [web UI](operations/web-ui.md), [web access](operations/web-access.md).

- **Pages:**
  - an overview of device states, collector and database health, high-priority interfaces, WAN circuits, and top utilization and errors;
  - device lists and device pages;
  - interface lists and interface pages;
  - sites, tags, groups, and providers;
  - dashboards, rankings, high-priority interfaces, and WAN circuits;
  - collector health and identity reviews;
  - global search, so any interface's history is two steps from the search box.
- **SmokePing-style latency graphs.** Each graph plots every probe's round-trip time as a dot (the "smoke"), the median as a line, and loss as bars, with one graph per collector position.
- **Traffic graphs** of utilization, packets, and errors, with optional capacity and contracted-rate lines.
- **Ranges from 1 hour to 400 days**, or any custom range, in the display time zone. The page states the resolution and any point cap.
- **Exact values.** A table under each graph gives the last, minimum, mean, and maximum of each series, and how many points are missing.
- **Downloads** of any graph's window as CSV or JSON.
- **Admin tasks in the browser:** add and edit devices, classify interfaces, decide identity reviews, and start a rediscovery.
- **Keyboard and screen-reader support.** A keyboard slider reads a graph point by point, and a table under each graph gives its exact values. Refreshes update in place without stealing focus.
- **Nothing to install in the browser.** The pages ship in the package and load nothing from the internet.
- **TLS through your reverse proxy.** vakr serves HTTP on loopback, and nginx (an example configuration is included) or your site's proxy terminates TLS.

## Dashboards, rankings, and kiosk displays

Guides: [dashboards](operations/dashboards.md), [kiosk](operations/kiosk.md).

- **Built-in dashboards:** overview, rankings, high-priority interfaces, and WAN circuits.
- **Custom dashboards** that admins build in the web UI from widgets, for example every core uplink on one page. An interface can be pinned to a dashboard from its own page.
- **Rankings** of top utilization (95th percentile of the window against capacity) and top errors, 10 to 100 rows. Each row shows its coverage, the interfaces left out and why, and links to CSV and JSON downloads.
- **Kiosk displays.** A wall screen shows a dashboard, or several in rotation, without anyone signing in. It uses a read-only kiosk token that an admin creates, lists, and revokes (`vakr kiosk`). Tokens expire after 30 days by default.

## Terminal UI and command line

Guides: [status and TUI](operations/status-and-tui.md), [collector](operations/collector.md).

- **`vakr tui`**, a full-screen terminal UI for use over `ssh`: device list, device detail, and health. It has search, filters, and background refresh every 10 seconds by default.
- **`vakr status`** prints the same view as a table, or as JSON for scripts.
- **`vakr collector once`** runs one diagnostic poll of a device and prints what the collector would see, without writing anything.
- **`vakr doctor`** checks the host, and `vakr doctor --network` checks the path to GitHub step by step.
- **Configuration commands:** `vakr config validate`, `show-effective`, `upgrade`, and `new-options`.

## API and integrations

Guides: [API](operations/api.md), [metrics](operations/metrics.md), [acceptance](operations/acceptance.md).

- **A JSON API (version 1)** with an OpenAPI document. The web UI, the TUI, and the CLI all read monitoring state through it, so your own tools see exactly what they see.
- **Two ways in.** A local socket serves the CLI and TUI, and HTTP behind the reverse proxy serves remote clients, with sessions and CSRF protection.
- **History exports** as CSV and JSON, in UTC ISO 8601 timestamps, with bounded sizes.
- **`vakr check health` for CheckMK and Nagios.** It uses the standard exit codes (0 OK, 1 warning, 2 critical, 3 unknown) and covers the collector, the spool, ingestion, rollups, replication, and updates.
- **Prometheus metrics** about vakr itself: poll durations and lateness, spool fill, ingestion and rollup lag, and API load. A metric that can't be read disappears instead of reading zero.
- **Acceptance evidence.** `vakr acceptance schedule`, `gaps`, and `coverage` compute, from vakr's own data, whether polls ran on time, what was missed and why, and how complete the data is.

## Two positions

Guide: [two positions](operations/two-positions.md).

- **Two complete nodes** at two vantage points, for example east and west. Each one polls every device and serves its own web UI, TUI, and kiosk displays.
- **Metadata replication.** One node is the primary and owns inventory, accounts, and dashboards. The peer copies them from the primary every 60 seconds.
- **Observation replication.** Each node pulls the other's observations. After a partition heals, both fill in the history the other collected, and nothing is duplicated.
- **Per-position views.** Latency and traffic are never merged across positions, because the path differs. Each position is its own series on the graph.
- **Honest composite status.** A device is Down only when every position with fresh data says so. A device that only one position can reach shows, for example, "Reachable from east only".
- **Role changes.** `vakr node promote` and `vakr node demote` swap the roles on purpose or after losing the primary. `vakr replication status` and the health checks show lag and backlog.

## Accounts and security

Guides: [users](operations/users.md), [credentials](operations/credentials.md).

- **Local accounts** with two roles. An admin manages accounts, inventory, policies, dashboards, kiosk tokens, rediscovery, and identity reviews. An operator reads everything and changes nothing.
- **Passwords** hashed with Argon2id, at least 15 characters (NIST SP 800-63B), and never accepted as command arguments.
- **Sessions** end after 30 minutes idle or 12 hours in all. Failed logins are throttled per account and address.
- **Audit records** of every change, with the operator who made it.
- **Secrets in one protected file**, `/etc/vakr/secrets/credentials.toml`. Device definitions only name a credential. No log, error, API answer, bundle, or audit record ever contains a secret value.
- **Signed releases.** Every release is signed, and self-update installs nothing whose signature fails against the key in the installed package.

## Installation, updates, and operations

Guides: [install](operations/install.md), [updates](operations/updates.md), [configuration](operations/configuration.md), [field kit](operations/field-data.md).

- **One self-contained `.deb`.** It installs everything under `/opt/vakr`, including its own Python, so host upgrades don't affect it. It runs as systemd services under a dedicated `vakr` user.
- **`vakr setup`** creates the first admin account on a new installation.
- **`vakr self-update`** from signed GitHub releases. It verifies the signature, backs up configuration and the metadata database, installs, checks health, and rolls back automatically on failure. An optional daily timer, a version hold, `--rollback`, an offline `--file` install, and support for proxies and enterprise TLS inspection are all included.
- **Configuration migrations.** `vakr config upgrade` applies a release's configuration changes and keeps your comments and formatting. `vakr config new-options` lists what a release added.
- **The field kit.** One read-only command collects facts about a production host, its devices, and vakr itself into one encrypted bundle for the developers. It can pseudonymize the bundle.
- **Parallel-run reports.** These compare vakr's data with MRTG and SmokePing during a side-by-side run. See [the parallel run](operations/field-data.md#the-parallel-run-with-mrtg-and-smokeping).

## Planned after the MVP

These are designed in the blueprint ([section 27.4](../tool-blueprint.md#274-scope)) but not in the current release:

- SNMPv3 (authPriv with SHA-2 and AES);
- PostgreSQL, TimescaleDB, and ClickHouse storage for larger deployments;
- public dashboards for anonymous viewers;
- live updates in the web UI (server-sent events; the web UI refreshes on a timer today);
- sign-in through OIDC or SSO;
- dashboards and sites managed from files;
- adaptive polling intervals;
- asynchronous bulk exports;
- multiple worker processes per collector;
- `.rpm` packages;
- a formal WCAG accessibility audit (the web UI is built to the accessibility requirements already).

## Out of scope

vakr is a performance and reachability monitor, not a general network management system ([blueprint 2.4](../tool-blueprint.md#24-explicit-non-goals-for-core-v1)). It does not do:
- NetFlow, IPFIX, or sFlow;
- syslog or SNMP traps;
- configuration backup or deployment;
- topology mapping;
- ticketing;
- server or application monitoring;
- vulnerability scanning;
- packet capture.

It also has no alerting or notification engine. CheckMK, Nagios, or Prometheus alert from `vakr check health` and the metrics endpoint.

## Compared with MRTG and SmokePing

vakr was built to take over the jobs of [MRTG](https://oss.oetiker.ch/mrtg/) and [SmokePing](https://oss.oetiker.ch/smokeping/), so it is tested against them in a parallel run before acceptance. The table compares their documented defaults and usual configuration, from the [MRTG configuration reference](https://oss.oetiker.ch/mrtg/doc/mrtg-reference.en.html) and the [SmokePing configuration guide](https://oss.oetiker.ch/smokeping/doc/smokeping_config.en.html) (checked 2026-10-28), with the current vakr release. Both tools are highly configurable, and add-ons extend them further.

| Capability | MRTG | SmokePing | vakr |
|---|---|---|---|
| Interface traffic | Yes: two values per target, usually in and out octets | No | Yes: traffic, packets, and errors, in and out, for every monitored interface |
| Latency and loss | No (only through an external script) | Yes: the main purpose | Yes: SmokePing-style probe groups and "smoke" graphs |
| Measurement interval | 5 minutes by default; shorter only with the RRDtool back end | Set per installation (`step`, commonly 300 seconds); 20 pings per round suggested | 60 seconds by default; 5 probes and the counters in the same poll |
| Measurement methods | SNMP v1 and v2c, and v3 when enabled (with Perl's Net::SNMP), or any external script | Many probe types: ICMP (FPing), DNS, HTTP, TCP, SSH, and more | ICMP Echo and SNMPv2c (SNMPv3 is planned) |
| Finding interfaces | `cfgmaker` writes the configuration once and is rerun by hand | Targets written by hand | Automatic discovery and daily rediscovery |
| Interface identity | `ifIndex` by default; name, description, IP, or MAC with `cfgmaker` options | Not applicable | A persistent identity matched across `ifIndex` changes, with admin review of unclear cases |
| Counter resets and reboots | Values above `MaxBytes` are ignored; a reset below that can still show as a spike | Not applicable | The rate is left out after a reset or restart, and the graph marks it |
| Missing data | Repeats the last value by default (`Unknaszero` logs zero instead) | Left blank in the RRD | A break in the line, with a marker that says why |
| Storage | Its own log format or RRD files, consolidated as they age | RRD files, consolidated as they age | SQLite, with a durable spool in front of it |
| History kept | High-resolution data for a day or two, then 30-minute, 2-hour, and daily averages for about 2 years | Set per installation; the suggested layout keeps 3.5 days at full resolution, then hourly for 180 days and 12-hourly for 360 days | 30 days at one minute, then 400 days of 15-minute minimum, mean, 95th percentile, and maximum |
| Peaks after aggregation | Averages; `WithPeak` adds the peak 5-minute values to the longer graphs | Averages, with minimums and maximums in the suggested layout | Minimum, mean, 95th percentile, and maximum in every rollup |
| Collection during outages | A missed run is lost | A missed round is lost | The spool keeps polling through database outages; two positions keep collecting through partitions |
| Several vantage points | One poller | A master with any number of slaves | Two complete positions that replicate each other |
| Graphs | Fixed daily, weekly, monthly, and yearly images | Preset ranges, zoom, and multi-host graphs | Any range from 1 hour to 400 days, quality markers, exact-value tables, and CSV and JSON downloads |
| Web interface | Static HTML pages and images rebuilt each run | A CGI program | A web application with search, filtering, rankings, dashboards, and kiosk displays |
| Sign-in and roles | None (left to the web server) | None (left to the web server) | Local accounts with admin and operator roles, sessions, throttled logins, and audit records |
| Terminal UI | No | No | `vakr tui` and `vakr status` |
| API | No | No | JSON API version 1 with an OpenAPI document |
| Alerting | Thresholds (`ThreshMaxI` and others) that run a program | Built-in alert patterns that send mail or run a program | None built in, by design; `vakr check health` and Prometheus metrics feed CheckMK, Nagios, or Prometheus |
| Self-monitoring | No | No | Collector, spool, ingestion, rollup, and replication health, and Prometheus metrics |
| Installation and updates | Distribution packages or source; configuration by hand | Distribution packages or source; configuration by hand | One signed `.deb` with self-update, rollback, and configuration migrations |
| Platforms | Any system that runs Perl | Any system that runs Perl | Ubuntu 26.04 LTS on amd64 |

**Where MRTG or SmokePing still do more.** The comparison is not one-sided:
- SmokePing measures DNS, HTTP, TCP, and other services, not just ICMP.
- MRTG can graph any OID or the output of a script. vakr graphs interface counters and ICMP results only.
- MRTG speaks SNMPv3 today, and vakr's SNMPv3 is planned.
- Both run on many platforms, have built-in threshold or alert actions, and have decades of use behind them.
- SmokePing can measure from many slave locations, where vakr has two positions.

**Running them side by side.** vakr can run beside an existing MRTG and SmokePing installation. During MVP acceptance, a 14-day parallel run compares their numbers interface by interface and device by device ([blueprint E.34](../tool-blueprint.md#e34-parallel-run-comparison-mvp-acc-03-mvp-acc-04), [parallel-run reports](development/parallel-run.md)).
