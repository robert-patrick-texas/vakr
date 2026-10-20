"""ADR-021 benchmark: SQLite telemetry layout for the Compact profile.

The scripts here generate twice the MVP workload (two positions, each storing
its peer's observations), load it into candidate SQLite layouts, and measure
ingest, size, query latency, rollups, retention, and reader/writer
concurrency. The results decide the table layout the telemetry adapter
implements; see ``docs/adr/ADR-021-compact-sqlite-telemetry.md``.

Dependencies:
    sqlite3: The standard-library driver only, so results match what the
        bundled interpreter ships.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_ingest --help
"""
