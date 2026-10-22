"""Metadata schema 3 (local authentication) on SQLite: upgrade, storage rules, no secrets.

A schema-2 database from the 0.2.x releases gains the authentication
tables with its records intact (MIG-002). The schema itself keeps users
undeletable (META-009) and stores tokens only as 32-byte digests (SEC-010).

Dependencies:
    pytest, sqlite3; the SQLite metadata adapter; tests.contract samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_auth_schema.py
"""

import sqlite3
from pathlib import Path

import pytest

from tests.contract import contract_auth_samples as a
from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import migrated_store, raw_rows, raw_write
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


def test_mig002_schema_2_upgrades_to_local_auth_with_data(tmp_path: Path) -> None:
    """MIG-002: a schema-2 database keeps its records and gains empty auth tables.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    full = metadata_family()
    old = SchemaFamily(full.name, full.application_id, full.migrations[:2])
    SqliteMigrationManager(db, old).migrate()
    store = SqliteMetadataStore(db, family=old)
    device = s.device()
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.commit()
    store.close()
    applied = metadata_migrations(db).migrate()
    assert [(m.version, m.name) for m in applied][:1] == [(3, "local_auth")]
    upgraded = SqliteMetadataStore(db)
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.devices.get(device.id) == device
        assert uow.users.list_page(PageRequest()).total == 0
        assert uow.change_counter() == 2  # unchanged: auth tables are not collection metadata
        uow.users.add(a.user())
        uow.commit()
    upgraded.close()


def test_users_are_never_deleted_and_tokens_are_digests(tmp_path: Path) -> None:
    """META-009, SEC-010: the schema refuses user deletes and non-digest token columns.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    alice = a.user()
    with sqlite_uow_factory(store)() as uow:
        uow.users.add(alice)
        uow.sessions.add(a.session(alice.id))
        uow.commit()
    store.close()
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        raw_write(db, "DELETE FROM local_user")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        raw_write(db, "UPDATE auth_session SET token_hash = X'00'")
    assert raw_rows(db, "SELECT length(token_hash) FROM auth_session") == [(32,)]


def test_a_damaged_user_row_is_reported_without_its_contents(tmp_path: Path) -> None:
    """5.2 rule 2: a row that no longer decodes is a StorageError naming no hash or SQL.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    alice = a.user()
    with sqlite_uow_factory(store)() as uow:
        uow.users.add(alice)
        uow.commit()
    raw_write(db, "UPDATE local_user SET password_hash = 'not-a-phc-string', role = 'root'")
    with sqlite_uow_factory(store)() as uow, pytest.raises(StorageError) as caught:
        uow.users.get(alice.id)
    store.close()
    assert "not-a-phc-string" not in str(caught.value)
    assert "SELECT" not in str(caught.value)
