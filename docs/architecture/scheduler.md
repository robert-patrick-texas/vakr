# Scheduler: how polls are planned and what each outcome means

This page explains when vakr polls each device, what one poll does, and how to read the record every scheduled poll leaves behind, including the polls that did not run. It is written for operators reading poll data and for developers wiring collectors into the scheduler. The code is in `src/vakr/scheduler/`; the defaults are in blueprint Appendix E.25 and the [configuration reference](../operations/configuration.md).

Three promises run through the scheduler (blueprint 4.3):

1. **Every scheduled poll leaves a record.** A poll that ran, a poll cut short, and a poll that never started all produce one `DevicePoll`. A gap in the poll records means the collector was not running, never that vakr quietly skipped a device.
2. **Collector trouble is never device trouble.** A poll the collector was too busy to run is *not attempted*, which shows as Unknown. It is never Down.
3. **The schedule is anchored.** Each poll's time comes from the plan, not from when the previous poll finished, so a slow minute does not push every later poll back (invariant 10).

## When a device is polled

### Collector name, phase, and order

Each collector has a name, by default the host's short name in lower case: `vakr-east.example.net` becomes `vakr-east` (SCHED-001). Two independent settings place every device's poll inside its interval (SCHED-002):

| Setting | Values | Effect |
|---|---|---|
| `scheduler.phase` | `hostname` (default), `fixed` | Shifts this collector's whole cycle. `hostname` derives the shift from the collector name, so two collectors watching the same routers do not probe them at the same instant. `fixed` uses `scheduler.fixed_phase`. |
| `scheduler.device_order` | `common` (default), `hashed` | Places each device after the phase. `common` uses the device's UUID alone, so every collector visits devices in the same relative order. `hashed` also mixes in the collector name, giving each collector its own order. |

The arithmetic is fixed by Appendix E.11 so that a future Go collector schedules identically: a hash is the first 8 bytes of SHA-256 over the UTF-8 text, read as a big-endian number. With `I` the interval in microseconds:

```text
phase    = hash(collector_name) mod I            (hostname)   or   fixed_phase mod I   (fixed)
position = hash(device_uuid) mod I               (common)     or   hash(collector_name + "/" + device_uuid) mod I   (hashed)
offset   = (phase + position) mod I
slot k   = k * I + offset   microseconds after 1970-01-01T00:00:00Z
```

So a device is polled at the same offset past every interval boundary: "router 17 is polled at 37.4 seconds past each minute". The offset depends only on the collector, the device, and its interval, so adding or removing other devices never moves it. Hashing spreads 100 devices across the minute instead of bursting at :00, but it does not promise that two devices never start together (SCHED-007). Test vectors for other implementations are in `tests/unit/scheduler/golden_phase_vectors.json`.

### Intervals

A device's interval is its own override, otherwise `polling.default_interval` (60 s). Values outside 30 s to 1 h are clamped; the record keeps both the configured and the effective interval, so the clamp is visible. Devices with 60, 120, and 300 second intervals share one due-time queue and one worker pool (SCHED-013). Disabled and retired devices are not scheduled, and their history is kept. Devices in maintenance are polled, and their records carry the `MAINTENANCE` flag.

### Reload

When inventory or policy changes, the plan is rebuilt without disturbing devices that did not change: they keep their offset and their next slot. A new device, or one whose interval changed, takes the first slot of its own grid that is not earlier than its next poll would have been. Because offsets are spread by the hash, importing a hundred devices at once does not make them all poll at the same moment.

### Clocks

Waiting, deadlines, lateness, and durations use the monotonic clock, which never jumps. Stored times are UTC (TIME-001). If the wall clock is stepped by more than 1 s (an NTP correction or `date -s`), the cadence is unchanged: no poll is repeated, skipped, or bunched up. Only the labels move. `scheduled_at` values after the step follow the corrected clock, so the step shows once as an unusual spacing between two consecutive `scheduled_at` values (SCHED-018). `completed_at` is always `started_at` plus the measured duration, so it never comes before `started_at`.

## When a slot comes due

```text
slot due ──► device still being polled? ── yes ──► MISSED_OVERRUN
                    │ no
                    ▼
             reached more than max_start_delay late? ── yes ──► NOT_ATTEMPTED (late dispatch)
                    │ no
                    ▼
             wait for a free worker (at most one waiting slot per device)
                    ├── next slot of the same device comes due ──► NOT_ATTEMPTED (superseded)
                    ├── waited longer than max_start_delay ───────► NOT_ATTEMPTED (no worker)
                    └── worker free ─────────────────────────────► poll runs
```

At most `scheduler.workers` polls run at once (32 by default; the MVP needs about 5 on average), and never two polls of the same device (SCHED-012, SCHED-016). A worker stays busy until its record is accepted by the queue to the spool, so if storage falls behind, polling slows instead of memory growing, and the slowdown is recorded as not-attempted polls and reported as an overloaded collector (section 6.4).

## What one poll does

The poll walks a state machine whose arrows depend on the device's reachability strategy (section 9.2, ADR-005, ADR-017):

| Strategy | ICMP replies | Then |
|---|---|---|
| `icmp_gated` (default) | 2 to 5 | SNMP Stage 1, then Stage 2 if Stage 1 found interfaces up/up |
| `icmp_gated` | 0 or 1 | SNMP is skipped; the ICMP result is the whole poll |
| `icmp_gated` | unknown: vakr could not ping (collector trouble) | SNMP runs as for 2 to 5; the poll is flagged `COLLECTOR_DEGRADED` and ICMP reads as unknown |
| `snmp_fallback` | unknown: vakr could not ping | as for 0 or 1 |
| `snmp_fallback` | 2 to 5 | as `icmp_gated` |
| `snmp_fallback` | 0 or 1 | one low-cost Stage 1 request without retries; no Stage 2 |
| `icmp_only` | any | ICMP only |
| `snmp_only` | not probed | Stage 1, then Stage 2 if Stage 1 found interfaces up/up |

Stage 1 reads `sysUpTime` and exact interface status. Stage 2 reads counters, but only for the interfaces this poll's Stage 1 found admin-up and oper-up, never from a remembered state (SCHED-009). A successful fallback request proves the management plane answers; it never turns lost pings into successful ones. A step that fails does not discard the evidence from steps that already finished.

A poll that runs longer than `scheduler.poll_deadline` (90 s) is stopped. Steps that already finished keep their results.

## Reading poll records

| `execution` | Flags | What it means | What to do |
|---|---|---|---|
| `complete` | | Every step the strategy called for answered fully. A device with 0 of 5 ping replies under `icmp_gated` is a complete poll of an unreachable device. | Read the ICMP and SNMP results. |
| `partial` | | Some evidence arrived, then a later step did not finish cleanly: an SNMP timeout after good pings, a partial counter walk, or the poll deadline. | The device answered something; check the SNMP results and the device's agent. |
| `failed` | | No step produced evidence, for example an `snmp_only` device whose agent did not answer. | Check the device, its address, and its SNMP credential. |
| `partial` or `failed` | `COLLECTOR_DEGRADED` | A step failed inside vakr (a socket error, a bug). | Look at collector health, not at the device. |
| `cancelled` | | Shutdown (a restart or an upgrade) cut the poll short. The steps that finished keep their evidence; the device reads as unknown until the next poll. | Nothing; expected during restarts and upgrades. |
| `missed_overrun` | `NOT_ATTEMPTED` | The previous poll of this device was still running at this slot, so vakr did not start a second session (SCHED-012). | Repeated overruns mean the device is slow to answer; check its agent. |
| `not_attempted` | `NOT_ATTEMPTED`, `COLLECTOR_DEGRADED` | The collector had no capacity: no worker within `max_start_delay`, the slot was superseded by the next one, or the scheduler itself was stalled. | Collector overload: look at collector health, workers, and the spool. Never a device fault. |
| `not_attempted` | `NOT_ATTEMPTED` | The collector was shutting down before the poll could start. | Nothing; expected during restarts and upgrades. |
| any | `MAINTENANCE` | The device was in maintenance when polled. | |

`NOT_ATTEMPTED` and `COLLECTOR_DEGRADED` put a device in the amber Unknown row of the status table, never red Down (Appendix E.2 row 3; see [quality states](quality-states.md)). Missed and not-attempted records carry no measurements at all, so they can never be mistaken for a zero.

## Collector health from the scheduler

The scheduler exposes a snapshot for health views and the `CollectorHealthObservation` record (OBS-001 to OBS-004, OBS-007):

- polls completed, late (started more than `scheduler.late_after`, 5 s, after their slot), and missed in the period;
- p50, p95, and p99 of start lateness, poll duration, and slot-to-completion time over the last 4,096 polls;
- output queue depth, capacity, how often producers found it full, and how long they waited;
- the worst event-loop lag in the period, measured by a 1 s probe;
- detected wall-clock steps and the current wall-clock offset.

The collector is reported **overloaded** when the period had capacity misses or the queue to the spool is full, **degraded** when event-loop lag exceeded 1 s, a step failed inside vakr, or the scheduler is shutting down, and **healthy** otherwise. These describe the collector, not any device, and never change a device's own interval (OBS-003, ADAPT-006).

## Shutdown

On stop (section 16.4), the scheduler stops starting polls, gives running polls `scheduler.shutdown_grace` (15 s) to finish, cancels the rest, and records each cancelled poll with whatever its finished steps produced. Slots that were waiting, and slots that came due during the drain, are recorded as not attempted. The service then flushes the queue to the spool and exits within systemd's stop timeout.

## For developers

- `Scheduler(clock, settings, ids, steps, sink)` with `replan(targets)`, `run()`, `stop()`, and `snapshot()` is the whole interface. Build targets with `targets_from_devices` and settings with `settings_from_config`. Call `replan` just before `run`.
- The ICMP and SNMP collectors implement the `IcmpStep` and `SnmpStep` protocols in `vakr.scheduler.steps`. A step returns what the target did and raises only for collector-side failures.
- `scheduler_problems(config)` reports the cross-key rule (`late_after` must not exceed `max_start_delay`) for `vakr config validate`.
- Tests run the real scheduler on a virtual-time event loop (`tests/unit/scheduler/sched_simloop.py`): a simulated day of 100 devices takes about 20 seconds and shows healthy completion p95 under 15 s and p99 under 30 s (TEST-004).

Not in the MVP: worker affinity across several worker processes (SCHED-014), cost-weighted ordering (SCHED-015), and adaptive interval changes (ADAPT-002 to ADAPT-007).
