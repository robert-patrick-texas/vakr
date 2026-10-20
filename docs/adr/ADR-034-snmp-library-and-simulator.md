# ADR-034: SNMP library, adapter structure, and simulator

| Field | Value |
|---|---|
| Status | Proposed; implemented in milestone M3 and awaiting owner review |
| Date | 2026-10-20 |
| Decides | Which SNMP library vakr uses and how; how its secrets and failure modes are contained; which simulators back the tests; where the SNMP settings live |
| Requirements | Blueprint 4.2, 8.3, 8.7, 9.4-9.6, 15.2, 15.5, 23, 27.4, Appendix E.4, E.9, E.18, E.23; SNMP-001, SNMP-004..SNMP-019, DISC-004..DISC-009, SEC-003 |
| Evidence | Test suites in [`tests/simulators/snmp/`](../../tests/simulators/snmp/__init__.py) and `tests/unit/{ports,adapters/snmp,discovery,collectors/snmp}`; the load check `make snmp-load` |

## Context

SNMP-015 requires "current asyncio-capable PySNMP APIs" and no blocking SNMP calls on the event loop. Milestone M3 (27.8) asks for a simulator "built from field captures (snmpsim)". The MVP polls only SNMPv2c; SNMPv3 is deferred (27.4) but must fit later without redesign. vakr runs on Python 3.15.

On 2026-10-20 the current releases were:

* **pysnmp 7.1.30** (lextudio, 2026-09-28), which needs `pyasn1>=0.6.4`. It offers two asyncio APIs. `hlapi.v3arch` is built on an `SnmpEngine` with a local configuration datastore, and `hlapi.v1arch` is a lighter `SnmpDispatcher` that speaks SNMPv1/v2c only.
* **snmpsim 1.2.2** (lextudio, 2026-04-26), which needs `pysnmp>=7.1.26,<8`.

Both install and run under CPython 3.15.0. snmpsim's responder imports `pysmi` without declaring it, so it fails at startup unless `pysmi` (2.0.0) is installed too. With that, the responder started, served GET and GETBULK from `.snmprec` files, advanced values through its `numeric` variation module, and served the full 100-device MVP fleet.

Reading PySNMP's v1arch code found five places where the community or a failure could leak or be misreported:

1. `CommunityData.__repr__` prints the community.
2. The `pysnmp.debug` and `pyasn1.debug` loggers hexdump raw packets, which contain the community.
3. An undecodable datagram raises inside an asyncio callback. The loop's exception report includes the callback's arguments, which are the raw bytes, and a reply echoes the community.
4. Replies are matched by request ID only, from any source address.
5. The client socket is opened in a background future. If that fails, requests silently time out, which would look like the device being down (invariant 2).

## Decision

### Library and adapter

* **Runtime dependency `pysnmp>=7.1.30`**, used through the **`hlapi.v1arch.asyncio`** API (`SnmpDispatcher`, `get_cmd`, `next_cmd`, `bulk_cmd`) for SNMPv2c. Varbinds are passed as plain `(ObjectName, Null)` pairs, so PySNMP never loads MIBs.
* **Each session gets its own dispatcher, and therefore its own UDP socket.** The session context manager closes it even after a timeout or cancellation.
* **PySNMP's own retries are off.** vakr's `RequestEngine` (`vakr.adapters.snmp.engine`) sends complete new attempts with fresh request IDs and counts each one (SNMP-011, SNMP-018). It holds a per-device lock around every PDU (SNMP-012).
* **The version-neutral logic** is in `engine`, `getbatch`, and `walking`: GET batching under `max_varbinds`, halving after tooBig, and GETBULK walks. It talks to a `PduTransport` protocol. SNMPv3 will be a second transport (`hlapi.v3arch`, or the PDU layer) behind the same engine, with nothing above the port changing.
* **No PySNMP or pyasn1 type crosses the port** (`vakr.ports.snmp`). Values are converted by ASN.1 tag set in `codec`.

### Guards (`vakr.adapters.snmp.guards`)

* `RedactedCommunity` subclasses `CommunityData` with a redacted `repr`.
* `quiet_library_debug()` runs before every session. It turns off PySNMP and pyasn1 packet debugging, and warns, if either was on.
* `GuardedDispatcher` overrides `_recv_callback`. It drops datagrams that do not come from the polled agent's address and port, and catches decode errors, counting them and logging a fixed message without the bytes.
* After a timeout, the transport checks that PySNMP's socket actually opened. If it did not, the failure is reported as a `TRANSPORT` collector problem.
* A source address is test-bound before the session starts.
* Library exceptions are converted outside their `except` blocks, so the new error does not keep the library frames, which hold the encoded packet, as context.

### Credentials

The community is resolved by name from `vakr.credentials`, in a worker thread, when the session opens. Only the transport holds it, and it is dropped when the session closes (15.2). A canary test checks logs at DEBUG (with asyncio debug mode on), every `repr`, exception text, tracebacks with captured locals, and CLI output. The CLI refuses `--community` and `-c` without echoing the value.

### Simulators

The dataset comes from a deterministic generator for 80 IOS XE-like routers and 20 Catalyst-like switches, with exactly 1,600 up/up interfaces. It uses RFC 5737 and RFC 3849 addresses only. Communities are generated at test time. Two servers read it:

* **snmpsim** (development dependencies `snmpsim>=1.2.2` and `pysmi>=2.0.0`) is an independent agent implementation. It serves `.snmprec` files written to a temporary directory, which keeps communities out of the repository and off command lines. It backs discovery, Stage 1/2, CLI, and load tests.
* **An in-process asyncio agent** (`tests/simulators/snmp/agent.py`), built on PySNMP's protocol layer, is the fallback for faults snmpsim cannot script deterministically. `--max-var-binds` truncates GETBULK rather than answering tooBig. The `error` variation fails an OID whatever the request size. `numeric` follows the wall clock. The in-process agent injects size-based tooBig, dropped requests, subtree error statuses, delays, decoy datagrams, restarts, and value changes, on a manual clock.

**`tools/snmpsim/walk2rec.py`**, a portable tool under section 29, converts field-kit walk text into `.snmprec`, so production captures become simulator fixtures.

### Configuration and a recorded contradiction

The SNMP options are one `[snmp]` table (`port`, `timeout`, `retries`, `max_varbinds`, `max_repetitions`, `max_walk_rows`, `rediscovery_interval`, `capability_reprobe_interval`), with defaults and bounds in Appendix E.23.

Blueprint 11.3's reference configuration instead shows `[polling] snmp_timeout`, `snmp_retries`, and `discovery_interval`, and `[poller] per_device_pdu_concurrency`. This ADR follows the M3 task assignment and puts them under `[snmp]`, because the option catalog is the single source of truth (CFGM-001) and the 11.3 example predates it. Per-device PDU concurrency stays fixed at 1 (SNMP-012) and is not an option. If the owner prefers the 11.3 names, a config migration (CFGM-002) renames them, which ADR-031 allows.

## Alternatives

* **`hlapi.v3arch` for v2c too.** Rejected for the MVP. Its engine keeps every community it has seen in the local configuration datastore for the life of the process, unless it is explicitly unconfigured. It is also heavier per request. It remains the likely SNMPv3 transport.
* **A hand-written UDP protocol over `pysnmp.proto`.** This gives full control: strict peer matching, socket errors surfaced synchronously, and precise timeouts. Rejected because SNMP-015 asks for the asyncio PySNMP API. The guards above recover most of the benefit.
* **net-snmp bindings or subprocesses.** Rejected. These are blocking C calls, or a process per request, with the community at risk of reaching an argument vector (SNMP-019).
* **snmpsim only.** Rejected: tooBig, timeouts, and restarts would need real time and could not be made deterministic.
* **The in-process agent only.** Rejected: vakr's client would then be tested only against vakr's own idea of SNMP.

## Rationale

v1arch is the smallest current asyncio PySNMP API that speaks v2c, and it keeps credentials per call rather than in a process-wide datastore. Moving retries, batching, and walks into vakr makes every attempt countable and quality-flaggable (SNMP-017, SNMP-018). It also makes the behaviour unit-testable against a scripted transport, and reusable for SNMPv3. Two simulators give both independence (snmpsim) and determinism (the in-process agent).

## Benefits

* A session's lifetime bounds the community's lifetime in memory, and no known PySNMP path prints it.
* Every failure is explicit and classified, and collector-side failures are never device failures.
* Fault tests are deterministic and fast. The default SNMP test run takes about 15 s, including one snmpsim startup.
* Field captures can be replayed as simulators.

## Risks

* **Private PySNMP and pyasn1 details.** The guards override `SnmpDispatcher._recv_callback` and read `pyasn1.debug._LOG`. Re-verify them, and the canary test, on every PySNMP or pyasn1 upgrade.
* **v1arch matches replies by request ID only.** The peer check narrows this to datagrams from the agent's address and port. An on-path attacker who can spoof that address is outside this mitigation. SNMPv3 `authPriv` is the long-term answer (SNMP-003).
* **PySNMP's timer resolution is 0.1 s**, so a timeout can fire up to 0.1 s late. vakr's own deadline adds a 1 s margin as a backstop.
* **snmpsim depends on `pysmi` without declaring it.** If a later snmpsim declares it, the explicit entry can go. `pysmi` brings `requests`, `jinja2`, and `lark` into development environments only. With `pysmi` installed, importing PySNMP emits two `DeprecationWarning`s. They are harmless.
* **One snmpsim process serves about 110 PDUs/s**, which bounds the load check rather than vakr: per-device poll p95 grows with the simulator's queue.

## Security impact

Communities come only from the credential provider by reference, per session, off the event loop, and are never accepted on the command line. Targets refuse multicast, unspecified, and broadcast addresses and are validated before use (15.5, SNMP-019). DNS lookups are bounded (5 s) and reported separately from device state (9.11). The canary test covers logs, `repr`s, exceptions, tracebacks with locals, decoy datagrams, PySNMP debug mode, and CLI output. CIDR allow and deny policy (15.5) belongs to the inventory layer and is not enforced here.

## Performance impact

Measured on the shared 4 vCPU development host with CPython 3.15.0, using snmpsim 1.2.2 serving the 100-device fleet and a global concurrency of 32 devices:

| Measure | Result |
|---|---|
| Discovery of all 100 devices | 20.1 s, 2,220 PDUs (router 18, switch 44) |
| One Stage 1/Stage 2 cycle of all 100 devices | 8.5 s for 1,600 interfaces (router 5 PDUs, switch 18) |
| Per-device poll duration | p50 1.62 s, p95 5.0 s (snmpsim queueing; under 2 s at concurrency 8); no timeouts |
| Total, discovery plus one poll cycle | 28.6 s, within one 60 s interval |
| PySNMP import | 0.26 s warm, 0.83 s cold; the CLI imports it only when it runs an `snmp` command |

## Compatibility and migration impact

These are new options; no data migration is needed. The options' `since` value reads `0.1.1`, because the catalog test forbids naming a release newer than `VERSION`. The release that bumps `VERSION` must set it to that release, so `vakr config new-options` reports them. A later rename to blueprint 11.3's `[polling]` names needs a config migration.

## Test changes

* `tests/unit/ports/test_snmp_port.py` covers SNMP-T11 and SNMP-019.
* `tests/unit/adapters/snmp/test_snmp_engine.py` covers SNMP-T08, SNMP-T10, SNMP-012, and SNMP-014 against a scripted transport. `test_snmp_codec.py` covers the codec and the guards.
* `tests/unit/discovery/` and `tests/unit/collectors/snmp/` cover parsing, assembly, policy facts, plans, SNMP-T06 aggregation, state classification, and observation builders.
* `tests/simulators/snmp/` contains:
  * the agent scenarios: SNMP-T03, T04, T05, T07, T08, T09, T10, T12, DISC-T04, restart, and discontinuity;
  * the snmpsim scenarios: SNMP-T01, SNMP-T05, the CLI, and an 11-device load check;
  * the canary leak test;
  * the walk2rec-to-snmpsim round trip;
  * the opt-in 100-device load check.
* `tools/snmpsim/test_walk2rec.py` tests the portable converter.

## Documentation changes

* This ADR.
* [`docs/operations/snmp.md`](../operations/snmp.md): operator guide, IOS XE notes, and the OID catalog of blueprint 9.4.
* [`tools/snmpsim/README.md`](../../tools/snmpsim/README.md).
* Appendix E.23.
* The `[snmp]` section of [`docs/operations/configuration.md`](../operations/configuration.md).

## Rollback and revisit criteria

Revisit when any of these holds:

* PySNMP stops supporting the current Python, changes the v1arch API, or moves the overridden internals.
* SNMPv3 work starts. Choose its transport, and decide whether v2c should move to the same API.
* Production field captures show agent behaviour the engine mishandles, such as GETBULK quirks or tooBig on small requests.
* A collector needs many simultaneous sessions to one agent, which would make a shared dispatcher worthwhile.
* The load check or production poll health shows poll p95 approaching the 15 s bound.

Rolling back the library means replacing `transport.py`, `guards.py`, and `factory.py`. The port, engine, discovery, and collectors are unaffected.
