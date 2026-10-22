"""Metadata schema 4 (admin operations) on SQLite: upgrade, storage rules, damaged rows (E.32).

A schema-3 database gains the empty ``operation`` table with its records
intact (MIG-002). The schema itself allows one active operation per device
even to a writer that bypasses the repository, and refuses changes to a
finished operation.

Dependencies:
    pytest, sqlite3; the SQLite metadata adapter; tests.contract samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_operations_schema.py
"""

import sqlite3
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.contract.contract_metadata_operations import operation
from tests.contract.contract_samples import T0
from tests.unit.adapters.sqlite.sqlite_testkit import migrated_store, raw_write
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_family,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.domain.errors import StorageError
from vakr.domain.paging import PageRequest


def test_mig002_schema_3_upgrades_to_operations_with_data(tmp_path: Path) -> None:
    """MIG-002: a schema-3 database keeps its records and gains an empty operation table.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    full = metadata_family()
    old = SchemaFamily(full.name, full.application_id, full.migrations[:3])
    SqliteMigrationManager(db, old).migrate()
    store = SqliteMetadataStore(db, family=old)
    device = s.device()
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.commit()
    store.close()
    applied = metadata_migrations(db).migrate()
    assert [(m.version, m.name) for m in applied][:1] == [(4, "operations")]
    upgraded = SqliteMetadataStore(db)
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.devices.get(device.id) == device
        assert uow.operations.list_page(PageRequest()).total == 0
        uow.operations.add(operation(device.id))
        uow.commit()
    upgraded.close()


def test_e32_the_schema_keeps_one_active_and_finished_rows_final(tmp_path: Path) -> None:
    """E.32: a second active row and a change to a finished row fail in SQL itself.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    device, east = s.device(), s.collector()
    done = operation(device.id).claimed(east.id, T0).failed(T0, "the agent did not answer")
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.collectors.add(east)
        uow.operations.add(operation(device.id, 1))
        uow.operations.add(done)
        uow.commit()
    store.close()
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        raw_write(db, "INSERT INTO operation (id, kind, device_id, state, requested_by, "
                  "requested_at, version) SELECT randomblob(16), kind, device_id, 'running', "
                  "'x', 0, 1 FROM operation WHERE state = 'queued'")  # fmt: skip
    with pytest.raises(sqlite3.IntegrityError, match="never changes"):
        raw_write(db, "UPDATE operation SET error = 'other' WHERE state = 'failed'")


def test_a_damaged_operation_row_is_a_storage_error(tmp_path: Path) -> None:
    """5.2 rule 2: a row that no longer decodes is a StorageError naming no SQL.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    device = s.device()
    queued = operation(device.id)
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.operations.add(queued)
        uow.commit()
    raw_write(db, "UPDATE operation SET started_at = 5")  # queued with a start time
    with sqlite_uow_factory(store)() as uow, pytest.raises(StorageError) as caught:
        uow.operations.get(queued.id)
    store.close()
    assert "SELECT" not in str(caught.value)
