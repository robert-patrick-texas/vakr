# History, ranking, and search query services (M4)

The API and the web UI read telemetry and inventory through four framework-free services in `vakr.query`. They use only the metadata and telemetry ports, so they work on every backend that passes the contract suites, and they return immutable view models that convert to JSON with `vakr.query.to_jsonable`. The API can return those views as they are. The services are synchronous; an async caller wraps each call in `asyncio.to_thread`. Create each service once per process and share it, because the inventory index and the ranking scans are cached inside the service.

Defaults and bounds are listed in blueprint [Appendix E.28](../../tool-blueprint.md#e28-history-rankings-and-search-queries-m4). The current-state service `StatusQueries`, used by `vakr status` and `vakr tui`, is described in [status and TUI](../operations/status-and-tui.md).

| Service | Methods | Returns |
|---|---|---|
| `HistoryQueries(metadata, telemetry, settings=None, clock=None)` | `interface_graph(interface_id, kind=GraphKind.TRAFFIC, request=None)` | `GraphView`, or `None` for an unknown interface |
| | `device_latency(device_id, request=None)` | `LatencyView`, or `None` for an unknown device |
| | `interface_timeline(interface_id, request=None)` | `TimelineView`, or `None` |
| `RankingQueries(metadata, telemetry, settings=None, clock=None, index=None)` | `top_utilization(request=None)`, `top_errors(request=None)` | `RankingView[UtilizationRow]`, `RankingView[ErrorRow]` |
| `CatalogQueries(metadata, telemetry, settings=None, clock=None, index=None)` | `interface_list(request=None)`, `high_priority(limit=25, collector_id=None)`, `wan_circuits(scope=None, limit=500, collector_id=None)`, `search(text, per_kind=50)` | `InterfaceListView`, `SearchView` |
| `OverviewQueries(metadata, telemetry, settings=None, clock=None, *, index=None, incidents=None)` | `overview(window=RankingWindow.M15)`, `browse(scope, request=None)`, and everything `StatusQueries` has | `OverviewView`, `DeviceListView` |
| `vakr.query.export` | `export_view(view, ExportFormat.CSV or JSON)` | `ExportFile(filename, media_type, body, rows)` |

`metadata` is a `MetadataUnitOfWorkFactory`; `telemetry` opens a telemetry backend, or is `None` when no telemetry database exists yet. `settings` comes from `vakr.query.settings_from_config(config, hostname=socket.gethostname())`. Passing the hostname resolves an empty `scheduler.collector_name` to the local collector's name, as the collector does. `OverviewQueries.incidents` is a callable that returns recorded telemetry-loss incidents, for example `lambda: vakr.ingest.incidents.read_incidents(spool_dir)`. Without it, the telemetry-loss feed reports that it is not available.

Errors follow one pattern. A request that is out of bounds raises `ValidationError` with a `field`; the API maps it to its error envelope. When the metadata store is unreadable, the call raises `StorageError`. When the telemetry store is unreadable, the call does not raise. It returns a view with `available=False` and `reason="telemetry unavailable: ..."`, so storage trouble never reads as an idle link or a down device (invariant 2).

## Requests and bounds

`GraphRequest(range=TimeRange(...), source=SourceChoice.AUTO, collector_id=None, max_points=1200)`:

- `TimeRange(preset)` takes one of `1h`, `6h`, `24h` (the default), `7d`, `30d`, `90d`, or `400d`. The window ends at the request time, truncated to the second.
- `TimeRange(start=..., end=...)` is a custom range of 1 minute to 400 days. Both ends must carry a timezone and are converted to UTC. The range may end at most 5 minutes in the future. Anything else raises `ValidationError`; nothing is silently widened (API-006, SEC-019).
- `max_points` is 1 to 1,200 per series (E.19).
- `collector_id` asks for one position's series. Without it, traffic, packet, error, and utilization graphs use the local collector when it is known (27.5 #6), else every position. Latency graphs always show every position.

`RankingRequest(window=RankingWindow.M15, limit=10, collector_id=None, scope=InterfaceScope())` accepts the windows `15m` (the DASH-003 default), `1h`, `6h`, and `24h`, and a limit of 1 to 100. The window ends on a UTC alignment step: 1 minute for the 15-minute and 1-hour windows, 5 minutes for the longer ones. Requests in the same step reuse one scan, and `computed_at` says when that scan ran.

`InterfaceListRequest(search="", scope=InterfaceScope(), sort=InterfaceSort.PRIORITY, offset=0, limit=100, collector_id=None)` pages through results by offset with `total` and `next_offset`. A page holds 1 to 500 rows, and search text is at most 256 characters (API-005).

`InterfaceScope` filters by `device_id`, `site_id` (the interface's site override, else its device's site), `tag_id` (the interface's operator or description tags, or its device's tags), `group_id` (the device's groups), `provider` (case-insensitive exact name), `wan_only`, `interest`, and `lifecycle`. Retired interfaces and interfaces of retired devices are hidden unless `include_retired` is set. `DeviceScope` filters `browse` by site, tag, group, provider (devices with any interface of that provider), or admin state (UI-009).

## Source choice (10.6)

| Range | `auto` reads | Bucket width at 1,200 points |
|---|---|---|
| 1h, 6h | One point per observation (native) with E.19 gap points | none |
| 24h | Native, downsampled with a min/max envelope | 120 s |
| 7d | Native, downsampled | 600 s |
| 30d | Native, downsampled; anything before the native horizon comes from rollups | 3,600 s |
| 90d | 15-minute rollups for the whole window, re-bucketed | 7,200 s |
| 400d | 15-minute rollups, re-bucketed | 43,200 s |

The rule behind the table: `auto` uses rollups for windows longer than 30 days. Otherwise it reads one point per observation when the window holds at most `max_points` polls at the effective interval, and native data downsampled on the ladder (60 s to 1 day, `vakr.domain.downsampling`) when it holds more. `source=native` reads native data only, and anything before the native horizon shows as a gap because it has expired. `source=rollup` reads rollups for any window, at a width of at least 15 minutes, and refuses a store that keeps no rollups. Every series discloses its spans: `resolutions` holds the source and `bucket_seconds` for each part of the window, and `bucket_seconds` is `null` for one point per observation (STATE-005, UI-010).

The bucketing rules are shared by every backend through the new port method `HistoryQuery.downsampled_series(..., source=None)`. Each bucket point is the mean of its values, with `low`/`high` as the bucket minimum and maximum. When rollups are re-bucketed, a point is the valid-weighted mean, the minimum of the bucket minimums, and the maximum of the bucket maximums. The contract suite `tests/contract/contract_telemetry_history.py` holds the memory and SQLite adapters to identical answers.

## Gaps, quality, and markers

A missing value is `null`, never `0` (invariant 1).

- **Native gaps (E.19).** A native series has one point per stored observation. Where two consecutive points are more than 1.5 times the effective poll interval apart, the service inserts a `null` point one interval after the earlier one, so a graph breaks its line instead of drawing across the outage (STORE-012). The effective interval is the one on the position's latest poll record, else the device's override, else `polling.default_interval`.
- **Bucket gaps.** Every bucket in a bucketed span is present, and a bucket with no valid value is `null` (ROLL-T05).
- **Flags.** Each point lists its quality flags by name, such as `baseline_uptime_reset`.
- **Markers.** Each series has `markers`: stretches with a `kind`, `start`, `end`, and operator `text`. The kinds are `restart` (agent restart reset the counters), `discontinuity` (counter discontinuity, width change, decrease, or unresolved identity), `baseline` (first sample, missing prior, baseline gap, or non-advancing time), `partial` (partial SNMP reply or missing fields), `collector` (collector degraded or poll not attempted), `over_capacity` (a rate above 110% of capacity, kept), and `gap` (no data, including empty stretches at the start or end of the window). Consecutive markers of one kind are merged.

## Units, positions, and reference lines

Values are always in the unit named by `unit.base`: `bit/s`, `pkt/s`, `err/s`, `%`, or `us` (microseconds, for RTT and jitter). `unit.display` and `unit.divisor` suggest the axis unit. Rates use SI network prefixes (`kbit/s`, `Mbit/s`, `Gbit/s`) chosen from the largest magnitude in the graph, and RTT is shown in `ms`.

Each series is one collector's evidence for one metric, and series are never merged or summed across collectors (MPOS-008). Labels read like `Inbound (east)`.

Traffic graphs carry `references`. One is `capacity`: the RATE-008 denominator with its source, for example `Capacity 1 Gbit/s (ifHighSpeed)`. The other is `contracted`: the annotation's contracted rate. Utilization graphs exist only when the interface has a utilization denominator under E.17 #15: a physical port, a port-channel, an interface of unknown type, or any interface with `capacity_override_bps`. A tunnel or other logical interface still has a traffic graph, but its utilization graph has `available=False` and the reason. Utilization uses the current capacity for the whole window, because capacity history is not kept.

## Timezone

All times in views are UTC, and `to_jsonable` writes them as ISO 8601 with `Z` (TIME-001, TIME-004). `window.timezone` names the zone to draw in (TIME-002, TIME-003). That is `time.timezone` when it is set to a known IANA zone; otherwise it is the host's zone, from `TZ` or the `/etc/localtime` link; otherwise `UTC`. `window.site_timezone` is the subject's site zone when that site has one.

## Latency "smoke" and state timelines

`LatencyView.positions` holds one `PositionLatency` per collector, in position order: `median`, `loss`, and `jitter` series plus the smoke. What the smoke contains depends on the data that is stored:

- **`smoke_mode="probes"`.** Every probe's RTT of every poll, in send order, with `null` for a lost probe, plus the poll's median and loss. Native observations keep every probe (ICMP-003). This mode applies when the window holds at most 1,200 polls (about 20 hours at 60 s), which covers the 1h and 6h presets.
- **`smoke_mode="median_envelope"`.** For longer windows each bucket carries the mean of the per-poll median RTTs, with the lowest and highest per-poll median as `low` and `high`. Beyond the native horizon, 15-minute rollups keep the minimum, mean, 95th percentile, and maximum of the per-poll median RTT, and the summed probe counts (RET-006). Individual probes are not kept there. The 95th percentile is not shown, because the p95 values of different buckets cannot be combined.
- **Loss in buckets.** Rollups compute loss from summed probe counts (RET-008). Native buckets average the per-poll loss percentages, which equals the counted loss when every poll sent the same number of probes (5 by default).

`TimelineView.segments` shows an interface's `admin`/`oper` status as segments from one position's native samples. A step longer than the gap threshold opens a segment without samples, with `admin` and `oper` set to `null`. Rollups keep no status, so a timeline covers at most the last 7 days of the request and at most 10,080 samples.

## Rankings (E.17 #13, #15)

`RankingQueries` makes one `HistoryQuery.window_summaries` scan of one collector per window. That single scan gives, for every interface, its sample counts, the nearest-rank p95 of inbound and outbound rates (`vakr.logic.stats.p95`), its valid error-delta sums, and its packet sums.

- **Utilization.** Each direction's p95 is divided by the interface's capacity, and the higher one is the score; a tie reports `in`. Interfaces without a denominator (E.17 #15), without samples from that position, or without a valid baseline are listed in `excluded` with the reason.
- **Errors.** Interfaces rank by input plus output error deltas. `per_million` is present only when every counted sample had packet deltas (`(in_pps + out_pps) × elapsed`) and packets moved. Interfaces with zero errors still rank, at the bottom. Logical interfaces rank here too, because errors need no denominator.
- **Coverage.** Every row's `coverage` says how many samples it rests on against the number the window should hold (DASH-005).

Ties break in natural device-then-interface order and then by UUID.

On SQLite each day partition is scanned as primary-key ranges, one per interface, and the values come back as exact 17-digit text, which Python merges and ranks. A window function sorted the same rows about four times slower, and SQLite's `percentile_disc` is not nearest-rank.

## Lists and search

`CatalogQueries` works over a bounded in-memory index of the inventory: at most 5,000 devices and 20,000 interfaces (E.29). It rereads the index only when `MetadataUnitOfWork.change_counter()` moves. Collectors are reread every time, because the counter does not cover them.

- **Interface list.** The `priority` order puts sticky interfaces first, then WAN circuits, then primary interfaces (the high-priority annotation, META-011), then `display_rank`, then natural device and interface names (UI-017, DASH-001). `name`, `traffic`, and `utilization` sort the other ways; the measured sorts put interfaces without a value last. Each row's `current` is the same `InterfaceRow` the device screen shows, from one position.
- **WAN circuits.** `wan_circuits` lists WAN interfaces with provider, circuit ID, contracted rate, site, and current values. Group the rows by `ref.provider` or `ref.site` for the provider and site views (DASH-002, UI-008).
- **Search.** `search` matches a case-insensitive plain substring, never a pattern (SEC-019). It matches devices by name, poll address, or UUID; interfaces by `ifName`, `ifAlias`, `ifDescr`, or UUID; sites by name or code; and providers, circuit IDs, tags, and groups. It returns up to 50 hits per kind, plus totals. Interface and circuit hits carry `interface_id` and `device_id`, so a client opens the full history straight from the result (UI-017). The API filters results by permission (12.1).

## Overview (12.2)

`OverviewView` holds:

- `devices`: counts per bucket, per E.2 status label, and fresh/stale/never-observed counts;
- `health`: the same collector, spool, and database health `vakr status` shows;
- `high_priority` and `wan`: up to 25 rows each;
- `top_utilization` and `top_errors`: the 15-minute window by default;
- `events`: four feeds;
- `filters`: providers, sites, tags, groups, and the WAN circuit count.

The event feeds are `restart` (management-agent restarts detected within `status.recent_restart_window`, per position), `rediscovery`, `identity_review` (open reviews, newest first), and `telemetry_loss` (spool incidents of the last 24 hours, from the injected reader). The `rediscovery` feed is always `available=False`, because no store records discovery runs yet. A feed without a source says so in `reason` and is never just empty (UI-014).

## Export (API-012)

`export_view(view, ExportFormat.CSV)` writes one row per point of every series. The columns are `time_utc, metric, position, collector_id, value, low, high, unit, source, bucket_seconds, timezone, flags`; a missing value is an empty cell, and flags are joined with `|`. `ExportFormat.JSON` encodes the view with `to_jsonable`. An export over 20,000 rows or 4 MiB raises `ValidationError`. Asynchronous bulk export is deferred past the MVP (27.4).

## Performance (12.9, MVP-ACC-05)

Measured with `uv run python -m tests.performance.sqlite_telemetry.bench_history` on the development host. The store held one full MVP day (100 devices, 1,600 interfaces, two collectors: 5.47 million rows), plus 400 days of 15-minute rollups for one interface. "Cold" is the first call in the process and "warm" is the median of five repeats.

| Call | Cold (ms) | Warm median (ms) |
|---|---:|---:|
| Traffic graph, 1h (one point per observation) | 4 | 2 |
| Traffic graph, 24h (120 s buckets) | 17 | 17 |
| Traffic graph, 7d (one day of data, 600 s buckets) | 21 | 21 |
| Traffic graph, 400d (rollups, 12 h buckets) | 628 | 582 |
| Latency, 1h, every probe | 10 | 9 |
| Latency, 24h, median envelope | 52 | 50 |
| Top utilization, 15m (new scan each call) | 133 | 118 |
| Top utilization, 1h | 247 | 317 |
| Top utilization, 6h | 1,188 | 1,056 |
| Top utilization, 24h | 4,155 | 4,397 |
| Top errors, 24h, after top utilization (scan reused) | 4,959 first pair | 53 |
| Interface list, priority order, first page | 69 | 48 |
| Interface list, by traffic (reads every device's current state) | 494 | 456 |
| Search | 9 | 9 |

The default suite runs a smaller check: `tests/performance/sqlite_telemetry/test_history_budget.py` asserts that the 400-day graph and the default 15-minute rankings over 1,600 interfaces each answer in under 2 s.

The 24-hour ranking is exact under E.17 #13: the nearest-rank p95 of every one-minute sample. Computing it means reading about 2.3 million samples per collector, which takes longer than the 2 s dashboard budget on first computation. Section 12.9's budget applies to the default windows, and the default ranking window is 15 minutes (DASH-003). Repeated requests within the 5-minute alignment step reuse the scan. E.28 records this choice and the follow-up, a precomputed ranking projection.
