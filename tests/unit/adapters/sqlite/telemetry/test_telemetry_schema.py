"""Telemetry schema: migration, pinned codes, templates, and the newer-schema refusal.

Covers MIG-001 (fresh install), MIG-004 (a newer schema is refused), the
ADR-021 schema (dictionaries, ``vocab_code``, catalog, templates, staging),
and the rule that Python column lists match the migrated DDL, which is how
a future template change is caught before it ships.

Dependencies:
    pytest, sqlite3; vakr.adapters.sqlite.telemetry; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_schema.py
"""

import re
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import synthetic
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import T0, open_store, raw, write
from vakr.adapters.sqlite.metadata import METADATA_APPLICATION_ID
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_APPLICATION_ID,
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_family,
    telemetry_migrations,
)
from vakr.adapters.sqlite.telemetry.layout import (
    COLUMNS,
    KEY_COLUMNS,
    NATIVE_FAMILIES,
    ROLLUP_COLUMNS,
    ROLLUP_FAMILIES,
    all_columns,
)
from vakr.adapters.sqlite.telemetry.vocab import CODES, vocab_rows
from vakr.domain.ids import CollectorId, InterfaceId, new_id
from vakr.ports.migrations import MigrationError, SchemaState, SchemaTooNewError


def _columns(path: Path, table: str) -> list[str]:
    """Return a table's column names in order.

    Args:
        path: Database file.
        table: Table name.

    Returns:
        list[str]: Columns.

    Raises:
        None
    """
    return [r[1] for r in raw(path, f"PRAGMA table_info({table})")]


def _primary_key(path: Path, table: str) -> list[str]:
    """Return a table's primary-key columns in key order.

    Args:
        path: Database file.
        table: Table name.

    Returns:
        list[str]: Key columns.

    Raises:
        None
    """
    rows = raw(path, f"PRAGMA table_info({table})")
    return [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]


def test_mig001_fresh_install_creates_the_fixed_schema(tmp_path: Path) -> None:
    """MIG-001: migrate creates every fixed table (0002: the projection), stamps the ID, seeds.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    manager = telemetry_migrations(path)
    assert manager.status().state is SchemaState.MISSING
    applied = manager.migrate()
    assert [a.name for a in applied] == ["initial", "ranking_projection"]
    assert manager.status().state is SchemaState.CURRENT
    assert raw(path, "PRAGMA application_id") == [(TELEMETRY_APPLICATION_ID,)]
    assert TELEMETRY_APPLICATION_ID != METADATA_APPLICATION_ID
    names = {r[0] for r in raw(path, "SELECT name FROM sqlite_schema WHERE type = 'table'")}
    fixed = {"dict_collector", "dict_device", "dict_interface", "vocab_code",
             "partition_template", "partition_catalog", "telemetry_state",
             "rank_unit", "rank_iface"}  # fmt: skip
    assert fixed | {f"{f}_hot" for f in NATIVE_FAMILIES} <= names
    assert raw(path, "SELECT value FROM telemetry_state WHERE name = 'staging_boundary_us'") == [
        (0,)
    ]
    assert manager.check_integrity().ok


def test_vocab_codes_are_pinned_complete_and_recorded(tmp_path: Path) -> None:
    """ADR-021: every stored enum member has a unique pinned code, mirrored in ``vocab_code``.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    for kind, codes in CODES.items():
        assert set(codes) == {m.value for m in kind}, f"{kind.__name__} lacks a pinned code"
        assert sorted(codes.values()) == list(range(1, len(codes) + 1))
    store = open_store(tmp_path)
    stored = raw(store.path, "SELECT vocab, value, code FROM vocab_code ORDER BY 1, 2, 3")
    assert stored == vocab_rows()
    store.close()


def test_column_lists_match_the_migrated_ddl(tmp_path: Path) -> None:
    """ADR-021: staging tables and template-built partitions have the columns the code uses.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    rows = [s.sample(dev, iface, col, T0), s.icmp(dev, col, T0), s.snmp(dev, col, T0),
            s.poll(dev, col, T0), s.health(col, T0)]  # fmt: skip
    backend = SqliteTelemetry(store)
    write(backend, rows)
    backend.maintain(T0 + s.MINUTE * 10)  # moves the rows into day partitions
    for family in NATIVE_FAMILIES:
        hot, day = f"{family}_hot", f"{family}_d20261001"
        assert _columns(store.path, hot) == list(all_columns(family))
        assert _columns(store.path, day) == list(KEY_COLUMNS + COLUMNS[family])
        assert _primary_key(store.path, day) == list(KEY_COLUMNS)
        assert _primary_key(store.path, hot) == ["ts_us", "series_key", "collector_key",
                                                 "observation_uuid"]  # fmt: skip
    versions = raw(store.path, "SELECT DISTINCT template_version FROM partition_catalog")
    assert versions == [(1,)]
    for family in ROLLUP_FAMILIES:
        ddl = raw(store.path, "SELECT ddl FROM partition_template WHERE family = ?", (family,))
        assert all(c in ddl[0][0] for c in ROLLUP_COLUMNS)
    store.close()


def test_mig004_store_refuses_a_newer_or_older_schema(tmp_path: Path) -> None:
    """MIG-004: a schema from a newer release is refused; an unmigrated file asks for migrate.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    with pytest.raises(MigrationError, match="vakr db migrate"):
        SqliteTelemetryStore(path)
    base = telemetry_family()
    note = synthetic(len(base.migrations) + 1, "add_note",
                     "ALTER TABLE vocab_code ADD COLUMN note TEXT;")  # fmt: skip
    later = SchemaFamily(base.name, base.application_id, (*base.migrations, note))
    SqliteMigrationManager(path, later).migrate()
    with pytest.raises(SchemaTooNewError, match="newer vakr release"):
        SqliteTelemetryStore(path)
    assert telemetry_migrations(path).status().state is SchemaState.TOO_NEW
    SqliteTelemetryStore(path, family=later).close()  # the newer release itself may open it


FLOOR_LOGIC_ENTRY = "Read-compatible telemetry versioning"
"""The changelog entry of the release that first reads newer additive schemas (E.14)."""
OLDEST_UPGRADE_SOURCE = (0, 1, 0)
"""The oldest release self-update must still upgrade from: today every published one (E.14)."""


def floor_release(sections: list[str]) -> tuple[int, ...] | None:
    """Return the first published release whose notes introduce the reader floor.

    Args:
        sections: The changelog's ``## `` sections, newest first.

    Returns:
        tuple[int, ...] | None: Its version, or ``None`` while it is unreleased.

    Raises:
        None
    """
    released = [re.match(r"(\d+)\.(\d+)\.(\d+) - ", s) for s in sections]
    found = [tuple(int(g) for g in m.groups()) for m, s in zip(released, sections, strict=True)
             if m is not None and FLOOR_LOGIC_ENTRY in s]  # fmt: skip
    return min(found) if found else None


def test_a_second_telemetry_migration_needs_a_rollback_story() -> None:
    """E.14 tripwire: self-update does not back up telemetry.db, so rollback cannot undo its schema.

    A failed update restores ``metadata.db`` from its pre-update backup, but
    ``telemetry.db`` (about 40 GB per node) is never copied. A release that
    rolls back must therefore still use the newer schema. Releases with the
    reader floor do when the newer migrations are additive (E.14). Releases
    without it, and any release facing a breaking migration, refuse it
    (MIG-004). So a telemetry migration after 0001 ships in a release whose
    notes carry ``manual-update-required``, which keeps the update timer
    away (UPD-005), unless it is additive and every release self-update may
    still upgrade from (``OLDEST_UPGRADE_SOURCE``) has the floor logic. The
    changelog section naming the migration file is those notes (ADR-021
    amendment 7 for 0002).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    from vakr.lifecycle.update.policy import MANUAL_MARKER

    family = telemetry_family()
    assert family.migrations[0].name == "initial"
    changelog = (Path(__file__).parents[5] / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", changelog, flags=re.MULTILINE)[1:]
    first = floor_release(sections)
    readers_ready = first is not None and first <= OLDEST_UPGRADE_SOURCE
    for m in family.migrations[1:]:
        name = f"{m.version:04d}_{m.name}.sql"
        notes = [s for s in sections if name in s]
        assert notes, f"name {name} in the changelog section of the release that ships it"
        if family.additive(m.version) and readers_ready:
            continue  # every release that may roll back reads it (E.14)
        assert MANUAL_MARKER in notes[0], f"the release shipping {name} needs {MANUAL_MARKER}"


def test_the_floor_release_comes_from_the_changelog() -> None:
    """E.14: only a published section introducing the floor counts; the oldest such one wins.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    entry = f"- {FLOOR_LOGIC_ENTRY} (E.14).\n"
    assert floor_release([f"Unreleased\n{entry}", "0.2.1 - 2026-10-20\nfixes\n"]) is None
    sections = [f"1.1.0 - 2027-01-02\n{entry}", f"1.0.0 - 2027-01-01\n{entry}", "0.9.0 - x\n"]
    assert floor_release(sections) == (1, 0, 0)
    assert OLDEST_UPGRADE_SOURCE < (1, 0, 0)  # so additive migrations still need the marker
