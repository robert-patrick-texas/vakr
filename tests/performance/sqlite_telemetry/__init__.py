"""Measured check of the real SQLite telemetry adapter against ADR-021 (M2 exit).

``bench_adapter`` loads full days of the MVP workload through the real
``write_batch``, ``maintain``, and ``RollupRepository.write`` paths and
reports ingest rate, graph latency, and bytes per row. It is a ``python -m``
script, not a test, so ``make check`` never runs it; ``tele_workload`` is
its deterministic generator. Nothing here is imported from ``src/``.

Dependencies:
    vakr.adapters.sqlite.telemetry; vakr.domain; vakr.logic.stats.

Example:
    $ uv run python -m tests.performance.sqlite_telemetry.bench_adapter --days 1
"""
