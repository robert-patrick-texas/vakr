"""Ports: the typed interfaces between vakr's logic and the outside world (5.2).

Every database, clock, and (later) network library sits behind a
``Protocol`` defined here. Scheduler, collection, API, web, and TUI code
depend only on these protocols and on ``vakr.domain``; adapters under
``vakr.adapters`` implement them. Swapping SQLite for PostgreSQL or
ClickHouse therefore changes which adapter is constructed, not the code
that uses it, and every adapter proves it behaves the same by passing the
shared contract suites in ``tests/contract`` (rule 6).

- ``vakr.ports.metadata``: inventory repositories and their unit of work.
- ``vakr.ports.telemetry``: observation writes, queries, current state,
  rollups, collector health, retention, and backend capabilities.
- ``vakr.ports.clock``: wall-clock and monotonic time.

Dependencies:
    vakr.domain only. Ports never import a database driver, network
    library, web framework, or ``vakr.adapters`` (ARCH-001).

Safety:
    Ports raise ``vakr.domain.errors`` exceptions, never driver exceptions,
    so no backend detail or SQL text leaks into API errors.

Example:
    >>> from vakr.ports.clock import ManualClock
    >>> from datetime import UTC, datetime
    >>> ManualClock(datetime(2026, 10, 20, tzinfo=UTC)).now_utc().year
    2026
"""
