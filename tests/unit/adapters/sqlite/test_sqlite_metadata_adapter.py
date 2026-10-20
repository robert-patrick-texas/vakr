"""SQLite metadata adapter behaviour the shared contract suites leave out.

Covers another process holding the write lock, stale read snapshots, the
schema's no-delete and append-only triggers, damaged rows, forged cursors,
interface tag kinds (META-013), and misuse outside a ``with`` block.

Dependencies:
    pytest, sqlite3; vakr.adapters.sqlite; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_metadata_adapter.py
"""

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import FAST, factory, migrated_store, raw_write
from vakr.adapters.sqlite.connection import SqliteSettings
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, table, transaction
from vakr.domain.errors import ConflictError, StorageError, ValidationError
from vakr.domain.inventory import API_MANAGED
from vakr.domain.paging import PageRequest, encode_cursor
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.migrations import MigrationError, SchemaState


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Create a migrated metadata file.

    Args:
        tmp_path: Directory for the database.

    Returns:
        Path: The file.

    Raises:
        None
    """
    path = tmp_path / METADATA_FILE
    migrated_store(path).close()
    return path


def _open(path: Path) -> tuple[SqliteMetadataStore, MetadataUnitOfWorkFactory]:
    """Open a store with short bounds.

    Args:
        path: Migrated database.

    Returns:
        tuple[SqliteMetadataStore, MetadataUnitOfWorkFactory]: Store and factory.

    Raises:
        None
    """
    store = SqliteMetadataStore(path, FAST)
    return store, factory(store)


def test_db008_other_process_holding_the_lock_is_a_bounded_retryable_failure(db: Path) -> None:
    """DB-008: a foreign writer delays ours only up to busy_timeout; readers keep reading.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    with new_uow() as uow:
        uow.tags.add(s.tag("seen"))
        uow.commit()
    with closing(sqlite3.connect(db, autocommit=True)) as other:
        other.execute("BEGIN IMMEDIATE")
        with new_uow() as reader:
            assert reader.tags.get_by_name("seen") is not None  # WAL: readers never wait
        with new_uow() as writer:
            with pytest.raises(StorageError) as caught:
                writer.tags.add(s.tag("blocked"))
            assert caught.value.retryable
            assert writer.tags.get_by_name("seen") is not None  # still usable for reads
        other.execute("ROLLBACK")
    with new_uow() as uow:
        uow.tags.add(s.tag("later"))
        uow.commit()
    store.close()


def test_second_writer_in_the_same_thread_fails_fast(db: Path) -> None:
    """DB-008: a thread cannot wait for itself; the first writer is unaffected.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    with new_uow() as first, new_uow() as second:
        first.tags.add(s.tag("a"))
        with pytest.raises(StorageError, match="already holds"):
            second.tags.add(s.tag("b"))
        first.commit()
    with new_uow() as uow:
        assert [t.name for t in uow.tags.list_page(PageRequest()).items] == ["a"]
    store.close()


def test_stale_snapshot_gives_accurate_conflicts_and_no_spurious_ones(db: Path) -> None:
    """DB-003: a write after another commit sees the newest data, never SQLITE_BUSY_SNAPSHOT.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    dev = s.device()
    with new_uow() as uow:
        uow.devices.add(dev)
        uow.commit()
    with new_uow() as alice:
        copy = alice.devices.get(dev.id)  # alice's snapshot starts here
        assert copy is not None
        with new_uow() as bob:
            bob.devices.update(replace(copy, comment="bob"), writer=API_MANAGED)
            bob.commit()
        alice.tags.add(s.tag("unrelated"))  # not a conflict: nothing alice wrote changed
        with pytest.raises(ConflictError) as caught:
            alice.devices.update(replace(copy, comment="alice"), writer=API_MANAGED)
        assert (caught.value.expected_version, caught.value.actual_version) == (1, 2)
        alice.commit()
    with new_uow() as uow:
        stored = uow.devices.get(dev.id)
        assert stored is not None
        assert stored.comment == "bob"
        assert uow.tags.get_by_name("unrelated") is not None
    store.close()


def test_update_rechecks_the_version_in_sql(db: Path) -> None:
    """DB-003: the UPDATE's own version check catches a stale read (row count zero).

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, _ = _open(db)
    tag = s.tag()
    with store.unit_of_work() as uow:
        uow.tags.add(tag)
        uow.tags.update(replace(tag, description="v2"), writer=API_MANAGED)
        with pytest.raises(ConflictError) as caught:
            # A stale "stored" copy passes the in-memory check; the UPDATE's WHERE catches it.
            uow.tags._replace(tag, replace(tag, description="v3"), API_MANAGED, ("id",))
        assert caught.value.actual_version == 2
    store.close()


def test_rows_are_never_deleted_and_history_is_append_only(db: Path) -> None:
    """META-009, IFID-005, SEC-018: the schema itself refuses deletes and history rewrites.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    dev = s.device()
    iface = s.interface(dev.id)
    with new_uow() as uow:
        uow.devices.add(dev)
        uow.interfaces.add(iface)
        uow.interfaces.append_identity_history(s.history(iface, s.T0))
        uow.audit.append(s.audit(s.T0))
        uow.commit()
    store.close()
    for statement in (
        "DELETE FROM device",
        "DELETE FROM interface",
        "UPDATE identity_history SET actor = 'x'",
        "DELETE FROM identity_history",
        "UPDATE audit_event SET summary = 'x'",
        "DELETE FROM audit_event",
    ):
        with pytest.raises(sqlite3.IntegrityError, match=r"never deleted|append-only"):
            raw_write(db, statement)


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE device SET admin_state = 'bogus'",
        "UPDATE device SET created_at = -99999999999999999",  # before year 1
    ],
)
def test_damaged_rows_are_storage_errors(db: Path, damage: str) -> None:
    """A row that no longer forms a valid record is damaged storage, not a bad request.

    Args:
        db: Migrated database.
        damage: Statement that corrupts the stored device.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    dev = s.device()
    with new_uow() as uow:
        uow.devices.add(dev)
        uow.commit()
    raw_write(db, damage)
    with new_uow() as uow:
        with pytest.raises(StorageError, match="not valid"):
            uow.devices.get(dev.id)
        with pytest.raises(StorageError):
            uow.devices.list_page(PageRequest())
    store.close()


def test_damaged_history_and_audit_rows_are_storage_errors(db: Path) -> None:
    """Append-only rows that no longer decode are reported as damaged storage.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    dev = s.device()
    iface = s.interface(dev.id)
    with new_uow() as uow:
        uow.devices.add(dev)
        uow.interfaces.add(iface)
        uow.commit()
    entry, event = s.history(iface, s.T0), s.audit(s.T0)
    history = (
        "INSERT INTO identity_history (id, interface_id, device_id, decided_at, outcome, source, "
        "evidence) VALUES (?, ?, ?, 0, 'odd', 'automatic', '[]')"
    )
    raw_write(db, history, (entry.id.bytes, iface.id.bytes, dev.id.bytes))
    raw_write(db, "INSERT INTO audit_event VALUES (?, 0, 'a', 'b', 'c', NULL, 'odd', '', NULL)",
              (event.id.bytes,))  # fmt: skip
    raw_write(db, "UPDATE interface SET addresses = ?", ('{"not": "a list"}',))
    with new_uow() as uow:
        with pytest.raises(StorageError, match="identity-history"):
            uow.interfaces.identity_history(iface.id, PageRequest())
        with pytest.raises(StorageError, match="audit event"):
            uow.audit.list_page(PageRequest())
        with pytest.raises(StorageError, match="interface"):
            uow.interfaces.get(iface.id)
    store.close()


def test_forged_cursors_are_rejected(db: Path) -> None:
    """API-005: well-formed cursors with wrong part types never reach SQL.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    with new_uow() as uow:
        uow.devices.add(s.device())
        for forged in (encode_cursor((7, "x")), encode_cursor(("a", "not-a-uuid")),
                       encode_cursor(("a", 5)), encode_cursor(("a",))):  # fmt: skip
            with pytest.raises(ValidationError):
                uow.devices.list_page(PageRequest(cursor=forged))
        with pytest.raises(ValidationError):
            uow.policies.list_page(PageRequest(cursor=encode_cursor(("x", "a", "b"))))
    store.close()


def test_meta013_operator_tags_survive_description_changes(db: Path) -> None:
    """META-012/013: description tags and operator tags are separate sets in one table.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    wan, core = s.tag("wan"), s.tag("core")
    dev = s.device()
    iface = s.interface(dev.id, tags=frozenset({core.id}), description_tags=frozenset({wan.id}))
    with new_uow() as uow:
        for record in (wan, core):
            uow.tags.add(record)
        uow.devices.add(dev)
        uow.interfaces.add(iface)
        seen = uow.interfaces.update_observed(
            replace(iface, if_alias="core only", description_tags=frozenset({core.id}))
        )
        uow.commit()
    with new_uow() as uow:
        stored = uow.interfaces.get(iface.id)
    assert stored == seen
    assert (stored.tags, stored.description_tags) == (frozenset({core.id}), frozenset({core.id}))
    store.close()


def test_numbers_beyond_64_bits_are_validation_errors(db: Path) -> None:
    """A value SQLite cannot store is the caller's error, and nothing is written.

    Args:
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    with new_uow() as uow:
        with pytest.raises(ValidationError, match="64-bit"):
            uow.devices.add(s.device(poll_interval_s=2**63))
        uow.commit()
    with new_uow() as uow:
        assert uow.devices.list_page(PageRequest()).total == 0
    store.close()


def test_store_refuses_a_missing_database_and_misuse(tmp_path: Path, db: Path) -> None:
    """16.3: the store opens only a current schema; units of work need their with block.

    Args:
        tmp_path: Directory for a missing database.
        db: Migrated database.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(MigrationError) as caught:
        SqliteMetadataStore(tmp_path / "missing.db")
    assert caught.value.status.state is SchemaState.MISSING
    store, new_uow = _open(db)
    loose = new_uow()
    with pytest.raises(RuntimeError, match="with block"):
        loose.tags.get_by_name("x")
    with store.unit_of_work() as uow, pytest.raises(RuntimeError, match="begin_write"):
        uow.tags._tx.execute("SELECT 1")
    store.close()
    with pytest.raises(StorageError, match="closed"), new_uow():
        pass


def test_unit_of_work_lifecycle_edges(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-entering is harmless, closing twice is harmless, and a failed begin frees the slot.

    Args:
        db: Migrated database.
        monkeypatch: Makes ``BEGIN`` fail once.

    Returns:
        None

    Raises:
        None
    """
    store = SqliteMetadataStore(db, SqliteSettings(max_connections=1, lock_wait_ms=100))
    uow = store.unit_of_work()
    with uow, uow:  # the inner enter reuses the open transaction
        assert uow.tags.get_by_name("x") is None
    uow.__exit__(None, None, None)  # already closed: nothing to do

    def refuse(*_args: object, **_kwargs: object) -> None:
        """Fail like a broken connection would.

        Args:
            *_args: Ignored.
            **_kwargs: Ignored.

        Returns:
            None

        Raises:
            StorageError: Always.
        """
        raise StorageError("broken", retryable=False)

    monkeypatch.setattr(transaction, "begin", refuse)
    with pytest.raises(StorageError, match="broken"), store.unit_of_work():
        pass
    monkeypatch.undo()
    with store.unit_of_work() as again:  # the single connection slot was given back
        assert again.tags.get_by_name("x") is None
    store.close()


def test_db002_a_failed_call_leaves_nothing_even_if_committed(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB-002: a call that fails after writing its row is undone, as the reference adapter does.

    Args:
        db: Migrated database.
        monkeypatch: Makes membership inserts fail after the row is written.

    Returns:
        None

    Raises:
        None
    """
    store, new_uow = _open(db)
    tag = s.tag()
    dev = s.device()
    with new_uow() as uow:
        uow.tags.add(tag)
        uow.devices.add(dev)
        uow.commit()
    real = table._link_statements

    def broken(link: table.Link) -> tuple[str, str]:
        """Return a membership insert that the database refuses.

        Args:
            link: The membership table.

        Returns:
            tuple[str, str]: The real delete and a failing insert.

        Raises:
            None
        """
        delete, _ = real(link)
        return delete, "INSERT INTO missing_table VALUES (?, ?)"

    monkeypatch.setattr(table, "_link_statements", broken)
    with new_uow() as uow:
        with pytest.raises(StorageError):
            uow.devices.add(s.device("half", tags=frozenset({tag.id})))
        with pytest.raises(StorageError):
            uow.devices.update(replace(dev, comment="x", tags=frozenset({tag.id})),
                               writer=API_MANAGED)  # fmt: skip
        uow.commit()  # commits only what succeeded: nothing
    with new_uow() as uow:
        assert uow.devices.get_by_name("half") is None
        assert uow.devices.get(dev.id) == dev
    store.close()
