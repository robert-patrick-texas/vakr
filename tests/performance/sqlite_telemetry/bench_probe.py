"""Measurements for ``bench_adapter``: sizes from ``dbstat``, query latency, and projections.

Sizes are read with SQLite's ``dbstat`` virtual table, so bytes per row
include keys, page headers, and free space, like the ADR-021 report.
Latency is wall-clock time in this process through the adapter's public
API, including building domain objects. Warm runs repeat a query on an
open store; cold runs evict the file from the page cache with
``posix_fadvise(DONTNEED)`` and open a fresh store first.

Dependencies:
    sqlite3, os, statistics, time: Direct file inspection and timing.
    vakr.adapters.sqlite.telemetry: The adapter under test.

Example:
    >>> family_of("obs_iface_d20260901"), family_of("roll_icmp_m202609"), family_of("x")
    ('obs_iface', 'roll_icmp', None)
"""

import os
import re
import sqlite3
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from vakr.adapters.sqlite.telemetry import SqliteTelemetry, SqliteTelemetryStore

_PARTITION = re.compile(r"((?:obs|roll)_[a-z]+)_(?:d[0-9]{8}|m[0-9]{6})")
NATIVE_DAYS_WORST = 31.04
"""Native days on disk just before a drop: 30 retained, today, and the day awaiting it."""

ROLLUP_DAYS_WORST = 431
"""Rollup days on disk just before a month drop (E.10, blueprint 27.3)."""


def family_of(table: str) -> str | None:
    """Return the partition family of a table name.

    Args:
        table: A table name.

    Returns:
        str | None: ``obs_iface``, ``roll_icmp``, ...; ``None`` if not a partition.

    Raises:
        None
    """
    match = _PARTITION.fullmatch(table)
    return None if match is None else match.group(1)


def sizes(path: Path) -> dict[str, dict[str, float]]:
    """Measure pages, bytes, rows, bytes per row, and page fill per partition family.

    Args:
        path: The telemetry database.

    Returns:
        dict[str, dict[str, float]]: Family to its measurements, plus ``file``.

    Raises:
        sqlite3.Error: If the file cannot be read.
    """
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with closing(sqlite3.connect(path)) as conn:
        for name, size, used in conn.execute(
            "SELECT name, sum(pgsize), sum(pgsize - unused) FROM dbstat GROUP BY name"
        ):
            family = family_of(name)
            if family is not None:
                out[family]["bytes"] += size
                out[family]["used"] += used
                out[family]["rows"] += conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]  # noqa: S608  # names from dbstat
        buckets = conn.execute(
            "SELECT family, sum(bucket_count) FROM partition_catalog "
            "WHERE family LIKE 'roll%' GROUP BY family"
        ).fetchall()
    for family, count in buckets:
        out[family]["metric_buckets"] = float(count)
    for stats in out.values():
        if stats.get("rows"):
            stats["bytes_per_row"] = stats["bytes"] / stats["rows"]
            stats["fill"] = stats["used"] / stats["bytes"]
        if stats.get("metric_buckets"):
            stats["bytes_per_metric_bucket"] = stats["bytes"] / stats["metric_buckets"]
    out["file"]["bytes"] = float(path.stat().st_size)
    return {k: dict(v) for k, v in out.items()}


def evict(path: Path) -> None:
    """Drop the database and its WAL from the page cache.

    Args:
        path: The telemetry database.

    Returns:
        None

    Raises:
        OSError: If a file cannot be opened.
    """
    for file in (path, path.with_name(f"{path.name}-wal")):
        if file.exists():
            fd = os.open(file, os.O_RDONLY)
            try:
                os.fsync(fd)
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)


def timed(
    path: Path, query: Callable[[SqliteTelemetry], object], warm: int = 20, cold: int = 3
) -> dict[str, float]:
    """Time a query warm and cold.

    Args:
        path: The telemetry database.
        query: Runs one query on a backend.
        warm: Timed warm repetitions after one untimed run.
        cold: Cold repetitions, each on a fresh store after eviction.

    Returns:
        dict[str, float]: ``warm_p50``, ``warm_p95``, ``cold_p50``, ``cold_max`` in ms.

    Raises:
        None
    """
    store = SqliteTelemetryStore(path)
    backend = SqliteTelemetry(store)
    query(backend)
    runs = []
    for _ in range(warm):
        t = time.perf_counter()
        query(backend)
        runs.append((time.perf_counter() - t) * 1000)
    store.close()
    colds = []
    for _ in range(cold):
        evict(path)
        fresh = SqliteTelemetryStore(path)
        t = time.perf_counter()
        query(SqliteTelemetry(fresh))
        colds.append((time.perf_counter() - t) * 1000)
        fresh.close()
    runs.sort()
    return {
        "warm_p50": statistics.median(runs),
        "warm_p95": runs[int(0.95 * (len(runs) - 1))],
        "cold_p50": statistics.median(colds),
        "cold_max": max(colds),
    }


def project(measured: dict[str, dict[str, float]], days: float) -> dict[str, float]:
    """Project the per-node worst case from the loaded days (blueprint 27.3).

    Args:
        measured: Output of ``sizes`` after loading ``days`` full days.
        days: Days loaded.

    Returns:
        dict[str, float]: Native and rollup bytes per day and the worst-case total, in GB.

    Raises:
        None
    """
    native = sum(v["bytes"] for k, v in measured.items() if k.startswith("obs_")) / days
    rollup = sum(v["bytes"] for k, v in measured.items() if k.startswith("roll_")) / days
    fixed = 64 * 2**20 + 20 * 2**20  # WAL after a truncating checkpoint, staging, dictionaries
    worst = NATIVE_DAYS_WORST * native + ROLLUP_DAYS_WORST * rollup + fixed
    return {
        "native_mb_per_day": native / 1e6,
        "rollup_mb_per_day": rollup / 1e6,
        "native_gb_worst": NATIVE_DAYS_WORST * native / 1e9,
        "rollup_gb_worst": ROLLUP_DAYS_WORST * rollup / 1e9,
        "total_gb_worst": worst / 1e9,
    }
