"""Migration framework on the metadata database: MIG-001, MIG-002, MIG-004, MIG-005.

Later releases are simulated with synthetic migrations appended to this
release's family (``sqlite_testkit.family_with``), so upgrade, refusal, and
destructive-gate paths are proven before a real second migration exists.

Dependencies:
    pytest; vakr.adapters.sqlite; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_migrations.py
"""

from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import (
    CURRENT,
    NEXT,
    family_with,
    manager,
    migrated_store,
    raw_rows,
    raw_write,
    synthetic,
)
from vakr.adapters.sqlite.metadata import (
    METADATA_APPLICATION_ID,
    SqliteMetadataStore,
    metadata_family,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.migrations import verify_backup
from vakr.domain.paging import PageRequest
from vakr.ports.migrations import (
    DestructiveMigrationError,
    MigrationError,
    SchemaState,
    SchemaTooNewError,
)

ADD_NOTES = synthetic(
    NEXT, "add_device_notes", "ALTER TABLE device ADD COLUMN notes TEXT NOT NULL DEFAULT '';"
)
ADD_TABLE = synthetic(
    NEXT + 1, "add_note_table", "CREATE TABLE device_note (id BLOB PRIMARY KEY) STRICT;"
)
DROP_INDEX = synthetic(NEXT, "drop_site_index", "DROP INDEX device_by_site;", destructive=True)
SHIPPED = list(range(1, CURRENT + 1))  # versions this release's own migrations produce


def test_mig001_fresh_install_reaches_current_schema(tmp_path: Path) -> None:
    """MIG-001: a missing database is created, journalled, stamped, and usable.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    mgr = manager(path)
    before = mgr.status()
    assert (before.state, before.version, before.can_migrate) == (SchemaState.MISSING, 0, True)
    assert [m.version for m in before.pending] == SHIPPED
    assert "fresh install" in before.detail
    assert not path.exists()  # status never creates the file
    applied = mgr.migrate()
    assert (applied[0].version, applied[0].name, applied[0].applied_by) == (
        1, "initial", "vakr test")  # fmt: skip
    assert [a.version for a in applied] == SHIPPED
    after = mgr.require_current()
    assert (after.state, after.version, after.pending) == (SchemaState.CURRENT, CURRENT, ())
    assert after.supported == metadata_family().supported
    assert [a.version for a in after.applied] == SHIPPED
    assert raw_rows(path, "PRAGMA application_id") == [(METADATA_APPLICATION_ID,)]
    assert raw_rows(path, "PRAGMA journal_mode") == [("wal",)]
    assert mgr.migrate() == ()  # already current: nothing to do
    assert mgr.check_integrity().ok


def test_mig001_empty_file_is_pending_and_migrates(tmp_path: Path) -> None:
    """MIG-001: a zero-byte file counts as an empty database, not a foreign one.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    path.touch()
    status = manager(path).status()
    assert (status.state, status.version) == (SchemaState.PENDING, 0)
    assert "empty" in status.detail
    assert [m.version for m in manager(path).migrate()] == SHIPPED


def test_db007_schema_has_no_row_ids(tmp_path: Path) -> None:
    """DB-007: every metadata table is WITHOUT ROWID, so no row number can leak.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    migrated_store(tmp_path / "metadata.db").close()
    tables = raw_rows(
        tmp_path / "metadata.db",
        "SELECT name, sql FROM sqlite_schema WHERE type = 'table' AND name <> 'schema_migrations'",
    )
    # 10 record tables (identity_review since 0002), 4 membership, 4 authentication (0003),
    # 1 admin operation table (0004), 1 dashboard table (0005)
    assert len(tables) == 20
    assert all("WITHOUT ROWID" in str(sql) for _, sql in tables)


def test_mig002_upgrade_preserves_ids_and_data(tmp_path: Path) -> None:
    """MIG-002: current-schema data survives two later upgrades with IDs intact and durations.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    store = migrated_store(path)
    dev = s.device(tags=frozenset())
    iface = s.interface(dev.id)
    entry, event = s.history(iface, s.T0), s.audit(s.T0, object_id=dev.id)
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(dev)
        uow.interfaces.add(iface)
        uow.interfaces.append_identity_history(entry)
        uow.audit.append(event)
        uow.commit()
    store.close()
    later = family_with(ADD_NOTES, ADD_TABLE)
    applied = manager(path, later).migrate()
    assert [(a.version, a.name) for a in applied] == [
        (NEXT, "add_device_notes"),
        (NEXT + 1, "add_note_table"),
    ]
    assert all(a.duration.total_seconds() >= 0 for a in applied)
    status = manager(path, later).require_current()
    assert [a.version for a in status.applied] == [*SHIPPED, NEXT, NEXT + 1]
    upgraded = SqliteMetadataStore(path, family=later)
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.devices.get(dev.id) == dev
        assert uow.interfaces.get(iface.id) == iface
        assert uow.interfaces.identity_history(iface.id, PageRequest()).items == (entry,)
        assert uow.audit.list_page(PageRequest()).items == (event,)
    upgraded.close()
    assert raw_rows(path, "SELECT notes FROM device") == [("",)]


def test_mig004_older_release_refuses_newer_schema(tmp_path: Path) -> None:
    """MIG-004: after an upgrade past this release's schema, it refuses with an actionable message.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path, family_with(ADD_NOTES)).migrate()
    status = manager(path).status()
    assert (status.state, status.version, status.pending) == (SchemaState.TOO_NEW, NEXT, ())
    assert not status.can_migrate
    assert "newer vakr release" in status.detail
    assert f"supports up to {CURRENT}" in status.detail
    with pytest.raises(SchemaTooNewError) as caught:
        SqliteMetadataStore(path)
    assert "restore the backup" in str(caught.value)
    with pytest.raises(SchemaTooNewError):
        manager(path).migrate()


def test_mig004_newer_release_crash_still_counts_as_newer(tmp_path: Path) -> None:
    """MIG-004: a newer release's unfinished migration also makes the schema too new.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    marker = (NEXT, "future", "sha256:0", "in_progress", 0, None, "vakr 9.9.9")
    raw_write(path, "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?, ?, ?)", marker)
    status = manager(path).status()
    assert (status.state, status.version) == (SchemaState.TOO_NEW, CURRENT)


def test_mig005_destructive_step_needs_flag_and_verified_backup(tmp_path: Path) -> None:
    """MIG-005: a destructive migration runs only with the flag and a verified backup.

    Args:
        tmp_path: Directory for the database and backups.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    later = manager(path, family_with(DROP_INDEX))
    stray = tmp_path / "not-a-backup.db"
    stray.write_bytes(b"junk")
    for allow, backup in ((False, None), (True, None), (True, stray)):
        with pytest.raises(DestructiveMigrationError) as caught:
            later.migrate(allow_destructive=allow, verified_backup=backup)
        assert [m.name for m in caught.value.destructive] == ["drop_site_index"]
        assert "--allow-destructive" in str(caught.value)
    copy = later.backup(tmp_path / "backups")
    assert copy.name == "metadata-20261020T031500Z.db"
    assert verify_backup(copy, later.family, CURRENT)
    with pytest.raises(DestructiveMigrationError):
        later.migrate(allow_destructive=False, verified_backup=copy)
    assert later.status().version == CURRENT  # every refusal left the schema alone
    assert [a.name for a in later.migrate(allow_destructive=True, verified_backup=copy)] == [
        "drop_site_index"
    ]
    assert raw_rows(path, "SELECT 1 FROM sqlite_schema WHERE name = 'device_by_site'") == []


def test_mig005_fresh_install_has_nothing_to_destroy(tmp_path: Path) -> None:
    """MIG-005: an empty database applies a destructive step without confirmation.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    applied = manager(tmp_path / "metadata.db", family_with(DROP_INDEX)).migrate()
    assert [a.version for a in applied] == [*SHIPPED, NEXT]


def test_mismatch_states_refuse_to_run(tmp_path: Path) -> None:
    """5.6: foreign files and rewritten histories are refused, never migrated.

    Args:
        tmp_path: Directory for the databases.

    Returns:
        None

    Raises:
        None
    """
    foreign = tmp_path / "foreign.db"
    raw_write(foreign, "CREATE TABLE other (x INTEGER)")
    tampered = tmp_path / "tampered.db"
    manager(tampered).migrate()
    raw_write(tampered, "UPDATE schema_migrations SET checksum = 'sha256:00'")
    stamped = tmp_path / "stamped.db"
    manager(stamped).migrate()
    raw_write(stamped, "PRAGMA application_id = 42")
    for path, words in ((foreign, "not a vakr"), (tampered, "differs"), (stamped, "not a vakr")):
        status = manager(path).status()
        assert status.state is SchemaState.MISMATCH
        assert words in status.detail
        with pytest.raises(MigrationError):
            manager(path).migrate()
        with pytest.raises(MigrationError):
            manager(path).require_current()


@pytest.mark.parametrize(
    ("rows", "words"),
    [
        ([(2, "applied")], "has no migration 0001"),
        ([(1, "in_progress"), (2, "applied")], "never finished, yet later ones did"),
    ],
)
def test_mismatch_journal_histories(
    tmp_path: Path, rows: list[tuple[int, str]], words: str
) -> None:
    """5.6: a journal with gaps or an unfinished middle step is not this release's history.

    Args:
        tmp_path: Directory for the database.
        rows: Journal rows ``(version, state)`` to leave behind.
        words: Expected phrase in the detail.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    family = family_with(ADD_NOTES)
    manager(path, family).migrate()
    raw_write(path, "DELETE FROM schema_migrations")
    for version, state in rows:
        checksum = family.migrations[version - 1].checksum
        insert = "INSERT INTO schema_migrations VALUES (?, 'x', ?, ?, 0, 1, 'test')"
        raw_write(path, insert, (version, checksum, state))
    status = manager(path, family).status()
    assert status.state is SchemaState.MISMATCH
    assert words in status.detail
