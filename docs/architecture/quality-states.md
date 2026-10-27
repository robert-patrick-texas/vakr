# Quality states, flags, and rules

This page explains every state vakr can show for a device or interface, every quality flag attached to an observation, and the rules that produce them. It is written for operators: what the screen says, what vakr actually knows, and what it deliberately refuses to claim. The rules live in `src/vakr/logic/` as pure functions with table-driven tests; the defaults come from blueprint Appendix E.

Four principles run through all of it (blueprint 4.3):

1. **Missing data is never zero.** An unanswered probe, a missing counter, or an unusable baseline is stored and shown as *unavailable*, never as 0 ms, 0 b/s, or 0 errors.
2. **Collector trouble is never target trouble.** If the vakr collector is overloaded, degraded, or down, the devices it watches show *Unknown*, not *Down*.
3. **Resets make gaps, not spikes.** A router reload, an interface counter reset, or a switch between 32-bit and 64-bit counters leaves a gap in the graph. vakr never subtracts across it.
4. **Partial results are kept and flagged.** Half an SNMP reply is still evidence. It is stored, used for what it covers, and marked.

## Reachability from one probe group

Each cycle sends five ICMP Echo probes about 200 ms apart (ICMP-001, ICMP-002). Every probe's round-trip time is kept in integer microseconds. A probe with no reply is stored as `-1`, shown as "no reply", and never enters any latency statistic (ICMP-003 to ICMP-005).

| Replies | State | Flag | Why |
|---|---|---|---|
| 5 of 5 | Reachable | none | |
| 4 of 5 | Reachable | `PARTIAL_LOSS` | Cisco IOS XE Control Plane Policing rate-limits ICMP to the route processor, so one lost probe is routine and should not turn a device amber. |
| 3 or 2 of 5 | Impaired | none | Loss at or above `icmp.impaired_loss_percent` (40%). |
| 1 of 5 | Impaired | `SINGLE_REPLY` | One reply proves the device answers, but it is too little to call the path healthy, and SNMP is skipped under `icmp_gated`. |
| 0 of 5 | Unreachable | none | Reserved for zero replies. |
| 0 of 5, collector unhealthy | Unknown | `COLLECTOR_DEGRADED` | An overloaded collector can drop replies itself, so silence proves nothing. |
| not sent | Unknown | `NOT_ATTEMPTED` | The scheduler or capacity limits prevented the poll. |

If `icmp.impaired_rtt_ms` is set, a median RTT strictly above it also makes the group Impaired. The threshold is compared exactly, so 1.005 ms means 1005 µs. When the collector is unhealthy but replies did arrive, the state follows the table, because replies cannot be invented, and `COLLECTOR_DEGRADED` marks the loss as suspect.

**Summary values** (ICMP-008, Appendix E.7):

- *min* and *max* are the smallest and largest RTT.
- *average* is the arithmetic mean.
- *median* is the middle value, or the mean of the two middle values.
- *jitter* is the population standard deviation of the successful RTTs. It is unavailable with fewer than two replies (ICMP-010).
- *loss* is `100 × lost / sent`.

All summaries are computed exactly and rounded once, half to even, to whole microseconds. Loss is rounded to one decimal place the same way. So 6.25% is stored as 6.2% and 18.75% as 18.8%. Exact arithmetic means a Python collector, a future Go collector, and the rollups agree to the microsecond. With no replies, every latency summary is unavailable.

### When SNMP runs

| Strategy | 0 replies | 1 reply | 2 or more replies |
|---|---|---|---|
| `icmp_gated` (default) | skipped | skipped | collected |
| `snmp_fallback` | one low-cost Stage 1 request | one low-cost Stage 1 request | collected |
| `icmp_only` | never | never | never |
| `snmp_only` | always | always | always |

A successful SNMP reply after lost ICMP proves the management plane answered, but it never rewrites the ICMP result: the probe group stays lost and the device shows "ICMP lost, SNMP OK" (ICMP-013).

## Device status

One collector's view of a device is decided by the table below. Rows are checked in order and the first match wins, so operator intent beats evidence, missing evidence beats a verdict, and a confirmed outage beats a degraded one (Appendix E.2).

| Row | Condition | Shown as | What it means |
|---|---|---|---|
| 1 | Admin state disabled or retired | gray, Offline: "Disabled" / "Retired" | Not polled by default. |
| 2 | Admin state maintenance | gray, Offline: "Maintenance (collection continues)" | Still polled; results carry `MAINTENANCE`. |
| 3 | No fresh observation, last poll not attempted, collector degraded, overloaded, or unavailable, or no reachability result at all | amber, Attention: "Unknown — *reason*" | vakr cannot currently see this device. That is not the same as the device being down. |
| 4 | ICMP unreachable and SNMP did not answer | red, Attention: "Down" | No reply of any kind from a healthy collector. |
| 5 | ICMP unreachable but SNMP answered | amber: "ICMP lost, SNMP OK" | ICMP is filtered or rate-limited; the device is alive. |
| 6 | ICMP impaired | amber: "Packet loss", "Latency", or both | |
| 7 | ICMP reachable but SNMP timed out or errored | amber: "SNMP failing" | Credentials, ACLs, or an overloaded agent. |
| 8 | Partial SNMP reply, incomplete discovery, or an interface identity awaiting review | amber: "Partial data" | |
| 9 | Management agent restarted within `status.recent_restart_window` (60 minutes) | amber: "Restarted" | See uptime below: this is not proof of a chassis reboot. |
| 10 | Otherwise | green, Active: "OK" | |

"SNMP answered" means a full or partial reply. Any successful SNMP response proves the device was reachable (SNMP-013), so a partial reply is never "Down".

The *reason* in row 3 is the most specific one available, in this order: collector state ("collector overloaded"), "poll not attempted", "no fresh observation", then "no ICMP result" or "no SNMP result".

**Strategies.** For `icmp_only` devices, SNMP is ignored: row 4 is simply "ICMP unreachable", and rows 5, 7, 9 and the SNMP part of row 8 never apply. For `snmp_only` devices, SNMP is the only reachability evidence: an SNMP timeout is "Down" (row 4), an SNMP error is "SNMP failing" (row 7) because the device did answer, and the ICMP rows 5 and 6 never apply.

### Two positions

With two vakr nodes, each position evaluates the table independently and both results stay visible side by side (MPOS-007). The composite status follows these rules:

- **Down (red)** only when every position with usable evidence says Down.
- **"Reachable from east only" (amber)** when some positions say Down and others still reach the device. This is often the first visible sign of a network partition between a position and part of the network.
- **Otherwise, the most severe** evidence-backed result.

A position whose own result is row 3 (Unknown) has no usable evidence. It neither confirms an outage nor turns a healthy device amber. Its Unknown is shown only when no position has evidence. Admin rows (1 and 2) come from shared metadata and are the same at every position. If replication lags, row 1 wins.

### Device list order

The device list sorts by bucket (Attention, then Active, then Offline), then by severity, then by most recent state change, then by name in natural order, so `ge-0/0/2` comes before `ge-0/0/10` (blueprint 7.7). Severity from most to least severe is: Down, "Reachable from … only", Unknown, then rows 5 to 9 in table order, then OK, then Maintenance and Disabled. Colour is never the only cue: every status has a text label.

## Freshness

An observation is current only while its age is at most `max(2.5 × interval, interval + 30 s)`. That is 150 s at the 60 s default, 40 s at 10 s, and 750 s at 300 s (Appendix E.3, TIME-006). One late or missed poll is tolerated; two are not. Stale data is flagged `STALE`, and the device shows "Unknown — no fresh observation" instead of its last known state. Ages are always shown next to current-state values (TIME-005). An observation timestamped in the future, which happens with clock skew, is shown with its negative age and is not called stale. Clock health is reported separately.

## Uptime and restarts

`sysUpTime.0` counts hundredths of a second since the device's **SNMP management agent** last started. It wraps to zero every 497 days. After a gap, vakr adds the elapsed wall time to the previous value and compares the result with the new value (Appendix E.5, UPTIME-002, UPTIME-004). The tolerance is the larger of 10 s and 1% of the gap.

| Result | When | What it means |
|---|---|---|
| Continuous | The new value matches within tolerance, including across the 497-day wrap. | The agent ran throughout. |
| Restart detected, high confidence | The new uptime is no longer than the gap. | The management agent restarted during the gap. |
| Restart detected, low confidence | The uptime went backwards but is longer than the gap. | Continuity is inconsistent, for example after a misattributed previous sample. |
| Unknown, `UPTIME_AHEAD` | The uptime is further ahead than the wall clock allows. | A device or collector clock problem. |
| Unknown, `CLOCK_UNSYNCED` | The collector's clock went backwards between polls. | |

**A restart of the management agent is not proof of a chassis reboot** (UPTIME-003, UPTIME-005). Agents restart on their own, and vakr reports exactly what the evidence shows. Every restart carries its evidence text and confidence.

## Counter baselines and rates

A rate is the difference between two cumulative counters divided by the actual time between them, never the configured interval (RATE-001, RATE-006). Bits per second is `Δoctets × 8 / elapsed`; packets and errors per second are `Δ / elapsed`. That subtraction is only valid when nothing reset the counters in between, so vakr checks every pair first (blueprint 9.7). If any check fails, the raw counters are still stored, every rate for that sample is unavailable, and the sample becomes the baseline for the next one (RATE-004). The graph shows a gap for one interval and then resumes. It never shows a spike.

| Flag | Shown as | Cause |
|---|---|---|
| `BASELINE_FIRST_SAMPLE` | gap | First sample for this interface from this collector. |
| `BASELINE_MISSING_PRIOR` | gap | The previous poll produced no sample, or no value for this counter. |
| `BASELINE_ELAPSED_INVALID` | gap | Two samples with the same or reversed timestamps. |
| `BASELINE_GAP` | gap | More time than the maximum gap passed between samples (see below). |
| `BASELINE_UPTIME_RESET` | gap | The management agent restarted, or uptime continuity was inconsistent or unknown. |
| `BASELINE_DISCONTINUITY` | gap | `ifCounterDiscontinuityTime` changed, appeared, or disappeared: the agent says its counters jumped. |
| `BASELINE_WIDTH_CHANGE` | gap | The counters switched between 64-bit (HC) and 32-bit. Rates resume on the next interval. |
| `BASELINE_DECREASE` | gap | A counter went down with no reset evidence and no plausible wrap. |
| `BASELINE_IDENTITY_UNRESOLVED` | gap | The interface's persistent identity is ambiguous or awaiting review, so the two samples may not be the same port. |

Every applicable reason is recorded, not just the first. A decrease is judged only when the other checks pass, so a reboot is reported as an uptime reset rather than an "unexplained" decrease.

**Maximum gap** (Appendix E.4): 5 × the effective interval for 64-bit counters. For 32-bit counters it is the smaller of that and half the counter's wrap time at the interface's line rate, because a second wrap inside the gap would be invisible. A 32-bit octet counter on a 1 Gb/s port wraps in 34 s, so 60-second polling of 32-bit counters on gigabit ports always produces gaps. vakr prefers HC counters wherever the device offers them (SNMP-007).

**32-bit wraps** (RATE-005, flag `COUNTER_WRAP_32`). A 32-bit counter that went down is accepted as a wrap only when all of these hold:

- every other check passed;
- the line rate is known;
- the wrapped difference is physically possible at that rate.

Error counters are always 32-bit, because IF-MIB defines no 64-bit error counters, so they can wrap even on HC interfaces. *Line rate* is the largest of the capacity override, `ifHighSpeed`, and `ifSpeed`. A contracted rate is not used here, because it does not limit what the port counts.

**Missing counters.** If one counter is missing from the reply (`MISSING_FIELDS`) or from the previous sample, only that metric is unavailable. The other rates are computed normally.

### Utilization

Utilization is a rate divided by a capacity. The denominator comes from the first available source in this order (RATE-008), and the UI names the one used:

1. the capacity override;
2. the contracted rate, only when it is configured as the utilization denominator;
3. `ifHighSpeed`, converted from Mb/s;
4. `ifSpeed`.

A speed of 0 means the agent does not know. An `ifSpeed` pinned at 4,294,967,295 means the link is faster than the field can hold. Neither value is used. With no usable source, rates are shown in absolute units and utilization is unavailable, flagged `CAPACITY_UNKNOWN` (RATE-010).

A rate above 1.10 × capacity is kept and flagged `RATE_EXCEEDS_CAPACITY` (RATE-009). It is never clipped: it usually means the configured capacity is wrong, which is worth seeing.

## Interface lifecycle

| State | Entered when | Status checks | Counters |
|---|---|---|---|
| Active | Discovered and admin up, oper up at the last Stage 1 poll | every poll | collected |
| Inactive | Admin down; oper down, lowerLayerDown, notPresent, dormant, or testing; or missing from the latest discovery | every poll | not collected |
| Stale | Inactive continuously for `lifecycle.stale_after` (10 days) | at the discovery cadence | not collected |
| Retired | Administrator action only | none | not collected |

Every state keeps its history and stays visible to discovery, so a returning interface is matched to its old identity rather than given a new one (LIFE-002, LIFE-005). An interface returns to Active on its first up/up Stage 1 result, or when rediscovery finds an interface that had gone missing.

Every change is recorded with the prior state, new state, reason, effective time, source (Stage 1, discovery, timer, or administrator), and the administrator's name for admin actions (LIFE-006). The move to Stale is recorded at the moment the 10-day threshold was crossed, not when vakr happened to notice. A stale interface remembers *why* it went inactive.

The 10 days count from when the interface became inactive, which vakr stores with it (`inactive_since`, LIFE-003). Only lifecycle changes move that time: editing a down interface's annotation, tags, or comment does not restart the countdown, and neither does a new cause, such as a down port being shut. Returning to Active clears it, retiring clears it, and restoring a retired interface starts a new countdown at the restore (Appendix E.6).

These inputs are not evidence and change nothing:

- `ifOperStatus` `unknown`;
- a missing status;
- a result older than the current state.

A retired interface returns to service as Inactive and becomes Active when Stage 1 next sees it up/up.

## Rankings

**Top utilization** ranks interfaces by the higher of their inbound and outbound 95th-percentile utilization over the window, 15 minutes by default (DASH-003). The ranking also shows which direction set the score and which denominator was used.

**The 95th percentile is the nearest-rank percentile.** Sort the available samples and take the one at position `ceil(0.95 × n)`. It is always a real sample, never an interpolated value, and the rollups use the identical definition (RET-004), so a dashboard and a report never disagree. With 15 one-minute samples the p95 is the highest sample; with 30 half-minute samples it is the second highest.

**Top errors** ranks by the sum of valid inbound and outbound error *deltas* over the window. Cumulative error counters are never ranked (DASH-004). Errors per million packets, `10⁶ × errors / packets`, is shown when every counted sample also has packet deltas and at least one packet moved.

**Coverage** (DASH-005). Samples with an unavailable baseline are left out, never counted as zero. Every entry shows how many samples it rests on, for example "12 of 15 samples", and is marked incomplete when that is short of the window. Interfaces with no usable sample are listed separately with the reason: "no valid baseline in the window", or "capacity unknown; utilization unavailable".

## Quality flag reference

Flags are combinable and stored as one integer per observation. Bit positions are part of the observation contract and never change.

| Bit | Flag | Meaning |
|---|---|---|
| 0 | `PARTIAL_LOSS` | Some probes were lost, but loss is below the impaired threshold (4 of 5 by default). |
| 1 | `SINGLE_REPLY` | Exactly one probe answered; SNMP is skipped under `icmp_gated`. |
| 2 | `NOT_ATTEMPTED` | The poll was not made because of capacity or scheduling. The state is Unknown, not failed. |
| 3 | `COLLECTOR_DEGRADED` | The collector was unhealthy, so its result cannot prove failure. |
| 4 | `STALE` | Older than the freshness threshold. |
| 5 | `PARTIAL_RESPONSE` | Some SNMP varbinds or subtables failed; the rest is kept. |
| 6 | `MISSING_FIELDS` | Specific fields were absent from an otherwise good reply. |
| 7 | `BASELINE_FIRST_SAMPLE` | No rate: first sample. |
| 8 | `BASELINE_MISSING_PRIOR` | No rate: no previous sample or value. |
| 9 | `BASELINE_GAP` | No rate: the gap was too long. |
| 10 | `BASELINE_UPTIME_RESET` | No rate: agent restart or inconsistent uptime. |
| 11 | `BASELINE_DISCONTINUITY` | No rate: the agent reported a counter discontinuity. |
| 12 | `BASELINE_WIDTH_CHANGE` | No rate: HC/32-bit switch. |
| 13 | `BASELINE_DECREASE` | No rate: unexplained decrease. |
| 14 | `BASELINE_IDENTITY_UNRESOLVED` | No rate: interface identity not settled. |
| 15 | `COUNTER_WRAP_32` | A plausible 32-bit wrap was accepted; the rate is valid. |
| 16 | `RATE_EXCEEDS_CAPACITY` | Rate above 1.10 × capacity; kept, not clipped. |
| 17 | `CAPACITY_UNKNOWN` | Absolute rates only; utilization unavailable. |
| 18 | `UPTIME_AHEAD` | Uptime further ahead than the wall clock allows. |
| 19 | `CLOCK_UNSYNCED` | Collector clock trouble, such as time going backwards. |
| 20 | `MAINTENANCE` | Collected while the device was in maintenance. |
| 21 | `REQUEST_SIZE_REDUCED` | SNMP requests were made smaller after a tooBig error (SNMP-017). |
| 22 | `RETRIED` | More than one SNMP attempt was needed (SNMP-018). |
| 23 | `BASELINE_ELAPSED_INVALID` | No rate: identical or reversed timestamps. |

Flags 7 to 14 and 23 mean every rate of that sample is unavailable. On a single metric, the same flags explain why only that metric is missing.

## Decisions beyond the Appendix E text

These choices make the rules safe at their edges. Each is covered by tests that cite it.

- **Exact arithmetic for summaries.** E.7 says summaries are computed in floating point. vakr computes them exactly and rounds once, half to even. Results equal correctly rounded float64 results, except in rare near-tie cases, where exact arithmetic is the deterministic answer.
- **A single reply is always Impaired**, even if a non-default threshold would allow more loss, because SNMP is skipped and a green device without SNMP data would mislead.
- **Uptime wrap edges.** Continuity is judged around the 2^32 circle, so a value just past the wrap is not a restart. A new value no longer than the gap is a restart even when the wrapped expected value is smaller than it. E.5's literal "lower than expected" test would call that a clock problem.
- **"SNMP answered" includes partial replies** in rows 4 and 5, per SNMP-013.
- **No reachability result is Unknown** (row 3), so a device is never green without evidence (UI-015).
- **Strategy rows.** "Skip rows referring to SNMP or ICMP" drops the other protocol's conditions rather than whole rows; dropping whole rows would make an unreachable `icmp_only` device green.
- **Composite and unknown positions.** A row-3 position is not used for "most severe" when another position has evidence. This keeps a collector outage from turning every device amber (blueprint 4.3, invariant 3).
- **Wrap checks use line rate, not the contracted rate.** A sub-rate contract does not stop the port from counting at port speed, so using it would lengthen the wrap window beyond what the hardware allows.
- **Lifecycle evidence.** `ifOperStatus` `unknown` and missing status change nothing. Rediscovery without status returns only interfaces that were missing.
