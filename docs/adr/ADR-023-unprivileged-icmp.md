# ADR-023: Unprivileged ICMP datagram sockets, shared per address family

| Field | Value |
|---|---|
| Status | Accepted 2026-10-19 (owner); implementation design accepted with M3 on 2026-10-20 |
| Date | 2026-10-20 |
| Decides | How the collector sends ICMP Echo probes without `CAP_NET_RAW`, how it matches replies, how it bounds itself, and how collector trouble is kept apart from target loss |
| Requirements | Blueprint 1.2 (ICMP privilege), 4.3 invariants 1-3, 6.2, 8.6 ICMP-001..014, 8.24 OBS-001..003, 9.3, 9.11, 15.3, 15.5, 16.3, 21.9; Appendix E.1, E.7, E.9, E.17, E.22 |
| Evidence | Measurements below, on the 4 vCPU development host (kernel 7.0, Python 3.15); tests in [`tests/unit/adapters/icmp/`](../../tests/unit/adapters/icmp/__init__.py) and [`tests/integration/icmp/`](../../tests/integration/icmp/__init__.py) |

## Context

The blueprint first isolated `CAP_NET_RAW` to an "ICMP component" that the process model never defined, while `icmp_gated` needs ICMP and SNMP in the same poll (22.1). The owner resolved that on 2026-10-19: ICMP uses Linux unprivileged ICMP datagram sockets, so no vakr process needs `CAP_NET_RAW` by default, and a collector-only raw-socket fallback is documented for hosts that cannot enable them (1.2, ICMP-012, 15.3).

A Linux ping socket is `socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)` (or `AF_INET6`/`IPPROTO_ICMPV6`). The kernel lets a process open one only if its effective or a supplementary GID is inside `net.ipv4.ping_group_range`, which governs IPv6 too. The kernel then computes the checksum and **replaces the Echo identifier with the socket's own**, and delivers to the socket only replies carrying that identifier. Errors such as "host unreachable" reach the socket only if `IP_RECVERR` is set.

Two facts about deployment shape the operator side. `systemd-sysusers` allocates the `vakr` group a system GID below 1000. Hardened images often narrow the range: the development host has `/etc/sysctl.d/99-allow-ping.conf` setting `1000 9999`, overriding systemd's `0 2147483647`. A default install there is refused.

## Decision

### Sockets

* **One non-blocking ping socket per address family and pinned source**, shared by every target and driven by the asyncio event loop (`loop.add_reader`). No thread per probe, and no blocking call. A device with a pinned source (9.11) gets a socket bound to that source, so the socket count grows with distinct sources, never with targets. The pool holds at most 8 sockets and closes an idle one to make room; when every socket is busy the probe is `NOT_SENT`/`SOCKET_LIMIT`.
* Each socket sets `IP_RECVERR` (`IPV6_RECVERR`), `SO_TIMESTAMPNS`, and a 1 MiB `SO_RCVBUF` request.
* **Raw fallback.** If the kernel refuses a ping socket with `EACCES`/`EPERM`, the pool tries a raw socket, which works only if the collector unit was granted `CAP_NET_RAW` by the example drop-in in [`packaging/examples/`](../../packaging/examples/vakr-collector.service.d/50-icmp-cap-net-raw.conf). Raw sockets set their own identifier and IPv4 checksum, strip the IPv4 header, ignore other programs' ICMP, and read errors as ordinary ICMP packets. If the raw socket is refused too, the original `ping_group_range` refusal is reported, because its fix is the preferred one.

### Matching

* Every probe carries a 12-byte payload: the magic `vakr` and an 8-byte token from `secrets`. A reply counts only if its **sequence number, token, and source address** all match a waiting probe. The identifier is ignored on ping sockets because the kernel rewrites it.
* Sequence numbers come from a per-socket 16-bit counter that skips numbers still waiting, so a number is reused as late as possible.
* A bounded memory of finished probes (twice the in-flight cap) classifies stray replies: **late** (its probe timed out), **duplicate** (already answered), or **foreign** (anything else). All three are dropped and counted (OBS-001).
* ICMP errors from the error queue match by sequence number, by the token when the router quoted enough of the request, and by the original destination. Remote unreachable, time-exceeded, and parameter-problem messages resolve the probe as `UNREACHABLE` (lost, RTT `-1`) with the reason and the reporting router. Redirects, source quench, and "packet too big" are ignored.
* An `IP_RECVERR` socket reports a queued error on its *next* call, so a send to one target can fail with an unreachable meant for another. When a send fails with such an errno, the error queue is drained and the send retried, at most twice.

### Timing and bounds

* Send times are anchored to the cycle start (`start + i × spacing`), so lateness never accumulates (invariant 10).
* **RTT** runs on the monotonic clock from just before `sendto` to the reply's arrival. The read time is corrected by how long the kernel says the reply sat unread (`SO_TIMESTAMPNS`). An implausible stamp, such as one in the future after a wall-clock step, is ignored. A reply arriving after its timeout counts as late, so no stored RTT exceeds the timeout.
* A **global cap on probes in flight** (`icmp.max_in_flight`, 1,024) is a semaphore. The **per-cycle deadline** is `(count − 1) × spacing + timeout + 200 ms` (2.0 s by default). A probe is sent only if it gets a slot early enough for its whole timeout to fit before the deadline; otherwise it is `NOT_SENT`/`OVERLOADED`.
* Each socket's **pending-reply table** is capped at the in-flight cap. Each wakeup reads at most 256 datagrams, so a reply storm cannot starve other work.

### Collector trouble is collector evidence

Every outcome is one of `REPLY`, `TIMEOUT`, `UNREACHABLE` (evidence about the target) or `NOT_SENT`, `ABORTED` (evidence about the collector, each with a `CollectorFault`). The collector summarises only the first three, so an unsent probe never counts as loss. Any fault makes the cycle `COLLECTOR_DEGRADED`, so zero replies becomes `UNKNOWN`, never `UNREACHABLE` (invariants 2-3, OBS-002, OBS-003). A cycle with nothing sent produces no ICMP observation and is `UNKNOWN`/`NOT_ATTEMPTED`.

| Where | errno or event | Fault |
|---|---|---|
| `socket()` | `EACCES`, `EPERM` | `PERMISSION_DENIED` (`ping_group_range`) |
| `socket()` | `EAFNOSUPPORT`, `EPROTONOSUPPORT` | `FAMILY_UNSUPPORTED` (for example IPv6 disabled) |
| `bind()` | `EADDRNOTAVAIL` | `SOURCE_UNAVAILABLE` |
| `sendto()` | `ENETUNREACH`, `EHOSTUNREACH` | `LOCAL_NO_ROUTE`: this host has no route, so a lost default route cannot mark every device down |
| `sendto()` | `EACCES`, `EPERM` | `LOCAL_POLICY` (local firewall, broadcast) |
| `sendto()` | `EAGAIN`, `ENOBUFS`, `ENOMEM`; no slot before the deadline | `OVERLOADED` |
| read | a non-ICMP error | the socket closes; waiting probes are `ABORTED`/`SOCKET_ERROR`, and the next cycle opens a new socket |
| `SO_MEMINFO` | the kernel drop counter rose during the cycle | `RECEIVE_OVERFLOW`: replies may have been dropped by our own full buffer |
| error queue | local origin | `ABORTED`/`SEND_ERROR` |

### Preflight and doctor

`vakr doctor` and `vakr check health` run the `icmp` check. On an installed node it tests the `vakr` service user's primary GID against the running range. In a checkout (`VAKR_ROOT` set, or no `vakr` account) it tests this process's groups and opens a ping socket to prove it. When the group is excluded, the summary carries the exact fix. It widens the range just enough to include the GID and never narrows it for anyone else. The fix goes in `/etc/sysctl.d/99-vakr-icmp.conf`, followed by `sudo sysctl --system`. If a later file sets the key (a name that sorts after the drop-in, or `/etc/sysctl.conf`, which `sysctl --system` reads last), the fix names that file and line instead. On the development host the check prints:

```text
unprivileged ICMP refused: the vakr service user (GID 987) is outside net.ipv4.ping_group_range 1000 9999; fix: echo 'net.ipv4.ping_group_range = 987 9999' | sudo tee /etc/sysctl.d/99-vakr-icmp.conf && sudo sysctl --system
```

The result is **WARNING**, not CRITICAL. `check health` gates self-update (UPD-006), and a CRITICAL would roll back every update on a host that has not applied the fix yet, while a rollback cannot fix a host setting. Devices show UNKNOWN, never down, while the setting is wrong. When the `CAP_NET_RAW` drop-in is installed, the check is OK and names it.

**The package does not change the sysctl.** A packaged sysctl.d file would silently override an administrator's deliberate hardening: `99-vakr.conf` from `/usr/lib/sysctl.d` sorts after `/etc/sysctl.d/99-allow-ping.conf` and would win. The operator applies the one-line fix that `vakr doctor` prints.

## Alternatives

* **One socket per target.** Measured below. It costs about 1.5× the CPU per cycle and 100 file descriptors instead of 1 for 100 targets, with no accuracy gain. Per-target sockets would also need an identifier per socket from the same kernel space, and an epoll registration and teardown per target per cycle. Rejected.
* **Raw sockets by default.** They need `CAP_NET_RAW` in the collector, see all host ICMP traffic, and need userspace filtering. Rejected by the owner (1.2); kept as the fallback.
* **Matching by identifier.** Impossible on ping sockets, which rewrite it. Sequence plus token works on both socket kinds.
* **No `IP_RECVERR`.** Simpler, but unreachable messages would look like plain timeouts, and operators lose "who said the host is unreachable". Kept, with the queued-error retry.
* **User-space receive time only.** Measured below. Under a burst of 100 sends the median loopback RTT reads 1.2 ms instead of 15 µs, because replies wait while the loop finishes sending. Rejected; arrival stamps are used whenever the kernel provides them.
* **CRITICAL health severity.** Rejected for the self-update reason above.
* **Changing the sysctl in `postinst`.** Rejected for the hardening reason above.
* **Shelling out to `fping` or `ping`.** Adds a process per cycle and parses text, and "avoid invoking shell commands with target input" (15.5) argues against it. Rejected.

## Rationale

The collector's measurement must not depend on how busy the event loop is, and its own trouble must not look like the network's. Sharing one socket per family keeps the per-probe path to one `sendto`, one timer, and one table entry. The kernel's arrival stamp removes event-loop lag from RTTs. Classifying every outcome as target evidence or collector evidence, at the place the error happens, lets the pure E.1 logic stay unchanged and still honour invariant 2.

## Benefits

* No process needs `CAP_NET_RAW` by default. The API and web processes never get it (15.3).
* Two sockets serve the MVP's 100 devices. Socket count grows only with pinned sources.
* RTTs within tens of microseconds on loopback, even under a burst.
* Every bound is explicit: in-flight probes, sockets, pending table, recent memory, reads per wakeup, retries, cycle deadline.
* Operators get the exact fix, and `vakr icmp probe` shows per-probe outcomes in operator language.

## Risks

* **Magic numbers.** Python does not export `SO_TIMESTAMPNS` (35) or `SO_MEMINFO` (55). The values are the asm-generic ones used on amd64 and arm64, and an integration test checks both on the build host. Other architectures would need a check. Without them, RTTs include loop lag, and the overflow check is skipped rather than guessed.
* **A shared receive buffer** can overflow under a large reply burst. It is detected (`RECEIVE_OVERFLOW`) and never reported as loss, but those cycles carry less information. At 1,000+ targets per collector, raise `net.core.rmem_max` so the 1 MiB request is granted.
* **Compact storage holds five RTT columns** (ADR-021). `icmp.probes` is therefore capped at 5. A cycle with unsent probes stores fewer than five RTTs, so the telemetry adapter must not fill missing columns with `-1`, which would read as loss (see Compatibility).
* **The raw fallback is exercised only by unit tests** with a fake socket; no CI host grants `CAP_NET_RAW`.
* **`icmp_gated` when ICMP cannot run.** If the collector cannot probe at all, `vakr.logic.icmp.snmp_plan` sees 0 replies and skips SNMP, so a misconfigured host collects nothing until the sysctl is fixed. The doctor WARNING says so. Whether SNMP should run when ICMP was not attempted is an open owner question.

## Security impact

Lower than raw sockets: no capability by default. When used, the fallback is confined to the collector unit by a drop-in the operator installs by hand. Tokens come from `secrets`, so an off-path host cannot forge a matching reply without seeing the request. Targets are validated as canonical unicast literals: multicast, unspecified, limited broadcast, and IPv4-mapped targets are refused (15.5). The CLI never passes target text to a shell. Name resolution in `vakr icmp probe` is bounded to 2 s. Nothing secret is logged or printed.

## Performance impact

Measured on the development host (4 vCPU, shared with four other agents' test runs) against `127.0.0.1`-`127.0.0.100`, 100 targets × 5 probes, five or more cycles per row:

| Variant | CPU per cycle | Cycle wall time | Descriptors | RTT median / p99 / max |
|---|---|---|---|---|
| Shared socket, 200 ms spacing | 31-34 ms | 0.81 s | 1 socket | 11-12 / 68-72 / 449-743 µs |
| Socket per target, 200 ms spacing | 49-52 ms | 0.81 s | 100 sockets | 15-18 / 69-84 / 169-2,498 µs |
| Shared socket, no spacing (burst) | 20-23 ms | 0.02 s | 1 socket | 8-10 / 23-28 µs |
| Socket per target, no spacing | 30-32 ms | 0.03 s | 100 sockets | 10 / 29-34 µs |
| Shared, 200 ms, arrival stamps | | | | 14-15 / 30-31 / 92 µs |
| Shared, 200 ms, read time only | | | | 1,190-1,240 / 3,330-3,425 / 3,682-3,827 µs |

The E.9 budget holds: 100 targets × 5 probes at 200 ms spacing finish in 0.81 s, inside the 2 s cycle. The integration test asserts under 2.0 s.

## Compatibility and migration impact

This is new code, with no stored data to migrate. New options are in one `[icmp]` block (`icmp.probes`, `icmp.spacing`, `icmp.timeout`, `icmp.impaired_loss_percent`, `icmp.impaired_rtt_ms`, `icmp.max_in_flight`). Their defaults keep the E.1 and E.9 behaviour, so no config migration is needed. `icmp.impaired_rtt_ms = 0` means "off", because the catalog has no unset value.

The telemetry adapter (M2-tele) must accept `IcmpObservation.rtts_us` with 1-5 entries. Two options were not decided here: store unused columns as `NULL` (a schema change from ADR-021's `NOT NULL`), or keep `sent_count` authoritative and document that columns past it are meaningless. The owner or the telemetry adapter decides; either way, never `-1`.

## Test changes

* Unit tests over a fake socket layer (an `eventfd` drives the real event loop): out-of-order replies with exact RTTs (ICMP-T01), duplicates, foreign tokens and sources, late replies after the deadline, RTT above the timeout, arrival stamps, router unreachables from the error queue, IPv6 and source binding (ICMP-T06), permission refusal and raw fallback (ICMP-012), send-error mapping, the stale queued error, broken reads, the in-flight cap and deadline, receive overflow, cancellation, the socket limit, and shutdown.
* Property tests (hypothesis): decoders never raise on arbitrary bytes; raw checksums verify; table bounds hold and each probe is answered at most once.
* Collector tests for ICMP-T01..T04 and T06, preflight tests including the development host's exact case, CLI tests, and the static half of ICMP-T07 (no packaged unit grants `CAP_NET_RAW`; the example is not packaged).
* Integration tests against `127.0.0.1` and `::1`, the E.9 timing test, and a check of the hard-coded socket option numbers. They skip with a reason when unprivileged ICMP is unavailable.

## Documentation changes

[ICMP operations](../operations/icmp.md), the generated [configuration reference](../operations/configuration.md), blueprint Appendix E.22, and this ADR.

## Rollback and revisit criteria

Revisit when any of these holds:

* A collector serves more than 1,000 targets and `RECEIVE_OVERFLOW` appears: consider a socket per target group, or a larger receive buffer.
* The `ping_group_range` model changes in the kernel, or an MVP host cannot enable it at all: make the raw fallback a first-class tested path.
* Compact storage gains variable-length RTTs: lift the five-probe cap.
* vakr ships on an architecture where `SO_TIMESTAMPNS` or `SO_MEMINFO` differ.

Rolling back to a socket per target means changing the channel key from (family, source) to (family, source, target). Matching, bounds, and fault mapping are unchanged.
