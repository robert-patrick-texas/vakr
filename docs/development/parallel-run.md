# The parallel-run comparison (MVP-ACC-03, MVP-ACC-04)

MVP acceptance asks two numeric questions of the parallel run with MRTG and SmokePing ([blueprint 27.6](../../tool-blueprint.md#276-mvp-acceptance-criteria)):

- **MVP-ACC-03.** Do vakr's 5-minute average traffic rates agree with MRTG's within ±2 %, after aligning the sample windows, with every difference outside that tolerance explained rather than tuned away?
- **MVP-ACC-04.** From the same vantage point, does vakr's median RTT agree with SmokePing's within ±1 ms or ±5 %, whichever is larger, and are its loss counts consistent with SmokePing's?

This page explains how the evidence is collected and how [`tools/acceptance`](../../tools/acceptance/README.md) computes the answer. The rules and bounds are in [Appendix E.34](../../tool-blueprint.md#e34-parallel-run-comparison-mvp-acc-03-mvp-acc-04), and the operator's side is in [field data: the parallel run](../operations/field-data.md#the-parallel-run-with-mrtg-and-smokeping).

## Data flow

```text
production host(s)                                   development host
------------------                                   ----------------
MRTG config + .log/.rrd ─┐
SmokePing config + .rrd ─┼─ vakr-fieldkit ── bundle ── field_inbox.py ── inbox/<bundle>/ ──┐
vakr acceptance export ──┘   (mrtg, smokeping,          (decrypt, verify,                    │
                              acceptance profiles)       extract)                            │
                                                                                             ▼
                                              tools/acceptance/acceptance_report.py
                                              parallel-run-report.md + .json
```

- **`vakr acceptance export`** (`src/vakr/query/parallel_*.py`, `src/vakr/cli/acceptance_export.py`) reads vakr's native observations through the telemetry read port (`read_raw`), page by page. It never downsamples: one row per interface sample (interval end, elapsed seconds, in/out bit/s, the `ifIndex` the counters came from, counter width, quality flags) and one row per ICMP cycle (every probe's RTT). A rate vakr could not derive (a first sample, a reset, a discontinuity) is an empty cell, never zero.
- **The field kit** parses MRTG's and SmokePing's configuration into allow-listed tables, copies MRTG's `.log` rows or `rrdtool fetch` output for the window, and splits the export's member stream into bundle members. It never stores raw configuration, and it redacts every credential it saw from every member. `rrdtool` runs only on the production host. The development side parses its recorded text output, so the comparison needs no rrdtool.
- **The report** merges every bundle and counts each window, sample, and cycle once. Daily bundles overlap by design.

## MVP-ACC-03: rebuilding MRTG's window

MRTG polls each target every 5 minutes, at a phase `d` after each 5-minute boundary: the cron start plus the time MRTG takes to reach this target in its run. Each poll gives one rate: the counter delta since the previous poll divided by the time between them. rateup (`.log` files) and RRD (`LogFormat: rrdtool`) both store, for each aligned window `[T−300, T)`, the time-weighted average of those per-poll rates. With polls at `T−300+d` and `T+d`, the window holds:

```text
MRTG(T) = d/300 × rate(T−600+d, T−300+d) + (300−d)/300 × rate(T−300+d, T+d)
```

vakr samples every interface about once a minute, and each sample's rate holds over its own interval (RATE-001, RATE-006). `acc_series.Cumulative` integrates those rates. The bits over any span, divided by the measured seconds in it, give vakr's average over exactly MRTG's two poll intervals. Combined with the weights above, that is what MRTG would have stored if it had seen vakr's traffic. The *coverage* is the share of those spans vakr measured. Below 90 %, the window is excluded: it is a gap in vakr's data, a restart (the sample flags say which), or an `ifIndex` change. It is never a disagreement, and it is never zero traffic.

**The phase.** `d` is unknown, but MRTG's files record the time of its last run for the target: the counter line of a `.log` file, or `last_update` of an RRD. The comparison tries every phase within ±60 s of that one, in 5 s steps, and keeps the one with the smallest median relative error over up to 400 windows. It is one phase per target for the whole run, and the report shows it. Run-to-run variation (a slow device earlier in MRTG's run delays this one) is not absorbed into the phase. It is an explanation category for the windows it explains, bounded to ±30 s.

**Bytes and bits.** MRTG's files hold bytes per second whatever `Options[]: bits` says. `bits`, `perminute`, and `perhour` change only the graphs, so every MRTG value is multiplied by 8 and nothing else. A reversed target (a leading `-`) swaps in and out, and its `MaxBytes1` and `MaxBytes2` with them.

### When a window disagrees

A window agrees when both directions are within ±2 % of MRTG's value. Otherwise it gets the first category that applies:

| Category | Meaning | How it is recognised |
|---|---|---|
| `ifindex_changed` | The device renumbered; MRTG polled another interface for part of the window | A sample in the window was read from another `ifIndex` |
| `device_restart` | A reload, reset, or rejected wrap: vakr leaves a gap, MRTG interpolates across it | Baseline flags on a sample in the window |
| `counter_wrap_32` | MRTG's 32-bit counters wrapped more than once between its polls, so MRTG under-counts | 32-bit target and vakr's rate ≥ 2³² bytes per 300 s (about 114.5 Mb/s) |
| `maxbytes_clip` | The traffic exceeded `MaxBytes` (or `AbsMax`), which MRTG discards | vakr's peak sample in the window is above the limit |
| `mrtg_rounding` | Within the 1 byte/s resolution of `.log` files | Difference ≤ 8 bit/s |
| `mrtg_repeat` | MRTG repeated its previous value after a failed poll (default without `unknaszero`) | MRTG's value equals its previous window exactly, that previous value matched vakr's previous window, and vakr's traffic moved |
| `mrtg_unknown_as_zero` | MRTG logged 0 for an unknown poll (`unknaszero`) | MRTG 0, vakr above 0 |
| `mrtg_run_jitter` | MRTG's run started earlier or later than usual this time | Agrees at a phase within ±30 s of the estimate |
| `edge_burst` | Traffic changed sharply inside a vakr sample that straddles MRTG's poll time | The difference fits within the tolerance plus the rate change across that sample, weighted by where the poll falls in it |
| `unexplained` | None of the above | Needs a person's look |

An interface **passes** with at least 288 compared windows (one day), total volume within ±2 % in both directions, and no `unexplained` window. Unexplained windows make it **review**, a volume outside ±2 % makes it **fail**, and fewer windows make it **insufficient**. Every category's count is reported. `mrtg_run_jitter` and `edge_burst` are the two categories a reviewer should weigh: they are bounded explanations of timing, not of the data.

## MVP-ACC-04: pooling probes per SmokePing row

SmokePing sends a burst of `pings` (20 by default) once per step and stores the round's median, lost count, and every RTT (sorted, with lost pings as unknowns at both ends). vakr sends five probes about 200 ms apart once a minute (ICMP-001, ICMP-002). For each SmokePing row `T`, vakr's probes from the cycles that started in `(T−300, T]` are pooled, about 25 probes.

- **RTT.** For each row, the medians agree within `max(1 ms, 5 % × SmokePing's median)`. The criterion is per series: the median of SmokePing's row medians against the median of vakr's, over every row where both have a median. Disagreeing rows are categorised: `loss_burst` (one side lost at least half its probes), `high_variance` (the RTT spread is wider than the tolerance), `round_blend` (vakr agrees over the neighbouring rows, because SmokePing's RRD averages two rounds into one row when its updates do not fall on the step boundary), or `unexplained`. They are counted but do not decide the verdict.
- **Loss.** SmokePing's lost pings over its pings are compared with vakr's lost probes over its probes, across every row both have. The two tools sample independently and with different counts, so the rates are consistent when they differ by at most 0.5 percentage points or by at most 3 standard errors of the difference of two proportions. A row where one tool lost everything while the other lost less than half is a `loss_outage`: one tool saw the device dark and the other did not.
- **Verdict.** A series **passes** with at least 288 rows compared, agreeing medians, consistent loss, and no `loss_outage` row. Outage rows alone make it **review**. A median or loss disagreement makes it **fail**, and too few rows make it **insufficient**.
- **Vantage point.** Each SmokePing series is the master or one slave. The report names the host each tool measured from and warns when SmokePing did not measure from vakr's host.

SmokePing's median is the upper middle value of an even count; vakr's is the ordinary median (9.3). With 25 pooled probes the two are the same.

## Matching

Devices match when any alias is shared. On vakr's side the aliases are the name, its first label, the poll address, and every address its ICMP cycles probed. On MRTG's and SmokePing's side they are the configured host, its first label, and the addresses it resolved to on that host. An MRTG `ifIndex` target matches the interface whose samples were read from that `ifIndex`, and only those samples are compared; `ifIndex` is never vakr's identity (invariant 6), but it is MRTG's. `#ifName`, `\ifDescr`, `/address`, and `!MAC` targets match vakr's current values. Two candidates are never merged. Ambiguous and missing matches are listed under "Not compared", with arithmetic, script, `%type`, `gauge`, `absolute`, and non-octet OID targets.

## Pseudonymized bundles

Every table member (MRTG targets, SmokePing targets, vakr's devices and interfaces) is pseudonymized column by column with the run's keyed rules. Hosts and each alias become host pseudonyms, addresses map into the pseudonym ranges, and labels keep only the classification keywords. The same name therefore gets the same pseudonym on any host that uses the same key, and the joins above still work. With MRTG and SmokePing on a second host, copy the pseudonym key there (see the operator guide). The report says when bundles were made with different keys, because then nothing joins across them.

## Committing the evidence

At the end of the parallel run, run the report over every bundle of the run. Only a report built from pseudonymized bundles is committed (the owner's decision of 2026-10-27, Appendix E.34 decision 11): a report from clear bundles names devices and addresses, and the repository is public.

- The reports go into the inbox by default, which git ignores. A report from clear bundles, or from a mix of clear and pseudonymized ones, can only be written there: `--output` anywhere else is refused with exit 2.
- A report whose bundles all share one pseudonym key says so under its title, and its JSON has `"pseudonymized": true`. Write it where the acceptance evidence is kept, for example `make acceptance-report ACCEPTANCE_OUT=docs/acceptance`, and commit `parallel-run-report.md` with the JSON beside it, or at least its checksum: the JSON lists every window.
- `make check` fails if a clear report is ever tracked (`tests/unit/test_evidence_reports.py`).

The capture (the field kit profiles and `vakr acceptance export`) must ship in a release before the run starts. The report can be refined afterwards from the same bundles, because the bundles hold the data as MRTG, SmokePing, and vakr recorded it.
