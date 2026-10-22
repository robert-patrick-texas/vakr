# Self-metrics

vakr exposes its own health and workload in the Prometheus text format, so a Prometheus server, Grafana Agent, or any compatible scraper can graph vakr itself: how long polls take and how late they start, how full the spool is, how far ingestion and rollups lag, and how busy the API is (OBS-001, OBS-004, OBS-005, OBS-007; Appendix E.33). Alerting stays with CheckMK or Nagios through [`vakr check health`](collector.md); the metrics are for trending and capacity planning.

- [Where the metrics are](#where-the-metrics-are)
- [Scraping on the node](#scraping-on-the-node)
- [Scraping through the reverse proxy](#scraping-through-the-reverse-proxy)
- [Missing is never zero](#missing-is-never-zero)
- [The metrics](#the-metrics)
- [Limits](#limits)

## Where the metrics are

`GET /api/v1/metrics` on `vakr-api` answers everything in one scrape: the API process itself, the inventory and the databases, and the collector. The collector has no listener of its own. Every 15 seconds it writes a snapshot of its figures to `/run/vakr/collector-metrics.json` (mode 0640, `vakr:vakr`), and the API reads it at each scrape. `vakr-api` must therefore be running to scrape anything; see [the API guide](api.md#starting-and-stopping).

The endpoint needs the **operator** role. It is never anonymous, so the reverse proxy in front of vakr exposes nothing new: without a credential the answer is 401 (OBS-T02). It is under `/api/`, so it carries `Cache-Control: no-store` and the usual security headers.

## Scraping on the node

On the node, the Unix socket authorizes by account: root and members of `vakr-view` or `vakr` may read it, with no password ([users](users.md#local-tui-and-cli-access)). Give a local agent's account `vakr-view`, which can read the socket and nothing else:

```console
$ sudo usermod -aG vakr-view telegraf
$ curl --silent --unix-socket /run/vakr/api.sock http://localhost/api/v1/metrics | head -3
# HELP vakr_build_info Always 1; the version label is the running release.
# TYPE vakr_build_info gauge
vakr_build_info{version="<release>"} 1
```

That suits a node_exporter text-file collector or any agent that reads a Unix socket. A Prometheus server on the node itself can scrape loopback HTTP with a kiosk token instead, as in the next section, with `scheme: http` and `targets: ['127.0.0.1:8080']`.

## Scraping through the reverse proxy

A Prometheus server elsewhere scrapes through the HTTPS reverse proxy ([web access](web-access.md)) with a **kiosk token** as its bearer credential. Kiosk tokens are read-only operator credentials ([kiosk displays](kiosk.md)); create one labelled for the scraper and store it in a file only Prometheus can read:

```console
$ sudo vakr kiosk create prometheus --lifetime 365d
$ sudo install -m 0400 -o prometheus /dev/stdin /etc/prometheus/vakr.token   # paste the token, then Ctrl-D
```

```yaml
scrape_configs:
  - job_name: vakr
    scheme: https
    metrics_path: /api/v1/metrics
    scrape_interval: 30s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/vakr.token
    static_configs:
      - targets: ['vakr.example.net']
```

TLS verification stays on: point `tls_config.ca_file` at your internal CA if the proxy's certificate is not publicly trusted. A kiosk token expires (at most 365 days); replace it before then as [the kiosk guide](kiosk.md#replacing-a-token-before-it-expires) describes, or the scrape starts answering 401. The token can read everything an operator can read, not only the metrics; a metrics-only credential is planned (E.33, recorded gap).

## Missing is never zero

A figure vakr could not measure is **absent** from the scrape, never written as 0 (invariant 1). Each part that can be missing has an `up` gauge that says why, and the answer is still 200:

| Gauge | 1 means | 0 with `reason` |
|---|---|---|
| `vakr_collector_metrics_up` | the collector's snapshot is fresh and valid; its families follow | `missing` (the collector is not running, or never wrote one), `stale` (older than 45 s: the collector hangs, or stopped without cleaning up), `too_large`, `unreadable`, `corrupt` |
| `vakr_state_metrics_up` | the inventory figures could be read | `metadata_unavailable` |

When the collector's snapshot is unusable, every `vakr_collector_*` series disappears at once rather than freezing at its last value. Alert on `vakr_collector_metrics_up == 0`, or let your `vakr check health` alerts cover the collector.

A `_total` counter runs from the start of its process (`vakr_process_start_time_seconds`), so a restart shows as a reset, which Prometheus's `rate()` handles. Summaries give the p50, p95, and p99 of recent samples (the last 4,096 polls, about 40 minutes at the MVP workload), plus a lifetime `_sum` and `_count`; a summary with no sample yet has no quantiles.

Collector overload is the collector's own state (`vakr_collector_state{state="overloaded"}`, `vakr_collector_polls_capacity_misses_total`), never a count of devices down (OBS-003). No metric names a device, an interface, a user, an address, or a path.

## The metrics

### API, inventory, and databases

| Metric | Type | Labels (fixed values) | Meaning |
|---|---|---|---|
| `vakr_build_info` | gauge | `version` | Always 1; the version label is the running release. |
| `vakr_process_start_time_seconds` | gauge | `process` (api, collector) | When the process started, Unix seconds. |
| `vakr_process_cpu_seconds_total` | counter | `process` (api, collector) | User and system CPU time since start. |
| `vakr_process_resident_memory_bytes` | gauge | `process` (api, collector) | Resident memory. |
| `vakr_process_open_fds` | gauge | `process` (api, collector) | Open file descriptors. |
| `vakr_event_loop_lag_seconds` | gauge | `process` (api, collector) | Largest event-loop lag of the current period (at most a minute). |
| `vakr_api_requests_total` | counter | `group` (auth, dashboards, exports, health, history, kiosk, login, metrics, operations, rankings, reads, web), `code` (200, 201, 202, 204, 304, 400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 500, 502, 503, 504, none, other) | Requests answered, by route group and status code. |
| `vakr_api_request_duration_seconds` | summary | `group` (auth, dashboards, exports, health, history, kiosk, login, metrics, operations, rankings, reads, web), `quantile` (0.5, 0.95, 0.99) | Request duration by route group: recent quantiles, lifetime sum and count. |
| `vakr_api_connections` | gauge | `listener` (socket, tcp) | Open connections on a listener. |
| `vakr_api_connection_limit` | gauge | `listener` (socket, tcp) | Connections plus requests a listener allows before answering 503 (E.30). |
| `vakr_collector_metrics_up` | gauge | `reason` (corrupt, missing, ok, stale, too_large, unreadable) | 1 when the collector's snapshot is fresh and valid, else 0 with the reason. |
| `vakr_collector_metrics_age_seconds` | gauge | none | Age of the collector's snapshot when it could be read. |
| `vakr_state_metrics_up` | gauge | `reason` (metadata_unavailable, ok) | 1 when the inventory and database figures could be read, else 0 with the reason. |
| `vakr_devices` | gauge | `admin_state` (disabled, enabled, maintenance, retired) | Devices in inventory by admin state. |
| `vakr_devices_by_status` | gauge | `bucket` (active, attention, offline) | Listed devices by status bucket (7.7): attention, active, offline. |
| `vakr_interfaces` | gauge | `lifecycle` (active, inactive, retired, stale) | Interfaces in inventory by lifecycle state. |
| `vakr_identity_reviews_open` | gauge | none | Interface identity reviews waiting for an admin. |
| `vakr_operations` | gauge | `state` (queued, running) | Admin operations not finished, by state (E.32). |
| `vakr_sessions` | gauge | none | Stored sign-in sessions, expired ones not yet purged included. |
| `vakr_kiosk_tokens` | gauge | none | Kiosk tokens, expired ones not yet purged included. |
| `vakr_users` | gauge | none | Local web and API accounts. |
| `vakr_replication_metadata_age_seconds` | gauge | none | Age of a peer's newest successful metadata pull (E.13); absent on a primary. |
| `vakr_replication_metadata_pulls_total` | counter | `result` | A peer's metadata pulls by result, since its pull record began. |
| `vakr_replication_observation_lag_seconds` | gauge | none | Age of the newest observation stored from the other position (E.13, MPOS-006); absent before the first. |
| `vakr_replication_backlog` | gauge | none | Observations the other position logged that this node has not stored yet. |
| `vakr_replication_gaps_total` | counter | none | Observations the other position no longer held when this node asked (MPOS-005). |
| `vakr_replication_observations_total` | counter | `result` | Observations pulled from the other position: `inserted`, or `duplicate` (already held, a replay). |
| `vakr_replication_observation_pulls_total` | counter | `result` | Observation pulls from the other position by result (`ok`, `failed`), since the record began. |
| `vakr_database_size_bytes` | gauge | `database` (metadata, telemetry), `file` (main, wal) | Size of a database file. |
| `vakr_database_schema_version` | gauge | `database` (metadata, telemetry) | Highest applied schema migration. |
| `vakr_rollup_lag_seconds` | gauge | none | How far the 15-minute rollups are behind the newest complete native data. |
| `vakr_rollup_waiting_age_seconds` | gauge | none | Age of the oldest native observation still waiting for a rollup (0 when none). |
| `vakr_rankings_unprojected_seconds` | gauge | none | Complete native data of the last 25 hours the ranking projection misses (E.28). |
| `vakr_metrics_truncated` | gauge | none | 1 when the series or size cap left families out (E.33). |

The process families (`vakr_process_*`, `vakr_event_loop_lag_seconds`) carry `process="api"` for `vakr-api` and `process="collector"` for the collector. Sign-in failures are `vakr_api_requests_total{group="login",code="401"}`, throttled sign-ins `code="429"`. Inventory and database figures are read at most every 15 seconds, so frequent scrapes cost no extra database work. The rollup and ranking figures are the same measurements the `rollups` and `rankings` checks of `vakr check health` report.

### Collector

| Metric | Type | Labels (fixed values) | Meaning |
|---|---|---|---|
| `vakr_collector_state` | gauge | `state` (degraded, healthy, overloaded, unavailable) | 1 for the collector's current state, 0 for the others; overload is never target-down. |
| `vakr_collector_devices_scheduled` | gauge | none | Devices the scheduler plans to poll. |
| `vakr_collector_interfaces_sampled` | gauge | none | Interfaces the collector samples. |
| `vakr_collector_workers` | gauge | none | Poll workers. |
| `vakr_collector_workers_busy` | gauge | none | Workers holding a poll or its unsent result. |
| `vakr_collector_slots_waiting` | gauge | none | Due poll slots waiting for a worker. |
| `vakr_collector_polls_scheduled_total` | counter | none | Poll occurrences that came due. |
| `vakr_collector_polls_started_total` | counter | none | Polls that started. |
| `vakr_collector_polls_completed_total` | counter | `execution` (failed, ok, partial) | Polls that finished, by how they ended. |
| `vakr_collector_polls_late_total` | counter | none | Polls that started later than scheduler.late_after after their slot. |
| `vakr_collector_polls_missed_total` | counter | `reason` (not_attempted, overrun) | Occurrences that never ran, by reason. |
| `vakr_collector_polls_capacity_misses_total` | counter | none | Occurrences not attempted because the collector lacked capacity (OBS-003). |
| `vakr_collector_polls_cut_short_total` | counter | `cause` (cancelled, deadline) | Polls ended early by shutdown or the poll deadline. |
| `vakr_collector_step_errors_total` | counter | none | Collector-side step failures. |
| `vakr_collector_faults_total` | counter | `reason` (icmp_not_sent, icmp_resolution, other, snmp_session, snmp_transport) | Collector-side trouble by reason: evidence about vakr, never about a target. |
| `vakr_collector_poll_latency_seconds` | summary | `latency` (completion, duration, lateness), `quantile` (0.5, 0.95, 0.99) | Poll lateness (start minus slot), duration, and completion (end minus slot). |
| `vakr_collector_steps_total` | counter | `step` (icmp, snmp_fallback_stage1, snmp_stage1, snmp_stage2), `status` (cancelled, deadline, error, no_response, ok, partial, skipped) | Poll steps by step and how they ended. |
| `vakr_collector_step_duration_seconds` | summary | `step` (icmp, snmp_fallback_stage1, snmp_stage1, snmp_stage2), `quantile` (0.5, 0.95, 0.99) | Duration of the steps that ran, by step (ICMP, SNMP Stage 1, fallback, Stage 2). |
| `vakr_collector_icmp_probes_sent_total` | counter | none | ICMP echo probes sent. |
| `vakr_collector_icmp_probes_answered_total` | counter | none | ICMP echo probes answered. |
| `vakr_collector_queue_depth` | gauge | `queue` (poll_output, spool_write) | Items waiting in a collector queue. |
| `vakr_collector_queue_capacity` | gauge | `queue` (poll_output, spool_write) | Bound of a collector queue. |
| `vakr_collector_queue_full_waits_total` | counter | none | Times a poll producer found the output queue full. |
| `vakr_collector_producers_blocked` | gauge | none | Producers waiting on a full queue now. |
| `vakr_collector_spool_bytes` | gauge | none | Bytes held in the durable spool. |
| `vakr_collector_spool_limit_bytes` | gauge | none | The spool's size limit. |
| `vakr_collector_spool_state` | gauge | `state` (critical, exhausted, normal, warning) | 1 for the spool's fill state, 0 for the others (6.4). |
| `vakr_collector_spool_backlog_records` | gauge | none | Durable records not yet in telemetry. |
| `vakr_collector_spool_oldest_age_seconds` | gauge | none | Age of the oldest record not yet in telemetry: the replay lag. |
| `vakr_collector_spool_incidents_total` | counter | none | Telemetry-loss incidents recorded. |
| `vakr_collector_ingest_batches_total` | counter | none | Write batches stored in telemetry. |
| `vakr_collector_ingest_observations_total` | counter | `result` (duplicate, inserted, set_aside) | Observations ingested: stored, already there (replays), or set aside. |
| `vakr_collector_ingest_latency_seconds` | summary | `quantile` (0.5, 0.95, 0.99) | Append-to-stored time of each acknowledged batch's newest record. |
| `vakr_collector_ingest_storage_available` | gauge | none | 1 when the last telemetry write succeeded, 0 when it failed. |
| `vakr_collector_ingest_consecutive_failures` | gauge | none | Failed writes since the last success. |
| `vakr_collector_clock_steps_total` | counter | none | Wall-clock steps the scheduler detected. |
| `vakr_collector_wall_offset_seconds` | gauge | none | Wall clock minus the plan timeline. |
| `vakr_collector_upkeep_passes_total` | counter | `kind` (move, projection, retention) | Telemetry upkeep passes completed. |
| `vakr_collector_upkeep_failures_total` | counter | `kind` (move, projection, retention) | Telemetry upkeep passes that failed. |
| `vakr_collector_upkeep_last_success_timestamp_seconds` | gauge | `kind` (move, projection, retention) | When an upkeep pass last succeeded, Unix seconds. |
| `vakr_collector_upkeep_last_duration_seconds` | gauge | `kind` (move, projection, retention) | How long the last successful upkeep pass took. |
| `vakr_collector_interval_seconds` | gauge | `interval` (configured, effective_max, effective_min) | Configured default poll interval, and the shortest and longest effective ones. |

## Limits

All in Appendix E.33.

| Bound | Value |
|---|---|
| Collector snapshot interval / stale after | 15 s / 45 s |
| Collector snapshot size | 1 MiB, at most 2,000 samples |
| Inventory and database figures cached | 15 s |
| Collector snapshot cached by the API | 2 s |
| Series per scrape / answer size | 2,000 / 512 KiB; families beyond are left out and `vakr_metrics_truncated` is 1 |
| Summary window | 4,096 samples per poll latency, 2,048 per step, 1,024 per API route group |
| Label values | fixed sets only; no device, interface, user, address, path, or token |
