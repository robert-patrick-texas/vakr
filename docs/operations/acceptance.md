# Acceptance reports

The MVP is accepted on evidence from the production node itself (blueprint 27.6). Three read-only commands compute that evidence from vakr's own telemetry and inventory:

| Command | Criterion | Default window | Passes when |
|---|---|---|---|
| `vakr acceptance schedule` | MVP-ACC-01 | 7 days | At least 99% of healthy-device polls start within ±5 s of schedule, no device is polled twice at once, the daily medians show no drift, and every healthy-device poll runs at 60 s |
| `vakr acceptance gaps` | MVP-ACC-10 | 14 days | Every period without data, per device, ICMP target, and interface, is explained by recorded evidence |
| `vakr acceptance coverage` | MVP-ACC-02 | 14 days (events) | Every monitored interface has traffic, packet, and error data at 1 h, 24 h, 7 d, 30 d, and 400 d, and no reload or counter discontinuity produced a spike |

Each report is computed per collector, so with [two positions](two-positions.md) each position is judged on its own evidence. Every definition, threshold, and bound below is in blueprint Appendix E.35; none is an option, because a criterion the judged system can tune is not a criterion.

```sh
sudo vakr acceptance schedule
sudo vakr acceptance gaps --window 14d
sudo vakr acceptance coverage --collector east --json
```

Run as root, the commands first become the `vakr` service user, like `vakr db` and `vakr status --local`. They open `/var/lib/vakr/metadata.db` and `telemetry.db` without creating either and only read; the collector is never blocked.

## Options and output

| Option | Meaning |
|---|---|
| `--window DURATION` | How far back to judge, ending five minutes ago so records still in the spool are not counted as gaps: `7d`, `36h`, from 1 hour to 30 days (native data is kept 30 days). For `coverage` it is the window searched for reloads and discontinuities; the graph ranges are fixed. |
| `--collector NAME` | Judge one position only. Without it, every collector in the inventory is judged. |
| `--limit N` | Rows listed per collector, 1 to 10,000 (default 1,000). Summaries always count every row. |
| `--json` | A JSON document (`"format": "vakr-acceptance-1"`) with the same figures as the text. |

The text output ends with one line to grep for: `RESULT MVP-ACC-01: PASS`, `FAIL`, or `INCOMPLETE`. Each check is printed with its measured value and threshold.

| Exit status | Meaning |
|---|---|
| 0 | Pass |
| 1 | Fail: at least one check failed |
| 2 | Usage error, such as a window or limit out of range, or an unknown collector |
| 3 | Incomplete (too little evidence to judge, or a read cut short by a bound), or the databases could not be read |

`vakr field collect --profile vakr` runs all three with `--json`, so the field bundle brings the evidence to the development host ([field data](field-data.md)).

## Schedule (MVP-ACC-01)

The scheduler records every slot it owns as a poll record (SCHED-012, SCHED-017), with its scheduled time, start, and completion. The report reads those records.

- **Start offset** is the start minus the scheduled time. A poll is **on time** when the offset is within ±5 s.
- **Healthy device.** A slot counts toward the on-time share unless its device was in trouble: the poll carries `MAINTENANCE`; or the poll, or the device's previous poll within two intervals, ended `PARTIAL` or `FAILED` without `COLLECTOR_DEGRADED`, or ran longer than its interval. A slot that never started (not attempted for capacity, or skipped because the previous poll overran) counts against the share: collector capacity is what the criterion tests, so collector-side misses are never excluded. A device that answers nothing to ICMP still completes its poll quickly, so it stays in the measure.
- **Overlap** is a poll that started before the same device's previous poll finished; a slot recorded twice also counts. Both must be zero.
- **Drift.** For each UTC day the report takes the median start offset and the median slot phase (how far each slot sits from the device's first slot in the window, modulo the interval) and fits a least-squares line through the daily medians. Each slope must stay within ±100 ms a day, over at least 7 UTC days that hold at least 60 healthy-device polls each. With fewer days, drift is `incomplete`, and so is the report. A wall-clock step of more than 1 s moves slot labels once (E.25 #3); the per-day rows show whether a slope is a step or a trend.
- **Workload.** Every healthy-device poll must run at the 60 s reference interval. The device and interface counts are shown beside the reference workload (100 devices, about 1,600 interfaces, blueprint 27.2) for comparison only.

Slots with no record at all are counted and left to `vakr acceptance gaps`, which explains them.

## Gaps (MVP-ACC-10)

The report rebuilds each device's slot timeline from its poll records, with the slots between records that have none, and lists every stretch of slots without data:

- **device** rows: slots whose poll produced no evidence at all. They imply that the device's ICMP target and interfaces had no data too, so those are not listed again.
- **icmp** rows: polls that ran but stored no ICMP result.
- **interface** rows: polls that ran but did not sample the interface. Interfaces of one device with the same stretch and cause share one row.

Each row carries one cause. Only `unexplained` fails the report; unexplained rows are listed first and marked `!`.

| Cause | Evidence |
|---|---|
| `collector_stopped` | No collector health report for more than 150 s (one is written every 60 s while the collector runs), a poll cancelled by shutdown, or a poll not attempted just before the reports stopped |
| `collector_busy` | The poll record says not attempted, with no sign of a full spool or a stop |
| `collector_fault` | A failed poll, or a missing ICMP result, flagged `COLLECTOR_DEGRADED`; or an SNMP result unknown for a collector-side reason |
| `storage` | Not attempted while a health report showed the spool at least 95% full; or, for the partner's collector, a silence that lasts to the end of the window (not replicated yet) |
| `telemetry_loss` | A telemetry-loss incident in the collector's spool log covers the slot ([spool](spool.md)) |
| `poll_overrun` | The previous poll was still running (`MISSED_OVERRUN`) |
| `device_unreachable` | The ICMP gate skipped SNMP, or the poll failed with no evidence and no collector fault |
| `snmp_timeout`, `snmp_error` | The poll's SNMP device result timed out, erred, answered in part, or Stage 2 did not finish |
| `admin_state` | The device is disabled or retired since before the slot, or the poll ran in maintenance |
| `not_yet_discovered` | The interface's identity was created after the slot, or the poll reported an unresolved identity |
| `interface_not_up` | The interface's lifecycle is not active, and has not been since before the slot |
| `inventory_change` | The device was created after the slot, or an audited change to it (or an `inventory.apply`) falls within two intervals of the stretch |
| `unexplained` | None of the above |

Collector or storage trouble is never reported as the device being down, and a slot without data is never a zero.

### What vakr does not record

Three things the report cannot see, which an operator may need to explain by hand:

- **An interface that went down and came back.** Interfaces that are not up/up get no samples and no stored status (E.27 #7), so a link that flapped while its device answered SNMP is `unexplained`. Telling it apart would need a status record for interfaces that are not sampled, which is a telemetry schema change.
- **Admin-state history.** The inventory keeps a device's current admin state and when it last changed. A device disabled and re-enabled inside the window explains its gap only through the audit entries at either end (`inventory_change`).
- **Why a poll was not attempted.** The record says not attempted, and `COLLECTOR_DEGRADED` for a capacity or storage refusal, but not which. The spool fill level in the health reports separates a full spool from a busy collector.

## Coverage (MVP-ACC-02)

A **monitored interface** is one whose lifecycle is active, on a device that is enabled or in maintenance and polls SNMP. For each one and each family (traffic, packets, errors), each range is one of:

| State | Meaning | Fails? |
|---|---|---|
| `data` | The graph at that range holds at least one value | No |
| `gap` | The interface has the family, just not in this range (for example a 1 h graph after a 90-minute outage); the graph shows the gap, and `gaps` explains it | No |
| `pending` | 400 d only: the interface's data is newer than the rollup frontier, or the interface is less than an hour old | No |
| `missing` | No value of the family anywhere: the graph is empty | Yes |

The 1 h to 30 d graphs draw native samples and the 400 d graph draws 15-minute rollups, as the web UI does (E.28). The report finds the newest value of each family from the last hour of samples and from the newest rollup buckets, which summarise native samples still held for 30 days.

**Reloads and discontinuities.** Within the event window, a device reload is an SNMP result that detected the agent restarting (found through the `BASELINE_UPTIME_RESET` flag its quarter-hour's ICMP rollup carries, RET-006). Every monitored interface of the device must then reset: its first sample after the reload carries a baseline-invalidating flag and no rate, so the graph shows a gap. A counter discontinuity is a sample flagged for an agent restart, a changed `ifCounterDiscontinuityTime`, a counter decrease, or a change of counter width; it must have no rate, and neither of the next two samples may exceed 1.10 times capacity (`RATE_EXCEEDS_CAPACITY`). Anything else is a **spike** and fails the report. Over-capacity samples that follow no event are counted separately: they usually point at a wrong capacity, not at a spike across a reload.

## Cost

The reports read lean marks through a dedicated port (`vakr.ports.telemetry.acceptance`), never whole observations, and read native samples only around stretches the rollups show as incomplete or flagged. Measured on the Compact SQLite adapter with a warm cache (100 devices, 800 interfaces, one collector, 3 days): `schedule` 4.8 s, `gaps` 4.1 s, `coverage` 1.8 s. Scaled to the MVP workload with two positions (1,600 interfaces, both collectors), that is about 25 s for a 7-day `schedule`, 60 s for a 14-day `gaps`, and 35 s for `coverage`, inside the field kit's 120 s per command. On a slower host, judge one position at a time with `--collector`.
