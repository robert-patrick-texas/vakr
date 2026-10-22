"""Read-compatible telemetry versioning: compat headers, the reader floor, older readers (E.14).

Self-update's rollback never restores ``telemetry.db``, so a release must
keep using a schema a newer release left behind when that release's
changes were additive (E.14), and must still refuse one with a breaking
change (MIG-004). These tests play the newer release with synthetic
migrations on top of this release's real ones, then open the file as this
release does: ``status``, ``SqliteTelemetryStore``, writes, maintenance,
and ``vakr db status`` and ``migrate`` (MIG-002, MIG-004, UPD-T03).

Dependencies:
    pytest; sqlite_testkit, telemetry_testkit; vakr.adapters.sqlite.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_compat.py
"""

from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import synthetic
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import T0, raw, write
from vakr.adapters.sqlite.migrations import Migration, SchemaFamily, SqliteMigrationManager
from vakr.adapters.sqlite.migrations.journal import DatabaseFacts, JournalEntry
from vakr.adapters.sqlite.migrations.model import Compat
from vakr.adapters.sqlite.migrations.parse import parse_migration
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_family,
    telemetry_migrations,
)
from vakr.cli import cmd_db
from vakr.cli.main import main
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, new_id
from vakr.ports.migrations import SchemaState, SchemaTooNewError

ADD_TABLE = "CREATE TABLE repl_cursor (source BLOB NOT NULL PRIMARY KEY, seq INTEGER NOT NULL);"
ADD_COLUMN = "ALTER TABLE obs_icmp_hot ADD COLUMN note TEXT;"
BREAK = "DROP INDEX IF EXISTS no_such_index; CREATE TABLE later_only (x INTEGER);"


def newer(*extra: Migration) -> SchemaFamily:
    """Return the telemetry family as a later release with ``extra`` migrations ships it.

    Args:
        *extra: Synthetic migrations after this release's last one.

    Returns:
        SchemaFamily: The later family, with read-compatible versioning.

    Raises:
        None
    """
    base = telemetry_family()
    return SchemaFamily(base.name, base.application_id, (*base.migrations, *extra),
                        base.compat_from)  # fmt: skip


def at(version: int, name: str, sql: str, compat: str) -> Migration:
    """Build a later telemetry migration with a ``compat`` header.

    Args:
        version: Its schema version.
        name: Its name.
        sql: Its statements.
        compat: ``additive`` or ``breaking``.

    Returns:
        Migration: The migration.

    Raises:
        None
    """
    return synthetic(version, name, sql, compat=compat)


def migrated(tmp_path: Path, family: SchemaFamily) -> Path:
    """Create ``telemetry.db`` with ``family``'s migrations, as the later release would.

    Args:
        tmp_path: Directory for the file.
        family: The later family.

    Returns:
        Path: The database file.

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    SqliteMigrationManager(path, family).migrate()
    return path


def test_compat_header_parses_and_rejects_other_values() -> None:
    """E.14: ``compat`` is additive or breaking; anything else is a build error.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert synthetic(3, "a", ADD_TABLE, compat="additive").compat is Compat.ADDITIVE
    assert synthetic(3, "b", ADD_TABLE, compat="breaking").compat is Compat.BREAKING
    assert synthetic(3, "c", ADD_TABLE).compat is None
    with pytest.raises(ValidationError, match="compat must be additive or breaking"):
        synthetic(3, "d", ADD_TABLE, compat="maybe")
    header = "-- description: d\n-- destructive: false\n-- recovery: r\n-- data-volume: v\n"
    with pytest.raises(ValidationError, match="repeated header key compat"):
        parse_migration("0003_x.sql", f"{header}-- test-impact: t\n-- compat: additive\n"
                        f"-- compat: breaking\n\n{ADD_TABLE}\n")  # fmt: skip


def test_telemetry_migrations_from_0003_must_declare_compat() -> None:
    """E.14: from ``compat_from`` on a missing header fails the family; metadata never asks.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert telemetry_family().compat_from == 3
    with pytest.raises(ValidationError, match="declare `-- compat: additive`"):
        newer(synthetic(3, "bare", ADD_TABLE))
    assert newer(at(3, "ok", ADD_TABLE, "additive")).additive(3)
    assert not newer(at(3, "ok", ADD_TABLE, "breaking")).additive(3)
    assert not telemetry_family().additive(2)
    assert not telemetry_family().additive(99)


@pytest.mark.parametrize(
    ("additive", "floor"),
    [(frozenset(), 4), (frozenset({3, 4}), 2), (frozenset({4}), 3), (frozenset({3}), 4)],
)
def test_floor_is_the_newest_applied_breaking_migration(
    additive: frozenset[int], floor: int
) -> None:
    """E.14: the floor ignores additive versions and unfinished rows; no row means breaking.

    Args:
        additive: Versions recorded additive.
        floor: Expected floor.

    Returns:
        None

    Raises:
        None
    """
    rows = tuple(JournalEntry(v, f"m{v}", "x", True, 0, 1, "t") for v in (1, 2, 3, 4))
    assert DatabaseFacts(1, 9, True, rows, additive).floor == floor
    unfinished = (*rows[:3], JournalEntry(4, "m4", "x", False, 0, None, "t"))
    assert DatabaseFacts(1, 9, True, unfinished, additive).floor == (3 if 3 not in additive else 2)


def test_e14_an_older_release_keeps_using_a_newer_additive_schema(tmp_path: Path) -> None:
    """E.14, MIG-002: schema 4 (two additive steps) is CURRENT here; the store reads and writes.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    later = newer(at(3, "add_cursor", ADD_TABLE, "additive"),
                  at(4, "add_note", ADD_COLUMN, "additive"))  # fmt: skip
    path = migrated(tmp_path, later)
    assert raw(path, "SELECT version, compat FROM schema_compat ORDER BY version") == [
        (3, "additive"), (4, "additive")]  # fmt: skip
    status = telemetry_migrations(path).status()
    assert (status.state, status.version, status.pending) == (SchemaState.CURRENT, 4, ())
    assert "schema 4 (readable by schema 2 and later)" in status.detail
    store = SqliteTelemetryStore(path)  # the startup preflight accepts it
    backend = SqliteTelemetry(store)
    dev, col = s.device().id, new_id(CollectorId)
    write(backend, [s.icmp(dev, col, T0), s.poll(dev, col, T0)])
    backend.maintain(T0 + s.MINUTE * 10)  # moves the rows into partitions built from 0001's DDL
    assert raw(path, "SELECT count(*) FROM obs_icmp_d20261001") == [(1,)]
    assert raw(path, "SELECT count(*) FROM repl_cursor") == [(0,)]  # never touched here
    store.close()
    assert telemetry_migrations(path).migrate() == ()  # nothing to run, nothing undone


@pytest.mark.parametrize(
    ("steps", "breaking"),
    [((("drop", BREAK, "breaking"),), 3),
     ((("add", ADD_TABLE, "additive"), ("drop", BREAK, "breaking")), 4),
     ((("drop", BREAK, "breaking"), ("add", ADD_TABLE, "additive")), 3)],
)  # fmt: skip
def test_mig004_a_newer_breaking_schema_is_still_refused(
    tmp_path: Path, steps: tuple[tuple[str, str, str], ...], breaking: int
) -> None:
    """MIG-004: any breaking step above this release's newest raises the floor past it.

    Args:
        tmp_path: Directory for the file.
        steps: Later migrations as (name, SQL, compat).
        breaking: The version the refusal names.

    Returns:
        None

    Raises:
        None
    """
    extra = (at(3 + i, name, sql, c) for i, (name, sql, c) in enumerate(steps))
    path = migrated(tmp_path, newer(*extra))
    status = telemetry_migrations(path).status()
    assert status.state is SchemaState.TOO_NEW
    assert f"its migration {breaking:04d} is breaking" in status.detail
    with pytest.raises(SchemaTooNewError, match="newer vakr release"):
        SqliteTelemetryStore(path)


def test_an_unfinished_or_foreign_newer_schema_is_not_trusted(tmp_path: Path) -> None:
    """MIG-003, MIG-004: a newer migration in progress, or another build's history, is refused.

    Args:
        tmp_path: Directory for the files.

    Returns:
        None

    Raises:
        None
    """
    path = migrated(tmp_path, newer(at(3, "add", ADD_TABLE, "additive")))
    _sql(path, "UPDATE schema_migrations SET state = 'in_progress', finished_at = NULL "
               "WHERE version = 3")  # fmt: skip
    assert telemetry_migrations(path).status().state is SchemaState.TOO_NEW
    _sql(path, "UPDATE schema_migrations SET state = 'applied', finished_at = 1 WHERE version = 3")
    assert telemetry_migrations(path).status().state is SchemaState.CURRENT
    _sql(path, "UPDATE schema_migrations SET checksum = 'sha256:other' WHERE version = 2")
    assert telemetry_migrations(path).status().state is SchemaState.MISMATCH


def test_e14_db_status_and_migrate_say_readable_and_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.14: ``vakr db status`` reports "schema M (readable by N and later)"; migrate does nothing.

    Args:
        tmp_path: Install root.
        monkeypatch: ``VAKR_ROOT``.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    state = tmp_path / "var/lib/vakr"
    state.mkdir(parents=True)
    assert main(["db", "migrate"]) == 0
    SqliteMigrationManager(
        state / TELEMETRY_FILE, newer(at(3, "add", ADD_TABLE, "additive"))
    ).migrate()
    capsys.readouterr()
    assert main(["db", "status", "telemetry"]) == 0
    assert "schema 3 (readable by schema 2 and later)" in capsys.readouterr().out
    assert main(["db", "migrate"]) == 0
    out = capsys.readouterr().out
    assert "readable by schema 2 and later" in out
    assert "nothing to migrate" in out
    assert cmd_db.EXIT_TOO_NEW == 2  # still the answer for a breaking newer schema


def _sql(path: Path, statement: str) -> None:
    """Change the file directly, as a crash or another build would leave it.

    Args:
        path: Database file.
        statement: One statement.

    Returns:
        None

    Raises:
        None
    """
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path, autocommit=True)) as conn:
        conn.execute(statement)
