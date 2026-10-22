# Two-position system tests

`make mpos-test` runs MPOS-T01 to MPOS-T04 (blueprint 27.5) against the packaged product: two complete vakr nodes, `east` and `west`, and a simulated IOS XE fleet, in containers, with traffic cut and delayed between them. It is opt-in and never part of `make check`. The design it tests is ADR-024, "two positions" (blueprint Appendix C and 27.5; M5 part A records it in `docs/adr/`).

Contents:

- [Running it](#running-it)
- [What it builds](#what-it-builds)
- [What each test checks](#what-each-test-checks)
- [Checks that wait for M5 parts A and B](#checks-that-wait-for-m5-parts-a-and-b)
- [Decisions](#decisions)
- [When it fails](#when-it-fails)

## Running it

You need Docker with privileged containers (systemd as PID 1, nftables, and `tc` inside the nodes), uv, curl, and outbound HTTPS to the Ubuntu archive and PyPI.

```console
$ make mpos-test MPOS_QUICK=1                        # about 7 minutes, plus the package build
$ make mpos-test                                     # the full run: a 30-minute partition
$ make mpos-test MPOS_QUICK=1 DEB=dist/vakr_<version>_amd64.deb   # skip the package build
$ make mpos-test MPOS_QUICK=1 MPOS_FEATURES=roles,replication     # once M5 parts A and B exist
```

| Variable | Default | Quick (`MPOS_QUICK=1`) | Meaning |
|---|---|---|---|
| `DEB` | build one with `packaging/build-deb.sh dist/mpos-test` | | The package to test |
| `MPOS_PARTITION_SECONDS` | 1800 | 120 | How long MPOS-T01 keeps the partition |
| `MPOS_HOLD_SECONDS` | 300 | 120 | MPOS-T01's composite window, MPOS-T02's backfill window, and MPOS-T03's outage |
| `MPOS_DEVICES` | 8 | 8 | Simulated devices (2 to 200); west is cut from the first half |
| `MPOS_FEATURES` | none | none | `roles`, `replication`: turn on the checks that need M5 parts A and B |
| `MPOS_KEEP` | 0 | 0 | 1 keeps the containers afterwards, to look inside with `docker exec` |

The **Two positions** GitHub workflow (`.github/workflows/mpos.yml`) runs quick mode by hand only (Actions → Two positions → Run workflow), with an optional features list. It uploads the logs when it fails.

Each check prints `PASS`, `FAIL`, or `SKIP` with the reason, as the package tests do, and the run ends with a summary. Any `FAIL` makes it exit 1.

## What it builds

| Container | Networks and addresses | What runs |
|---|---|---|
| `vakr-mpos-sim` | device network, `172.29.71.2` and one address per device from `172.29.71.11` | snmpsim, from the `uv.lock` wheel with its hash checked, answering every device's SNMP on its own address; the kernel answers ICMP |
| `vakr-mpos-east` | inter-position `172.29.70.20`, device `172.29.71.20` | the package, booted with systemd: `vakr-collector`, `vakr-api` |
| `vakr-mpos-west` | inter-position `172.29.70.30`, device `172.29.71.30` | the same |

`tests/system/mpos/simdata.py` writes the fleet on the host from the simulator dataset (ADR-034): one snmpsim data file per device, keyed by a community generated for this run, plus the inventory and credentials file both nodes install. The communities exist only in that credentials file (mode 0600) and in the data file names under `dist/mpos-test/fleet/`, never on a command line or in output.

Each node gets its position in `/etc/vakr/conf.d/40-mpos-position.toml`, the same credentials and inventory, and `vakr inventory apply`. The collector name is the container's hostname, `east` or `west`.

Traffic shaping, inside the nodes:

| What | How |
|---|---|
| 60 ms between the positions | `tc qdisc replace dev <inter-position interface> root netem delay 30ms` on each node |
| West's path to the devices | `netem delay 60ms` on west's device interface, so west's RTT to every device is about 60 ms and east's about 0 |
| A cut | an nftables table that drops everything arriving from the listed addresses (input hook only); deleting the table heals it. Requests still leave and nothing comes back, so the collectors see timeouts, as across a real partition |

`tests/system/mpos/probe.py` runs inside a node with the package's own interpreter and reads what that node knows: `vakr status --json`, the latency history over the API socket, and per-collector row and distinct-UUID counts from `telemetry.db`, opened read-only.

## What each test checks

The tests run in this order after a warm-up in which both nodes must show every device Active.

| Test | Steps | Checks today | Checks that wait for parts A and B |
|---|---|---|---|
| MPOS-T04 | The delays above are on from the start. | Each node's own median RTT to a device: east under 15 ms, west 45 to 90 ms. Each node's latency history has one series per position, with no position or collector twice. | Each node also shows the other's series beside its own, each with its own RTT (part B). |
| MPOS-T01 | Cut east from west, and west from half the devices, for `MPOS_PARTITION_SECONDS`. | East still shows every device Active. West shows the cut devices down from west, and the others Active. Both nodes kept polling. | After east-west heals (west's device cut kept for one window), west's composite for the cut devices reads "Reachable from east only" (part B). |
| MPOS-T02 | Heal everything. | Neither node holds a duplicate observation UUID. | Each node holds at least every observation the other's collector had made when the backfill window began, family by family (part B). That the backfill runs oldest first is written with part B, from its replication cursor. |
| MPOS-T03 | Cut east (the primary) from west (the peer) for `MPOS_HOLD_SECONDS`. | West kept polling with its last-known inventory. | West refuses an inventory edit with a message naming the primary (part A). |

## Checks that wait for M5 parts A and B

Without `MPOS_FEATURES`, the nodes run as two independent single nodes with the same inventory, and the checks above that need roles or replication print `SKIP` with the part they wait for. `roles` (part A) makes east the metadata primary and west the peer, and exchanges replication tokens in `configure_roles` (`tests/system/mpos/lib.sh`). That function follows part A's contract ([two positions](../operations/two-positions.md), ADR-024):

- **Roles:** `[node] role`, `position`, and, on west, `primary_url = "https://172.29.70.20"`.
- **Tokens:** each node runs `vakr replication token create mpos-<partner>`, and the token goes into the partner's `/etc/vakr/secrets/replication.token` (mode 0640, `root:vakr`). It is never printed.
- **HTTPS between the nodes:** vakr-api listens on plain HTTP on loopback, as in production, so the harness puts `tests/system/mpos/tlsproxy.py` in front of it on each node's inter-position address, port 443. It is a test-only TLS terminator standing in for the operator's reverse proxy.
  - `tlsproxy.py gen` makes, on the host, a test CA and one server certificate per address (SAN = the address). They meet the strict X.509 profile (EGRESS-003).
  - Each node trusts the CA in `/etc/vakr/tls/ca.d/mpos-test.pem`. vakr's own TLS checks are unchanged, and no option weakens them.
- **West starts empty:** with `roles`, west installs `inventory.toml` but does not apply it. It copies east's inventory by the metadata pull, as a real peer does; applying it would give the same names other UUIDs, which the pull refuses.

## Decisions

These were decided with the harness (2026-10-22):

1. **Opt-in.** Two booted nodes, a partition, and package installs take minutes, need privileged Docker, and reach the Ubuntu archive and PyPI. `make check` stays hermetic, and the workflow runs only by hand.
2. **Real intervals.** The collector's test-only interval override (`ServiceTimings.poll_interval_override_s`) is settable from code only, never from configuration, so a packaged node cannot use it. Quick mode sets every device's `interval = "30s"` in the inventory instead, which is the shortest interval the product allows (Appendix E.18). That is why quick mode still needs about 7 minutes with a prebuilt package (`DEB=`): package installs, discovery, several polls, and two windows of 120 s, plus one more window for each of MPOS-T01's composite and MPOS-T02's backfill once `replication` is on.
3. **Where the 60 ms goes.** MPOS-T04 asks for 60 ms between the positions, which the inter-position netem provides. Latency graphs measure each position's RTT to the devices, though, which that delay does not touch. So west's path to the devices also gets 60 ms. The positions then see clearly different RTTs to the same device, and a merged series would show up as a value between them.
4. **The composite needs the other node's evidence.** MPOS-T01 asks for "Reachable from east only" on west while the nodes are cut from each other. East's evidence can reach west only by replication, which cannot cross the cut, and evidence from before the cut ages out. So the harness heals east-west first, keeps west's device cut for one window, and then checks the composite. **Contradiction recorded; choice:** the order above, which keeps both parts of MPOS-T01 testable.
5. **Counts with ongoing polling.** MPOS-T02 compares row counts while both collectors keep polling, so exact equality never holds at one instant. The harness snapshots the source's per-collector counts, waits one window, and requires the receiver to hold at least that many of the source collector's rows in every family, and both nodes to hold no duplicate UUIDs.
6. **One responder, many addresses.** snmpsim answers by community, so one responder bound to every device address serves the whole fleet, and per-device cuts are per-address firewall rules. Its Python and pysnmp come from the package itself (`dpkg-deb -x`), and snmpsim, pure Python, from the wheel `uv.lock` pins, with its SHA-256 checked.
7. **Cuts drop what arrives, not what leaves.** An nftables drop in the output hook makes the collector's own sends fail. vakr reports that, rightly, as its collector degraded (OBS-003), and then every device reads "Unknown — collector degraded" instead of the cut ones Down. A real partition loses packets in the network, so the harness drops only in the input hook: requests leave, nothing comes back, and the collector sees timeouts.
8. **Fixed names.** Containers and networks have literal names (`vakr-mpos-east`, `vakr-mpos-west`, `vakr-mpos-sim`, `vakr-mpos-inter`, `vakr-mpos-dev`), removed before a run and in its exit trap.

## When it fails

A failing run copies each node's journal, `vakr status --json`, row counts, and firewall rules, and the simulator's log, into `dist/mpos-test/logs/` before it removes the containers. `MPOS_KEEP=1` keeps the containers for `docker exec vakr-mpos-west ...`. Remove them afterwards with `docker rm --force vakr-mpos-east vakr-mpos-west vakr-mpos-sim` and `docker network rm vakr-mpos-inter vakr-mpos-dev`.
