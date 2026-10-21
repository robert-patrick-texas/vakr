"""``telemetry_db`` self-health check: schema, WAL size, mover lag, and integrity (OPS-001, E.21).

Every test relocates the installation with ``VAKR_ROOT``. Thresholds are
the E.21 defaults; the WAL test lowers the threshold instead of writing
256 MiB.

Dependencies:
    pytest, sqlite3; vakr.adapters.sqlite.telemetry.health; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_health.py
"""

import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import synthetic
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import write
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.adapters.sqlite.telemetry import (
    SqliteTelemetry,
    SqliteTelemetryStore,
    health,
    telemetry_family,
    telemetry_migrations,
)
from vakr.adapters.sqlite.telemetry.health import check_telemetry_db
from vakr.domain.ids import CollectorId, new_id
from vakr.health.model import Status
from vakr.health.registry import CHECKS


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the installation and create its state directory.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: The state directory.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    state_dir = tmp_path / "var/lib/vakr"
    state_dir.mkdir(parents=True)
    return state_dir


def test_check_is_registered_and_skips_hosts_without_vakr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPS-001: the check runs in ``vakr check health``; a non-node host is OK.

    Args:
        tmp_path: Install root without a state directory.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    assert check_telemetry_db in CHECKS
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    assert check_telemetry_db().status is Status.OK


def test_fresh_install_warns_then_current_is_ok_with_perfdata(state: Path) -> None:
    """MIG-001: WARNING before migrating; OK with schema, WAL, and lag perfdata after.

    Args:
        state: State directory.

    Returns:
        None

    Raises:
        None
    """
    assert check_telemetry_db().status is Status.WARNING
    telemetry_migrations(state / "telemetry.db").migrate()
    result = check_telemetry_db()
    assert result.status is Status.OK, result.summary
    assert "mover lag 0 s" in result.summary
    labels = {p.label for p in result.perfdata}
    assert labels == {"telemetry_schema", "telemetry_wal_bytes", "telemetry_mover_lag"}


def test_mover_lag_and_wal_size_warn(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E.21: staged rows older than 30 minutes, or a WAL over its threshold, warn.

    Args:
        state: State directory.
        monkeypatch: Lowers the WAL threshold.

    Returns:
        None

    Raises:
        None
    """
    path = state / "telemetry.db"
    telemetry_migrations(path).migrate()
    store = SqliteTelemetryStore(path)
    now = datetime.fromtimestamp(int(time.time()), UTC)
    old = now - timedelta(minutes=45)
    write(SqliteTelemetry(store), [s.health(new_id(CollectorId), old)])
    lagging = check_telemetry_db()
    assert lagging.status is Status.WARNING
    assert "staged rows older than 30 min" in lagging.summary
    SqliteTelemetry(store).maintain(now)
    assert check_telemetry_db().status is Status.OK
    with closing(sqlite3.connect(path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM vocab_code").fetchone()  # pins the WAL
        write(SqliteTelemetry(store), [s.health(new_id(CollectorId), now)])
        monkeypatch.setattr(health, "WAL_WARN_BYTES", 1)
        assert "WAL above 0 MiB" in check_telemetry_db().summary
    store.close()


def test_newer_schema_and_damage_are_critical(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MIG-004, OPS-001: a newer schema, a foreign file, or a failed check is CRITICAL.

    Args:
        state: State directory.
        monkeypatch: Injects an integrity failure.

    Returns:
        None

    Raises:
        None
    """
    path = state / "telemetry.db"
    base = telemetry_family()
    probe = synthetic(len(base.migrations) + 1, "probe", "CREATE TABLE probe (x INT);")
    later = SchemaFamily(base.name, base.application_id, (*base.migrations, probe))
    SqliteMigrationManager(path, later).migrate()
    assert check_telemetry_db().status is Status.CRITICAL
    path.unlink()
    path.write_bytes(b"not a database" * 100)
    assert check_telemetry_db().status is Status.CRITICAL
    path.unlink()
    telemetry_migrations(path).migrate()
    monkeypatch.setattr(health, "_check", lambda _conn, table: [f"{table}: bad page"])
    result = check_telemetry_db()
    assert result.status is Status.CRITICAL
    assert "fails PRAGMA quick_check" in result.summary
