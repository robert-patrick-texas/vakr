"""Nothing SQLite-shaped leaves the adapter: no SQL text, no driver types (5.2 rules 1-2, DB-007).

Covers ARCH-001 and 5.2 (driver types and SQL stay in adapter modules),
DB-007 (read models carry UUIDs, never keys), and the error rule of
``storage.md``: storage failures surface as domain errors whose messages
hold no SQL, table names, or driver text, with the driver exception
dropped from the traceback. Failures are provoked behind the adapter's
back: a busy write lock, a dropped partition, a damaged payload.

Dependencies:
    pytest, ast, sqlite3; vakr.adapters.sqlite.telemetry; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_boundaries.py
"""

import ast
import re
import sqlite3
from contextlib import closing
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import (
    T0,
    at,
    bucket,
    open_store,
    write,
)
from vakr.adapters.sqlite.connection import SqliteSettings
from vakr.adapters.sqlite.telemetry import SqliteTelemetry, SqliteTelemetryStore
from vakr.domain.enums import ObservationType
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.paging import PageRequest
from vakr.domain.series import Metric, TimeWindow

SRC = Path(__file__).resolve().parents[5] / "src" / "vakr"
SQLISH = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|CREATE|DROP|PRAGMA)\b|obs_|roll_")
DAY = TimeWindow(T0, at(1))


def _no_driver_objects(value: object, path: str = "result") -> None:
    """Assert a result holds no ``sqlite3`` objects, walking dataclasses and tuples.

    Args:
        value: A port result.
        path: Where it sits, for the failure message.

    Returns:
        None

    Raises:
        AssertionError: If a driver object is found.
    """
    assert type(value).__module__ != "sqlite3", f"{path} is a driver object"
    if isinstance(value, tuple | list):
        for i, item in enumerate(value):
            _no_driver_objects(item, f"{path}[{i}]")
    elif is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            _no_driver_objects(getattr(value, f.name), f"{path}.{f.name}")


def _assert_clean(err: StorageError) -> None:
    """Assert a storage error names no SQL and hides the driver exception.

    Args:
        err: The error raised by the adapter.

    Returns:
        None

    Raises:
        AssertionError: If SQL text or the driver exception leaks.
    """
    assert SQLISH.search(str(err)) is None, str(err)
    assert err.__cause__ is None
    assert err.__suppress_context__


def test_arch001_only_adapters_import_the_driver() -> None:
    """ARCH-001, 5.2 rule 1: no module outside ``vakr.adapters`` imports ``sqlite3``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        # The portable self-update tool (blueprint 29) copies database files with the
        # online backup API and never reads vakr's schema; it is not a vakr layer.
        if {"adapters", "lifecycle"} & set(path.relative_to(SRC).parts):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )  # fmt: skip
            offenders += [f"{path.relative_to(SRC)}: {n}" for n in names if n == "sqlite3"]
    assert offenders == []


def test_db007_results_are_domain_types_only(tmp_path: Path) -> None:
    """5.2 rule 2, DB-007: every port result is built from domain types.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    write(backend, [s.sample(dev, iface, col, T0), s.icmp(dev, col, T0), s.health(col, T0)])
    backend.maintain(T0 + s.MINUTE * 3)
    backend.rollups.write([bucket(iface, col, Metric.IN_BPS, T0, 1.0)])
    for result in (
        backend.query.read_raw(ObservationType.INTERFACE, iface, DAY, PageRequest()),
        backend.query.interface_series(iface, col, Metric.IN_BPS, DAY),
        backend.query.downsampled_series(iface, col, Metric.IN_BPS, DAY),
        backend.current.device_states(col, PageRequest(), now=T0),
        backend.current.interface_states(DeviceId(dev), col, PageRequest(), now=T0),
        backend.collector_health.latest(col),
        backend.rollups.read(iface, col, Metric.IN_BPS, DAY, limit=5),
        backend.retention.apply(at(2)),
        backend.maintain(at(2)),
    ):
        _no_driver_objects(result)
        s.assert_no_row_ids(result)
    store.close()


def test_busy_database_is_a_retryable_error_without_sql(tmp_path: Path) -> None:
    """5.3, DB-008: another process holding the write lock gives a clean, retryable error.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    first = open_store(tmp_path)
    path = first.path
    first.close()
    store = SqliteTelemetryStore(path, SqliteSettings(busy_timeout_ms=50, lock_wait_ms=100))
    backend = SqliteTelemetry(store)
    with closing(sqlite3.connect(path, autocommit=True)) as other:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(StorageError) as caught:
            write(backend, [s.icmp(s.device().id, new_id(CollectorId), T0)])
        other.execute("ROLLBACK")
    store.close()
    assert caught.value.retryable
    _assert_clean(caught.value)


def test_damaged_storage_is_reported_without_sql(tmp_path: Path) -> None:
    """storage.md: a partition dropped behind vakr's back or a damaged payload is a StorageError.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    write(backend, [s.sample(dev, iface, col, T0)])
    backend.maintain(T0 + 3 * s.MINUTE)
    backend.rollups.write([bucket(iface, col, Metric.IN_BPS, T0, 1.0)])
    with closing(sqlite3.connect(store.path, autocommit=True)) as conn:
        conn.execute("DROP TABLE obs_iface_d20261001")
        conn.execute("UPDATE roll_iface_m202610 SET payload = x'0700'")
    with pytest.raises(StorageError) as dropped:
        backend.query.read_raw(ObservationType.INTERFACE, iface, DAY, PageRequest())
    _assert_clean(dropped.value)
    with pytest.raises(StorageError) as damaged:
        backend.rollups.read(iface, col, Metric.IN_BPS, DAY, limit=5)
    assert SQLISH.search(str(damaged.value)) is None
    store.close()
