"""Measurement rules: pure functions that turn raw evidence into operator-visible state.

These rules decide whether an operator sees a real outage or a false alarm,
and whether a router reload shows up as a gap or as a bandwidth spike. They
are kept apart from collection, storage, and presentation so each one can be
tested exhaustively and reused unchanged by the collector, the rollups, the
API, the web UI, and the TUI.

Modules:
    stats: Exact half-to-even summaries and the shared nearest-rank p95 (E.7, RET-004).
    icmp: Probe-group summary, reachability state, SNMP gating (9.3, E.1).
    uptime: ``sysUpTime`` continuity across a gap (E.5, UPTIME-001..005).
    capacity: Utilization denominator and line rate (RATE-008..010).
    counters, baseline, rates: Counter samples, baseline validation, and
        rates (9.7, E.4, RATE-001..010).
    freshness: When an observation stops counting as current (E.3).
    status_table, status: Device status rows, composite across positions,
        and device-list order (E.2, 7.7, MPOS-007).
    lifecycle_model, lifecycle: Interface lifecycle transitions (E.6, LIFE-*).
    ranking, ranking_errors: Top-utilization and top-error rankings (DASH-003..005).
    natural: Natural name order (``ge-0/0/2`` before ``ge-0/0/10``).

Inputs are small frozen dataclasses defined here, not the domain observation
types, so the rules stay independent of how observations are stored. Only
``vakr.domain.enums`` and ``vakr.domain.quality`` are shared with the domain.

Dependencies:
    Standard library only, plus ``vakr.domain.enums`` and ``vakr.domain.quality``.

Safety:
    Every function is pure: no I/O, no clock reads (callers pass ``now`` and
    elapsed times in), no logging, no randomness. Missing evidence becomes
    ``None`` or ``UNKNOWN``, never zero (blueprint 4.3).

Example:
    >>> from vakr.logic.icmp import ProbeGroup, summarise_probes
    >>> summarise_probes(ProbeGroup((900, 1000, 1100, 1000, 1000), sent=5)).state.name
    'REACHABLE'
"""
