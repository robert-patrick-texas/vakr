"""The dashboards schema on SQLite: upgrade with data intact, widget-schema upgrades on read.

A database from before custom dashboards gains the empty ``dashboard`` table
with its records intact (MIG-002). Stored widgets carry the schema that
wrote them: an older one is upgraded when read and the next write stores
the current one; a newer one is refused as damaged-for-this-release rather
than guessed at (DASH-007).

Dependencies:
    pytest; the SQLite metadata adapter; tests.contract samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_dashboard_schema.py
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.contract import contract_auth_samples as a
from tests.contract import contract_samples as s
from tests.contract.contract_dashboards import dashboard
from tests.unit.adapters.sqlite.sqlite_testkit import manager, migrated_store, raw_rows, raw_write
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_family,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.domain import dashboard_upgrades, dashboards
from vakr.domain.errors import StorageError


def test_mig002_a_database_before_dashboards_upgrades_with_data(tmp_path: Path) -> None:
    """MIG-002: records survive; the dashboard table starts empty and is not collection data.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    full = metadata_family()
    cut = next(i for i, m in enumerate(full.migrations) if m.name == "dashboards")
    before = list(full.migrations[:cut])  # every migration after it builds on it
    old = SchemaFamily(full.name, full.application_id, tuple(before))
    SqliteMigrationManager(db, old).migrate()
    store = SqliteMetadataStore(db, family=old)
    device, user = s.device(), a.user()
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.users.add(user)
        uow.commit()
    store.close()
    applied = metadata_migrations(db).migrate()
    assert next(m.name for m in applied) == "dashboards"  # later schemas follow it
    upgraded = SqliteMetadataStore(db)
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.devices.get(device.id) == device
        assert uow.users.get(user.id) == user
        counter = uow.change_counter()
        assert uow.dashboards.count() == 0
        uow.dashboards.add(dashboard())
        uow.commit()
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.change_counter() == counter
    upgraded.close()


def test_dash007_older_widgets_are_upgraded_on_read_and_rewritten_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DASH-007: a schema-1 row read by a release of schema 2 comes back upgraded.

    Args:
        tmp_path: Directory for the database.
        monkeypatch: Pretends this release writes widget schema 2.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    noc = dashboard()
    with sqlite_uow_factory(store)() as uow:
        uow.dashboards.add(noc)
        uow.commit()

    def retitle(widgets: list[Any]) -> list[Any]:
        """Schema 1 to 2 for this test: titles gain a prefix.

        Args:
            widgets: Schema-1 widget JSON.

        Returns:
            list[Any]: Schema-2 widget JSON.

        Raises:
            None
        """
        return [{**w, "title": f"v2 {w['title']}"} for w in widgets]

    monkeypatch.setattr(dashboards, "WIDGET_SCHEMA", 2)
    monkeypatch.setitem(dashboard_upgrades.UPGRADES, 1, retitle)
    monkeypatch.setattr("vakr.adapters.sqlite.metadata.dashboards.WIDGET_SCHEMA", 2)
    with sqlite_uow_factory(store)() as uow:
        read = uow.dashboards.get(noc.id)
        assert read is not None
        assert [w.title for w in read.widgets] == [f"v2 {w.title}" for w in noc.widgets]
        uow.dashboards.update(replace(read, description="rewritten"))
        uow.commit()
    assert raw_rows(db, "SELECT widget_schema FROM dashboard") == [(2,)]
    store.close()


def test_bak_t01_a_metadata_backup_restores_dashboards_intact(tmp_path: Path) -> None:
    """BAK-T01: a backup copy holds every dashboard with its UUID, widgets, and version.

    Args:
        tmp_path: Directory for the database and the backup.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    noc = dashboard()
    with sqlite_uow_factory(store)() as uow:
        uow.dashboards.add(noc)
        changed = uow.dashboards.update(replace(noc, description="v2"))
        uow.commit()
    copy = manager(db).backup(tmp_path / "backups")
    store.close()
    restored = SqliteMetadataStore(copy)
    with sqlite_uow_factory(restored)() as uow:
        assert uow.dashboards.get(noc.id) == changed
        assert uow.dashboards.list_all(limit=10) == (changed,)
    restored.close()


def test_dash007_widgets_from_a_newer_release_are_refused_not_guessed(tmp_path: Path) -> None:
    """DASH-007: a row whose widget schema is newer than this release reads as unusable.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    store = migrated_store(db)
    noc = dashboard()
    with sqlite_uow_factory(store)() as uow:
        uow.dashboards.add(noc)
        uow.commit()
    raw_write(db, "UPDATE dashboard SET widget_schema = 9")
    with sqlite_uow_factory(store)() as uow, pytest.raises(StorageError) as exc:
        uow.dashboards.get(noc.id)
    assert "newer than this release" in str(exc.value)
    raw_write(db, "UPDATE dashboard SET widget_schema = 1, widgets = ?", (json.dumps([{}]),))
    with sqlite_uow_factory(store)() as uow, pytest.raises(StorageError):
        uow.dashboards.list_all(limit=5)
    store.close()
