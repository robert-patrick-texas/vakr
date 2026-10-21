# Rollups and native-data retention

A collector keeps every one-minute observation for 30 days. For longer graphs (90 days, 400 days) it keeps **rollups**: one summary per 15 minutes for every interface and every device, built from those observations. This page explains what a rollup holds, when native data may be removed, how the rollup job catches up after an upgrade, and what the `rollups` health check means.

The short version:

- Every hour the collector rolls up each complete quarter-hour, then runs the retention pass.
- **Native data is removed only after a rollup covers it.** If rollups fall behind, native data stays (the disk grows) and nothing is lost.
- After the first upgrade to a release with rollups, the job works through the backlog in bounded steps of at most two minutes each hour. A few weeks of data takes a few hours.
- `vakr check health` warns when rollups are more than 3 hours behind. It reports CRITICAL when native data older than its retention is still waiting for a rollup.

## What a rollup holds

One rollup row covers one interface (or device), one collector, and one UTC-aligned quarter-hour: 00:00–00:15, 00:15–00:30, and so on. It holds every metric of that subject.

| Metric | Smallest, central, p95, largest |
|---|---|
| Interface traffic, packets, and errors, in and out | Minimum, mean, nearest-rank 95th percentile, and maximum of the one-minute rates. A 30-second spike stays visible as the maximum after a year. |
| ICMP latency | Minimum, **median**, 95th percentile, and maximum of every individual reply in the quarter-hour, not of each cycle's summary. |
| ICMP loss | Minimum, 95th percentile, and maximum of each cycle's loss, and the quarter-hour's loss from the **summed probe counts**: 5 of 5 lost and 0 of 2 lost is 71.4%, never the 50% an average of percentages would give. Values are kept to one decimal place. |
| ICMP jitter | Minimum, mean, 95th percentile, and maximum of each cycle's jitter. |

Each row also keeps its **coverage**: how many samples the device's schedule intended in the quarter-hour (15 at the default 60-second interval), how many arrived, and how many had a value. A quarter-hour with 10 of 15 samples shows as thin, not as a full one. A quarter-hour with no sample at all has no row, and graphs show it as a gap. Missing data is never shown as zero.

The quality flags of every member sample are kept as well. Two cases are worth knowing:

- An ICMP rollup is marked "agent restarted; counters reset" when SNMP saw the device restart in that quarter-hour, so restarts stay visible after the native data has gone.
- ICMP cycles from a degraded collector count as samples, but they give no loss value: collector trouble never shows as packet loss on a target.

Interface values are stored at float32 precision (about 7 significant digits); ICMP values exactly. Rollups do not keep probe totals, SNMP success and failure counts, or the exact time of an outage or restart inside the quarter-hour; [ADR-021 amendment 6](../adr/ADR-021-compact-sqlite-telemetry.md#amendment-6-what-the-rollup-rows-cannot-hold-yet-2026-10-20-m4-rollup-job) explains why and what a later schema would add.

## When native data expires

The hourly retention pass removes native data older than `telemetry.native_retention_days` (30), **but never past the rollup frontier**: the end of the newest quarter-hour the rollup job has finished for every subject. Until rollups cover a quarter-hour, its native data stays, whatever its age. Rollups older than `telemetry.rollup_retention_days` (400) are removed the same way.

A quarter-hour is complete, and so ready for its rollup, once it ended more than 10 minutes ago. While the spool still holds observations that could not be stored yet (for example after a database outage), the job also waits until 10 minutes before the oldest of them, so a quarter-hour is never summarised before its data has arrived.

## After an upgrade: the backlog

Collectors that ran a release without rollups (0.2.x) have native data but no rollups. The first pass after the upgrade starts at the oldest native data and works forward. Each hourly pass does at most 7 days of quarter-hours and starts no new quarter-hour after 2 minutes, so polling is never starved. At the MVP workload a quarter-hour takes about 0.6 seconds, so each pass rolls up about two days. Native data for the whole backlog stays until it is covered, so graphs keep working throughout.

A pass that is stopped part-way, by its bounds, a restart, or a database error, loses nothing. Each quarter-hour is written for every subject in one transaction, and the next pass resumes at the rollup frontier. Rewriting a quarter-hour replaces it, so nothing is counted twice. Stretches with no native data at all, such as a collector outage, are skipped.

The collector logs each pass:

| Event | Meaning |
|---|---|
| `telemetry.rollups` | How many quarter-hours a pass rolled up, the new frontier, and how many subject rows held fewer samples than scheduled (incomplete buckets, RET-009). |
| `telemetry.rollups_behind` | The pass stopped at its bounds; how many hours of complete data remain. The next pass continues. |
| `telemetry.retention_failed` | The rollup or retention pass failed. It runs again an hour later, and no data is lost. |

## The `rollups` health check

`vakr check health` and `vakr doctor` include a `rollups` check. It reads `telemetry.db` read-only, so it works even when the collector is stopped.

| Result | When | What to do |
|---|---|---|
| OK | Rollups are at most 3 hours behind the newest complete native data, or there is nothing to roll up yet. | Nothing. Normally they are less than 1.5 hours behind. |
| WARNING | Rollups are more than 3 hours behind. | Expected for a few hours after the first upgrade, while the backlog drains. Otherwise check that `vakr-collector.service` runs, and look for `telemetry.retention_failed` in its journal. |
| CRITICAL | Native data older than `telemetry.native_retention_days` is still waiting for a rollup. Retention cannot expire it, so the disk grows past its budget. | Fix the collector as for WARNING. While the backlog drains, keep an eye on free space under `/var/lib/vakr`. |

Performance data:

- `rollup_lag_s` is the time from the rollup frontier to the end of the newest complete quarter-hour that holds native data.
- `rollup_waiting_age_s` is the age of the oldest native observation still waiting for a rollup, or 0 when nothing is waiting.

A stopped collector does not raise the lag. The check compares rollups with the native data that exists, not with the clock.

## See also

- [The databases](database.md): where the telemetry file lives and how it is laid out.
- [The collector service](collector.md): the hourly maintenance pass.
- [Appendix E.10](../../tool-blueprint.md#e10-rollups-and-retention) lists every default and rule on this page.
