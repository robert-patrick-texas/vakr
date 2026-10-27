"""Metadata schema 7 (``inactive_since``) on SQLite: backfilled upgrade, fresh install (LIFE-003).

Schema 6 stored only an interface's lifecycle state, and vakr started the
stale clock at ``updated_at``. Migration 0007 adds ``inactive_since`` and
fills it from ``updated_at`` for rows already INACTIVE or STALE, the best
evidence those rows hold; every other row stays ``NULL`` (MIG-002). Rows
reach the schema-6 file through ``ATTACH`` from a current store, so they are
encoded exactly as the adapter writes them, minus the new column.

Dependencies:
    pytest, sqlite3; the SQLite metadata adapter; tests.contract samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_inactive_since_schema.py
"""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import migrated_store, raw_rows
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_family,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.interfaces import Interface
from vakr.domain.timestamps import to_epoch_us

HOUR, DAY = timedelta(hours=1), timedelta(days=1)


def _schema_6(db: Path) -> None:
    """Create a metadata database at schema 6, the last one without ``inactive_since``.

    Args:
        db: The file to create.

    Returns:
        None

    Raises:
        None
    """
    full = metadata_family()
    cut = next(i for i, m in enumerate(full.migrations) if m.name == "interface_inactive_since")
    assert cut == 6
    SqliteMigrationManager(db, SchemaFamily(full.name, full.application_id,
                                            full.migrations[:cut])).migrate()  # fmt: skip


def _copy_into_schema_6(source: Path, target: Path) -> None:
    """Copy devices and interfaces into the schema-6 file, leaving out columns it lacks.

    Args:
        source: A current-schema database with the records.
        target: The schema-6 database.

    Returns:
        None

    Raises:
        None
    """
    with closing(sqlite3.connect(target)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(interface)")]
        assert "inactive_since" not in cols
        conn.execute("ATTACH DATABASE ? AS src", (str(source),))
        names = ", ".join(cols)
        conn.execute("INSERT INTO main.device SELECT * FROM src.device")
        conn.execute(f"INSERT INTO main.interface ({names}) SELECT {names} FROM src.interface")  # noqa: S608
        conn.commit()


def test_mig002_schema_6_upgrades_and_backfills_inactive_since(tmp_path: Path) -> None:
    """MIG-002, LIFE-003: inactive and stale rows start at ``updated_at``; others stay NULL.

    The inactive interface went down at T0+2h but an annotation edit moved
    ``updated_at`` to T0+5d; schema 6 never recorded the earlier time, so the
    backfill can only use T0+5d. That is the choice E.26 records.

    Args:
        tmp_path: Directory for the databases.

    Returns:
        None

    Raises:
        None
    """
    current, db = tmp_path / "current.db", tmp_path / METADATA_FILE
    device = s.device(tags=frozenset())
    wanted: list[tuple[Interface, object]] = []
    for name, state, updated, start in (
        ("Gi1", InterfaceLifecycle.ACTIVE, s.T0 + HOUR, None),
        ("Gi2", InterfaceLifecycle.INACTIVE, s.T0 + 5 * DAY, s.T0 + 2 * HOUR),
        ("Gi3", InterfaceLifecycle.STALE, s.T0 + 12 * DAY, s.T0),
        ("Gi4", InterfaceLifecycle.RETIRED, s.T0 + 3 * HOUR, None),
    ):
        iface = s.interface(device.id, name, lifecycle=state, updated_at=updated,
                            inactive_since=start)  # fmt: skip
        wanted.append((iface, None if start is None else updated))
    store = migrated_store(current)
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        for iface, _ in wanted:
            uow.interfaces.add(iface)
        uow.commit()
    store.close()
    _schema_6(db)
    _copy_into_schema_6(current, db)
    applied = metadata_migrations(db).migrate()
    assert [(m.version, m.name) for m in applied] == [(7, "interface_inactive_since")]
    upgraded = SqliteMetadataStore(db)
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.devices.get(device.id) == device
        for iface, backfilled in wanted:
            assert uow.interfaces.get(iface.id) == replace(iface, inactive_since=backfilled)
    upgraded.close()
    assert raw_rows(db, "SELECT DISTINCT version FROM interface") == [(1,)]  # nothing re-planned


def test_mig001_a_fresh_database_has_a_nullable_inactive_since(tmp_path: Path) -> None:
    """MIG-001, LIFE-003: a fresh install reaches schema 7; the column is a nullable integer.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    device = s.device(tags=frozenset())
    down = s.interface(device.id, lifecycle=InterfaceLifecycle.INACTIVE, inactive_since=s.T0,
                       updated_at=s.T0 + DAY)  # fmt: skip
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.interfaces.add(down)
        uow.commit()
    store.close()
    status = metadata_migrations(db).require_current()
    assert status.version == len(metadata_family().migrations) == 7
    columns = {r[1]: (r[2], r[3]) for r in raw_rows(db, "PRAGMA table_info(interface)")}
    assert columns["inactive_since"] == ("INTEGER", 0)
    stamp = raw_rows(db, "SELECT inactive_since, updated_at FROM interface")
    assert stamp == [(to_epoch_us(s.T0), to_epoch_us(s.T0 + DAY))]  # UTC microseconds
    migration = metadata_family().migrations[6]
    assert (migration.destructive, migration.transactional) == (False, True)
    assert "LIFE-003" in migration.description
    assert "MIG-002" in migration.test_impact
