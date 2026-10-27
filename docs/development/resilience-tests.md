# Resilience system tests

`make resilience-test` runs the MVP-ACC-06 scenarios (blueprint 27.6) against the packaged product: one vakr node booted with systemd and a simulated IOS XE fleet, in containers. The node survives a service restart, a host reboot, its telemetry store being unavailable, and an abrupt power loss, and the harness then accounts for every poll the schedule expected. The pass condition is **no silent loss**. It is opt-in and never part of `make check`; the defaults, timings, and pass conditions are in Appendix E.36.

Contents:

- [Running it](#running-it)
- [What it builds](#what-it-builds)
- [The scenarios](#the-scenarios)
- [The accounting](#the-accounting)
- [Decisions](#decisions)
- [When it fails](#when-it-fails)

## Running it

You need Docker with privileged containers (systemd as PID 1), uv, curl, and outbound HTTPS to the Ubuntu archive and PyPI.

```console
$ make resilience-test                                         # builds the package first
$ make resilience-test DEB=dist/vakr_<version>_amd64.deb       # about 14 minutes
$ make resilience-test DEB=... RESIL_SCENARIOS=restart,power   # some scenarios only
```

| Variable | Default | Meaning |
|---|---|---|
| `DEB` | build one with `packaging/build-deb.sh dist/resilience-test` | The package to test |
| `RESIL_DEVICES` | 8 | Simulated devices (2 to 50), every one polled at 30 s |
| `RESIL_OUTAGE` | 180 | Seconds the telemetry store stays unavailable in S3 |
| `RESIL_SCENARIOS` | `restart,reboot,storage,power` | Which scenarios run; they always run in this order |
| `RESIL_KEEP` | 0 | 1 keeps the containers afterwards, to look inside with `docker exec` |
| `RESIL_NET_PREFIX` | `172.29.86.` | The device network's first three octets |

Each check prints `PASS` or `FAIL`, as the package tests do, the accounting prints its report, and the run ends with a summary. Any `FAIL` makes it exit 1.

## What it builds

| Name | Address | What runs |
|---|---|---|
| `vakr-acc06-net` | `172.29.86.0/24` | The device network. The two-position tests use `172.29.70.0/24` and `172.29.71.0/24`, so both harnesses can run at once. |
| `vakr-acc06-sim` | `172.29.86.2` and one address per device from `172.29.86.11` | snmpsim, from the `uv.lock` wheel with its hash checked, answering every device's SNMP on its own address; the kernel answers ICMP |
| `vakr-acc06-node` | `172.29.86.20`, hostname `acc06` | The package, installed and set up as [the install guide](../operations/install.md) says, booted with systemd: `vakr-collector`, `vakr-api` |

The fleet comes from the two-position harness's `tests/system/mpos/simdata.py` with `--interval 30s`, the shortest interval the product allows (E.18). The communities exist only in the credentials file (mode 0600 on the host, 0640 `root:vakr` on the node) and in the data file names, never on a command line or in output.

Setup follows the first-run checklist: `vakr setup --username acc06-admin` with a password generated for the run and piped in twice (never printed or stored), the credentials file, `vakr credentials check`, `vakr inventory apply`, and `systemctl enable --now vakr-collector.service vakr-api.service vakr.target`. Nothing is started by hand after that: the reboot and power-loss scenarios check that systemd brings both services back.

Three helpers run inside the node with the package's own interpreter:

- `tests/system/resilience/probe.py` reads what the node knows: `vakr status --json`, the API over its socket, the stored poll records, ICMP results, and collector-health reports through the telemetry port (`read_raw`), the spool's `status.json`, and every telemetry-loss incident. Telemetry and spool reads run as `vakr`, so no file becomes root's.
- `tests/system/resilience/faults.py lock` holds the telemetry database's write lock, as a long backup or a stuck job would; `faults.py powercut` removes a frozen collector's unsynced spool tail and tears its last write.
- `tests/system/resilience/accounting.py` runs on the host and decides the pass condition from what the probe read.

## The scenarios

Before any fault, a baseline: `vakr check health` is OK, nothing waits in the spool, there is no incident, and no device is Down. Each scenario ends in the same steady state (a poll stored for every device since the collector came back, every device Active, none Down, `vakr check health` OK), except that after S4 the check stays WARNING for the 24 hours E.24 gives a telemetry-loss incident.

| Scenario | What the harness does | What it checks |
|---|---|---|
| S1 service restart | Waits until a poll is under way, then `systemctl restart vakr-collector vakr-api`. | A new collector run starts; the old one logged `collector.stopped`; no incident; vakr-api answers on its socket again. |
| S2 host reboot | `systemctl reboot` inside the node; a container's systemd exits when it reboots, so `docker start` is the power coming back. | The node shuts down cleanly within 120 s; systemd starts vakr-collector and vakr-api by itself; no incident. |
| S3 telemetry store unavailable | Holds the telemetry database's write lock for `RESIL_OUTAGE` seconds; restarts the collector once in the middle; then releases the lock. | `vakr check health` says the database is not accepting writes and then counts ingestion behind as a second problem (`+1 more`, `spool_oldest_s` past 60 s), WARNING and never CRITICAL; polling keeps reaching the spool, before and after the restart; no device is Down, and once their data is stale every device reads "Unknown — …" (E.2 row 3); after the release the backlog drains. |
| S4 abrupt power loss | (a) `systemctl kill --signal=SIGKILL` the collector mid-poll; systemd restarts it. (b) `docker kill` the whole node mid-poll, then start it. (c) A power cut that also loses the page cache: freeze the collector with SIGSTOP just after a spool append, remove the records no fsync may have covered yet (those of the last `spool.fsync_interval` that ingestion has not acknowledged) and leave half a frame at the end (`faults.py powercut`), then `docker kill` and start the node. | Each recovery records an `unclean_stop` telemetry-loss incident, and (c) a `torn_tail` as well; `vakr check health` warns of it and the overview's telemetry-loss feed lists it; systemd brings the services back; no device is Down. Every poll (c) removed must be missing inside the E.8 window. |

## The accounting

Slot `k` of a device is `k × interval + offset` (E.25 decision 1), so once one stored poll shows a device's offset, every slot it was due in the window is known. The harness logs when each collector run became ready (`READY=1`, systemd's `ActiveEnterTimestampMonotonic`) and when it stopped the run and how, then reads every stored poll record and incident. The accounting waits two intervals after the last recovery, so its window covers it. Each expected slot from the end of the warm-up to 45 s before the accounting is then exactly one of:

| Outcome | Meaning |
|---|---|
| stored | A poll record exists, whatever its execution state. `not_attempted` and `cancelled` records are explicit (E.25 decision 8). |
| not running | No collector run covered the slot: the outage the views show as the collector unavailable (invariant 3). |
| E.8 window | The slot was due within `fsync_interval` (1 s) + 1 s of hand-off + the longest poll before an abrupt stop, and recovery recorded an `unclean_stop` incident when the collector came back (E.8, STORE-007). |
| unexplained | Anything else: silent loss. The run fails. |

The run also fails on:

- two records for one slot (SVC-T04);
- a complete poll without its ICMP result;
- an ICMP result of `unreachable`: the simulated devices never go down, so that would be collector trouble reported as the target (invariant 2);
- an abrupt stop without an `unclean_stop` incident, or a clean stop with one;
- any other incident outside an abrupt stop's recovery.

`tests/system/resilience/test_accounting.py` checks these rules on every `make check`.

## Decisions

These were decided with the harness (2026-10-27):

1. **Opt-in.** A booted node, a reboot, a held lock, and three kills take about 14 minutes, need privileged Docker, and reach the Ubuntu archive and PyPI. `make check` stays hermetic; the accounting's rules run there as unit tests.
2. **"Unavailable" is a held write lock.** The store becomes unavailable to writers while it stays readable, as during a long backup or a stuck maintenance job: every write fails after the adapter's 5 s busy timeout (E.20) with a `StorageError`, exactly the path a full or failing disk takes in the ingester. A full filesystem was not used: in the Compact profile the spool and `telemetry.db` share `/var/lib/vakr` (the collector unit may write only there), so filling it fills the spool too, which is the separate `rejected_full` path (6.4, SPOOL-T07, unit-tested).
3. **A process kill cannot lose the page cache, so S4c simulates it.** `docker kill` stops every process at once, but the host's kernel keeps whatever reached the page cache, and recovery fsyncs it (E.24 "Unclean stop"). S4a and S4b therefore prove the incident and that nothing beyond the E.8 window goes missing, but lose nothing themselves. S4c removes what a real power cut could take: the records appended in the last fsync interval that ingestion had not acknowledged, cut at a frame boundary, plus a torn half frame. It freezes the collector right after an append, so those records exist; the accounting then requires each poll it removed to be missing inside the E.8 window.
4. **Real intervals.** The collector's test-only interval override is settable from code only, so the inventory sets every device to 30 s, as the two-position tests do.
5. **When a run starts.** A run starts when systemd records `READY=1`: the scheduler starts just before it, and no slot is polled before it.
6. **Fixed names.** Containers and the network have literal names with the prefix `vakr-acc06-`, removed before a run and in its exit trap.
7. **Found and fixed with the harness: the unclean-stop incident under bursty appends** (SPOOL-T08). Poll results reach the spool in bursts. S4c removed one poll's 43 records, appended about 2 s after a quiet spell, and recovery reported 13 records in a range that ended 1 s after the last durable record, so it missed the lost burst. The incident now counts the busiest fsync interval among the newest records and runs from the last durable record to the recovery that found it (Appendix E.24 "Unclean stop", E.36 decision 4).

## When it fails

A failing run copies the node's journal, `vakr status --json`, `vakr check health`, and incidents, and the simulator's log, into `dist/resilience-test/logs/` before it removes the containers; the run log (`events.tsv`), the stored polls (`polls.tsv`), and the incidents (`incidents.jsonl`) stay in `dist/resilience-test/`. `RESIL_KEEP=1` keeps the containers for `docker exec vakr-acc06-node ...`. Remove them afterwards with `docker rm --force vakr-acc06-node vakr-acc06-sim` and `docker network rm vakr-acc06-net`.
