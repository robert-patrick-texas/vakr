"""The scheduler: when each device is polled, and what every occurrence becomes (8.11, 9.2, 9.9).

An MRTG-style poller runs once a minute and hopes it finishes; vakr plans
every device's poll on an anchored, monotonic schedule, never starts a
second poll of a device that is still being polled, and records every
scheduled occurrence, including the ones it could not run, so an operator
can tell a slow collector from a broken router (invariants 2 and 10).

Modules:
    hashing, phase: E.11 SHA-256 phase and order (SCHED-001..008, ADR-007).
    targets: Devices plus resolved interval and strategy.
    timeline: Monotonic plan time with UTC labels (SCHED-011, SCHED-018).
    plan: The due-time priority queue and re-planning (SCHED-010, SCHED-013).
    admission: Bounded workers, no overlap, recorded misses (SCHED-012..017).
    machine: The poll state machine as a transition table (9.2, ADR-005, ADR-017).
    steps: The async ICMP and SNMP step protocols the collectors implement.
    runner: Runs one poll through the machine, keeping partial evidence.
    records: ``DevicePoll`` records for finished and missed occurrences.
    outbox: The bounded, backpressure-reporting output queue (6.4).
    stats, lag, health: Counters, percentiles, event-loop lag (OBS-001..007).
    settings: ``[scheduler]`` and ``[polling]`` options (Appendix E.25).
    dispatch, driver: The asyncio dispatcher and the ``Scheduler`` facade.

Dependencies:
    vakr.domain: Poll records, IDs, quality flags.
    vakr.logic.icmp: The ICMP gate (``snmp_plan``).
    vakr.ports.clock: Monotonic and UTC time.

Safety:
    No protocol adapter is imported here: steps arrive through protocols.
    Every queue and pool is bounded, and nothing blocks the event loop.

Example:
    >>> from vakr.scheduler.phase import PhaseRule
    >>> PhaseRule("vakr-east").phase.value
    'hostname'
"""
