"""Telemetry schema 4 (interface status records): additive, readable by schema 3, kept in place.

``0004_interface_status.sql`` declares ``compat: additive`` and passes the
static additive check, so a release at schema 3 rolled back onto it
still calls the database current (E.14). Its rows stay in their one table
when the mover drains staging, are never written to ``repl_log`` (whose
family CHECK stops at 5), and retention deletes them behind the horizon
(E.35, MIG-001, MIG-002).

Dependencies:
    pytest; tests.contract.contract_samples; vakr.adapters.sqlite.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_interface_status_schema.py
"""

from datetime import timedelta
from importlib.resources import files
from pathlib import Path

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import T0, raw, write
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.adapters.sqlite.migrations.compat import additive_problems
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_family,
    telemetry_migrations,
)
from vakr.adapters.sqlite.telemetry.kept import purge_kept
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.ports.migrations import SchemaState


def test_mig001_0004_is_additive_with_its_compat_header() -> None:
    """E.14: 0004 declares additive and its SQL is additive by the static rules.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    family = telemetry_family()
    (step,) = [m for m in family.migrations if m.version == 4]
    assert step.name == "interface_status"
    assert family.additive(4)
    assert additive_problems(step.sql) == []
    text = files("vakr.adapters.sqlite.telemetry").joinpath("sql/0004_interface_status.sql")
    assert "\n-- compat: additive\n" in text.read_text(encoding="utf-8")


def test_e14_a_schema_3_release_reads_schema_4(tmp_path: Path) -> None:
    """E.14, MIG-002: a schema-3 release's family calls a schema-4 file current and readable.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    new = telemetry_family()
    older = SchemaFamily(new.name, new.application_id, new.migrations[:3], new.compat_from)
    status = SqliteMigrationManager(path, older).status()
    assert (status.state, status.version) == (SchemaState.CURRENT, 4)
    assert SqliteMigrationManager(path, older).migrate() == ()


def test_e35_status_rows_stay_put_unlogged_and_expire(tmp_path: Path) -> None:
    """E.35: the mover leaves status rows in place; the log skips them; retention deletes them.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    store = SqliteTelemetryStore(path)
    backend = SqliteTelemetry(store)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    write(backend, [s.status(dev, iface, col, T0), s.snmp(dev, col, T0)])
    backend.writer.write_batch(new_id(BatchId), [s.status(dev, iface, col, T0 + s.MINUTE)])
    backend.maintain(T0 + timedelta(hours=1))
    assert raw(path, "SELECT count(*) FROM obs_ifstat_hot") == [(2,)]
    assert raw(path, "SELECT count(*) FROM partition_catalog WHERE family = 'obs_ifstat'") == [
        (0,)]  # fmt: skip
    assert raw(path, "SELECT family FROM repl_log") == [(3,)]
    with store.writing() as conn:
        assert purge_kept(conn, None) == 0
        assert purge_kept(conn, int((T0 + timedelta(seconds=30)).timestamp() * 1_000_000)) == 1
    assert raw(path, "SELECT count(*) FROM obs_ifstat_hot") == [(1,)]
    store.close()
