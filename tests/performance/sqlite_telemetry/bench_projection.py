r"""Build and measure the ranking projection on a loaded MVP database (E.28, 12.9).

``bench_history --project`` calls ``build_projection`` before it times the
rankings: it migrates the file to the current schema (0002 adds the
projection), forgets any projection, and projects every bucket of the last
25 hours that ends at least ``tail`` minutes before the clock, newest first,
exactly as the collector's projector does, timing each bucket. The
unprojected tail is then scanned natively by every ranking, as in
production between two projector passes; 45 minutes is the worst case at
the 15-minute cadence (10 minutes of settling, the bucket in progress, and
the time to the next pass). It also reports the projection's disk use from
``dbstat`` and what the writer's invalidation adds to one batch.

Dependencies:
    sqlite3, statistics, time: Size and timing.
    vakr.adapters.sqlite.telemetry: Migrations, the store, and the projection.

Safety:
    Writes only the given benchmark database.

Example:
    $ uv run python -m tests.performance.sqlite_telemetry.bench_history --reuse --project
"""

import sqlite3
import statistics
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    telemetry_migrations,
)
from vakr.adapters.sqlite.telemetry.ranking_projection import (
    SqliteRankingProjection,
    forget_buckets,
)
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.logic.stats import p95
from vakr.ports.telemetry.ranking_projection import PROJECTION_KEEP


def _sizes(path: Path) -> dict[str, int]:
    """Return the bytes the projection's tables and key index occupy, and its row counts.

    Args:
        path: The database file.

    Returns:
        dict[str, int]: ``table``, ``index``, and ``manifest`` bytes; ``buckets``
        and ``hours`` rows; ``payload`` bytes.

    Raises:
        sqlite3.Error: If ``dbstat`` cannot be read.
    """
    names = {"rank_iface": "table", "sqlite_autoindex_rank_iface_1": "index",
             "rank_unit": "manifest"}  # fmt: skip
    with closing(sqlite3.connect(path)) as conn:
        pages = conn.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name").fetchall()
        widths = conn.execute("SELECT width_s, count(*) FROM rank_iface GROUP BY width_s")
        rows = {int(w): int(n) for w, n in widths}
        payload = conn.execute("SELECT sum(length(payload)) FROM rank_iface").fetchone()[0]
    sizes = {names[str(n)]: int(b) for n, b in pages if str(n) in names}
    return {"table": 0, "index": 0, "manifest": 0, **sizes, "buckets": rows.get(900, 0),
            "hours": rows.get(3600, 0), "payload": payload or 0}  # fmt: skip


def _invalidation_us(store: SqliteTelemetryStore, clock: datetime) -> float:
    """Time the writer's per-batch invalidation statement (two buckets, as a batch has).

    Args:
        store: The open store.
        clock: The benchmark's now.

    Returns:
        float: Microseconds per batch, inside an open write transaction.

    Raises:
        StorageError: If the database is unavailable.
    """
    times = [to_epoch_us(clock) - 60_000_000, to_epoch_us(clock)]
    with store.writing() as conn:
        started = time.perf_counter()
        for _ in range(1000):
            forget_buckets(conn, times)
        elapsed = time.perf_counter() - started
    return elapsed * 1000


def build_projection(directory: Path, clock: datetime, tail: timedelta) -> str:
    """Migrate, rebuild the projection behind ``clock - tail``, and describe the cost.

    Args:
        directory: Holds ``telemetry.db``.
        clock: The benchmark's now.
        tail: How much of the newest data stays unprojected.

    Returns:
        str: Markdown lines with per-bucket times, the rebuild, and sizes.

    Raises:
        StorageError: If the database is unavailable.
    """
    path = directory / TELEMETRY_FILE
    started = time.perf_counter()
    applied = telemetry_migrations(path).migrate()
    migrated = time.perf_counter() - started
    store = SqliteTelemetryStore(path)
    projection = SqliteRankingProjection(store)
    projection.forget()
    end = clock - tail
    keep = clock - PROJECTION_KEEP
    todo = projection.unprojected(TimeWindow(keep, end)) if end > keep else ()
    took, partials = [0.0], 0
    for bucket in todo:
        started = time.perf_counter()
        partials += projection.project(bucket)
        took.append(time.perf_counter() - started)
    took = took[1:] or took
    invalidation = _invalidation_us(store, clock)
    store.close()
    sizes = _sizes(path)
    return "\n".join((
        f"- migration: {[a.name for a in applied]} in {migrated * 1000:.0f} ms",
        f"- projected {len(todo)} buckets ({partials} partials) ending by {end.isoformat()}: "
        f"{sum(took):.1f} s; per bucket mean {statistics.mean(took) * 1000:.0f} ms, "
        f"p95 {p95(took) * 1000:.0f} ms, max {max(took) * 1000:.0f} ms",
        f"- projection size: rank_iface {sizes['table'] / 2**20:.1f} MiB and its key index "
        f"{sizes['index'] / 2**20:.1f} MiB ({sizes['buckets']} bucket and {sizes['hours']} "
        f"hour partials, {sizes['payload'] / 2**20:.1f} MiB of payload); rank_unit "
        f"{sizes['manifest'] / 2**10:.0f} KiB",
        f"- writer invalidation: {invalidation:.1f} us per batch",
    ))  # fmt: skip
