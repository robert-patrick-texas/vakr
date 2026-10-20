"""Helpers for the SQLite adapter tests: synthetic migrations, stores, raw reads.

Synthetic migrations stand in for future releases, so upgrade paths
(MIG-002) and interrupted migrations (MIG-003) are tested before any real
second migration exists. The module name is unique on purpose: test
helpers are never imported from a ``conftest``.

Dependencies:
    sqlite3: Raw inspection of database files.
    vakr.adapters.sqlite: The adapter under test.

Example:
    >>> synthetic(2, "add_notes", "ALTER TABLE device ADD COLUMN notes TEXT;").version
    2
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vakr.adapters.sqlite.connection import SqliteSettings
from vakr.adapters.sqlite.metadata import (
    SqliteMetadataStore,
    metadata_family,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.migrations import (
    Migration,
    SchemaFamily,
    SqliteMigrationManager,
    parse_migration,
)
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

NOW = datetime(2026, 10, 20, 3, 15, tzinfo=UTC)
"""Fixed wall-clock time for journals and backup names."""

FAST = SqliteSettings(busy_timeout_ms=200, lock_wait_ms=300)
"""Short bounds so contention tests finish quickly."""


def synthetic(
    version: int, name: str, sql: str, *, destructive: bool = False, transactional: bool = True
) -> Migration:
    """Build a migration as a later release would ship it.

    Args:
        version: Schema version it produces.
        name: Migration name.
        sql: Its statements.
        destructive: Whether it removes data.
        transactional: Whether it runs in one transaction.

    Returns:
        Migration: The parsed migration.

    Raises:
        None
    """
    text = (
        f"-- description: Synthetic {name} for tests.\n"
        f"-- destructive: {str(destructive).lower()}\n"
        f"-- transactional: {str(transactional).lower()}\n"
        "-- recovery: Restore the backup taken before migrating.\n"
        "-- data-volume: Tiny test data.\n"
        "-- test-impact: MIG-002, MIG-003, MIG-005.\n"
        f"\n{sql}\n"
    )
    return parse_migration(f"{version:04d}_{name}.sql", text)


def family_with(*extra: Migration) -> SchemaFamily:
    """Return the metadata family as a later release with ``extra`` migrations would ship it.

    Args:
        *extra: Migrations after this release's last one.

    Returns:
        SchemaFamily: The extended family.

    Raises:
        None
    """
    base = metadata_family()
    return SchemaFamily(base.name, base.application_id, (*base.migrations, *extra))


def manager(path: Path, family: SchemaFamily | None = None) -> SqliteMigrationManager:
    """Return a migration manager with a fixed clock.

    Args:
        path: Database file.
        family: Schema family; this release's when ``None``.

    Returns:
        SqliteMigrationManager: The manager.

    Raises:
        None
    """
    chosen = family if family is not None else metadata_family()
    return SqliteMigrationManager(path, chosen, clock=ManualClock(NOW), applied_by="vakr test")


def migrated_store(path: Path, settings: SqliteSettings | None = None) -> SqliteMetadataStore:
    """Create, migrate, and open a metadata database.

    Args:
        path: Database file to create.
        settings: Store bounds, or the defaults.

    Returns:
        SqliteMetadataStore: The open store.

    Raises:
        None
    """
    metadata_migrations(path).migrate()
    return SqliteMetadataStore(path) if settings is None else SqliteMetadataStore(path, settings)


def factory(store: SqliteMetadataStore) -> MetadataUnitOfWorkFactory:
    """Return a unit-of-work factory for ``store``.

    Args:
        store: An open store.

    Returns:
        MetadataUnitOfWorkFactory: The factory.

    Raises:
        None
    """
    return sqlite_uow_factory(store)


def raw_rows(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    """Read rows straight from a database file, bypassing the adapter.

    Args:
        path: Database file.
        sql: A query.
        params: Bound values.

    Returns:
        list[tuple[Any, ...]]: The rows.

    Raises:
        sqlite3.Error: If the query fails.
    """
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql, params).fetchall()


def raw_write(path: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    """Change a database file behind the adapter's back (damage, foreign data).

    Args:
        path: Database file.
        sql: One statement, run in autocommit mode.
        params: Bound values.

    Returns:
        None

    Raises:
        sqlite3.Error: If the statement fails.
    """
    with closing(sqlite3.connect(path, autocommit=True)) as conn:
        conn.execute(sql, params)
