"""Helpers for the SQLite telemetry tests: stores, raw reads, times, and rollup buckets.

The module name is unique on purpose: test helpers are never imported from
a ``conftest``. Raw reads open their own ``sqlite3`` connection so a test
can see exactly what is on disk, behind the adapter's back.

Dependencies:
    sqlite3; vakr.adapters.sqlite.telemetry; tests.contract.contract_samples.

Example:
    >>> at(1, 2).isoformat()
    '2026-10-02T02:00:00+00:00'
"""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.contract import contract_samples as s
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_migrations,
)
from vakr.domain.ids import BatchId, new_id
from vakr.domain.observations import Observation
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric
from vakr.domain.state import RollupBucket
from vakr.ports.telemetry.results import RetentionPolicy, WriteResult

T0 = s.T0
"""2026-10-01T00:00Z, a UTC midnight on the 15-minute grid."""

MINUTE = s.MINUTE
BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def at(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """Return a time relative to ``T0``.

    Args:
        day: Days after ``T0``.
        hour: Hours after that.
        minute: Minutes after that.
        second: Seconds after that.

    Returns:
        datetime: The UTC time.

    Raises:
        None
    """
    return T0 + timedelta(days=day, hours=hour, minutes=minute, seconds=second)


def open_store(tmp_path: Path, policy: RetentionPolicy | None = None) -> SqliteTelemetryStore:
    """Create, migrate, and open a telemetry database in ``tmp_path``.

    Args:
        tmp_path: Directory for ``telemetry.db``.
        policy: Retention policy, or the Compact default.

    Returns:
        SqliteTelemetryStore: The open store.

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    return (
        SqliteTelemetryStore(path) if policy is None else SqliteTelemetryStore(path, policy=policy)
    )


def write(backend: SqliteTelemetry, rows: Sequence[Observation]) -> WriteResult:
    """Write one batch with a fresh batch ID.

    Args:
        backend: The backend.
        rows: Observations.

    Returns:
        WriteResult: The result.

    Raises:
        None
    """
    return backend.writer.write_batch(new_id(BatchId), rows)


def raw(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    """Read rows straight from the file, bypassing the adapter.

    Args:
        path: Database file.
        sql: A query.
        params: Bound values.

    Returns:
        list[tuple[Any, ...]]: The rows.

    Raises:
        sqlite3.Error: If the query fails.
    """
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql, params).fetchall()


def tables_named(path: Path, prefix: str) -> list[str]:
    """List the tables in the file whose names start with ``prefix``.

    Args:
        path: Database file.
        prefix: Name prefix, such as ``obs_iface_d``.

    Returns:
        list[str]: Sorted names, from ``sqlite_schema``.

    Raises:
        sqlite3.Error: If the schema cannot be read.
    """
    rows = raw(path, "SELECT name FROM sqlite_schema WHERE type = 'table'")
    return sorted(r[0] for r in rows if r[0].startswith(prefix))


def bucket(
    subject: UUID, collector: UUID, metric: Metric, start: datetime, base: float, **over: Any
) -> RollupBucket:
    """Build a full 15-minute bucket whose aggregates derive from ``base``.

    Args:
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        start: Aligned bucket start.
        base: Minimum; mean, p95, and maximum are offsets from it.
        **over: Field values to replace.

    Returns:
        RollupBucket: The bucket.

    Raises:
        None
    """
    fields: dict[str, Any] = {
        "subject_id": subject, "collector_id": collector, "metric": metric,
        "bucket_start": start, "bucket_seconds": ROLLUP_BUCKET_SECONDS,
        "expected_count": 15, "sample_count": 15, "valid_count": 15,
        "minimum": base, "mean": base + 7.0, "p95": base + 13.0, "maximum": base + 14.0,
    }  # fmt: skip
    return RollupBucket(**fields | over)
