"""SQLite custom-dashboard repository (UI-004, DASH-007, DB-003).

Dashboards live in ``dashboard`` (migration ``0004_dashboards``), unique by
ID and name. Widgets are one canonical-JSON column beside the
``widget_schema`` that wrote it; reading decodes them through
``vakr.domain.dashboard_codec``, which upgrades an older schema, and every
write stores the current one. Updates check the optimistic version under
SQLite's write lock (DB-003).

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.transaction.
    vakr.domain: The dashboard record, codec, ownership, and errors.

Safety:
    SQL is fixed text with bound values. Messages name the record kind and
    ID or name only.

Example:
    >>> SqliteDashboardRepository(tx).count()  # doctest: +SKIP
    0
"""

import json
import sqlite3
from dataclasses import replace

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.dashboard_codec import widgets_from_json, widgets_to_json
from vakr.domain.dashboards import WIDGET_SCHEMA, Dashboard, Visibility
from vakr.domain.enums import ManagedByKind
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from vakr.domain.ids import DashboardId
from vakr.domain.json_values import canonical_json
from vakr.domain.ownership import ManagedBy

_COLUMNS = (
    "id", "name", "description", "managed_by_kind", "managed_by_source", "visibility",
    "refresh_s", "widget_schema", "widgets", "created_by", "created_at", "updated_by",
    "updated_at", "version",
)  # fmt: skip
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM dashboard"  # noqa: S608  # constant columns
_INSERT = (
    f"INSERT INTO dashboard ({', '.join(_COLUMNS)}) "  # noqa: S608  # constant columns
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)
_UPDATE = (
    "UPDATE dashboard SET name = ?, description = ?, visibility = ?, refresh_s = ?, "
    "widget_schema = ?, widgets = ?, updated_by = ?, updated_at = ?, version = ? WHERE id = ?"
)
_FIXED = ("id", "created_by", "created_at", "managed_by")


def _widgets_text(d: Dashboard) -> str:
    """Encode a dashboard's widgets as canonical JSON text.

    Args:
        d: The dashboard.

    Returns:
        str: The JSON array.

    Raises:
        None
    """
    return canonical_json(widgets_to_json(d.widgets)).decode("utf-8")


def _encode(d: Dashboard) -> tuple[object, ...]:
    """Encode a dashboard as a row in ``_COLUMNS`` order.

    Args:
        d: The dashboard.

    Returns:
        tuple[object, ...]: The values.

    Raises:
        None
    """
    return (
        d.id.bytes, d.name, d.description, d.managed_by.kind.value, d.managed_by.source,
        d.visibility.value, d.refresh_s, WIDGET_SCHEMA, _widgets_text(d), d.created_by,
        encode_time(d.created_at), d.updated_by, encode_time(d.updated_at), d.version,
    )  # fmt: skip


def _decode(r: sqlite3.Row) -> Dashboard:
    """Decode a dashboard row, upgrading an older widget schema (DASH-007).

    Args:
        r: The row.

    Returns:
        Dashboard: The record.

    Raises:
        StorageError: If the row no longer forms a valid dashboard.
    """
    try:
        widgets = widgets_from_json(r["widget_schema"], json.loads(r["widgets"]))
        return Dashboard(
            id=DashboardId(blob_to_uuid(r["id"])),
            name=r["name"],
            description=r["description"],
            widgets=widgets,
            refresh_s=r["refresh_s"],
            managed_by=ManagedBy(ManagedByKind(r["managed_by_kind"]), r["managed_by_source"]),
            visibility=Visibility(r["visibility"]),
            created_by=r["created_by"],
            created_at=decode_time(r["created_at"]),
            updated_by=r["updated_by"],
            updated_at=decode_time(r["updated_at"]),
            version=r["version"],
        )
    except (ValueError, RecursionError) as exc:  # ValidationError is a ValueError
        reason = f": {exc}" if isinstance(exc, ValidationError) else ""
        raise StorageError(f"a stored dashboard is not valid{reason}", retryable=False) from None


class SqliteDashboardRepository:
    """Custom dashboards in one unit of work, by ``(name, id)``."""

    def __init__(self, tx: SqliteTransaction) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def add(self, dashboard: Dashboard) -> Dashboard:
        """Insert a new dashboard.

        Args:
            dashboard: The record, ``version == 1``.

        Returns:
            Dashboard: The record.

        Raises:
            ValidationError, DuplicateError, StorageError: As the port documents.
        """
        if dashboard.version != 1:
            raise ValidationError("a new dashboard must have version 1", field="version")
        self._tx.begin_write()
        if self.get(dashboard.id) is not None:
            raise DuplicateError("dashboard", "id", dashboard.id)
        if self._tx.query("SELECT 1 FROM dashboard WHERE name = ?", (dashboard.name,)):
            raise DuplicateError("dashboard", "name", dashboard.name)
        self._tx.execute(_INSERT, _encode(dashboard), ErrorContext("dashboard", dashboard.id))
        return dashboard

    def get(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Return one dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE id = ?", (dashboard_id.bytes,))
        return _decode(rows[0]) if rows else None

    def get_by_name(self, name: str) -> Dashboard | None:
        """Return the dashboard with exactly this name.

        Args:
            name: The name.

        Returns:
            Dashboard | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE name = ?", (name,))
        return _decode(rows[0]) if rows else None

    def list_all(self, *, limit: int) -> tuple[Dashboard, ...]:
        """List dashboards by ``(name, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[Dashboard, ...]: The records.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} ORDER BY name, id LIMIT ?", (max(limit, 0),))
        return tuple(_decode(r) for r in rows)

    def update(self, dashboard: Dashboard) -> Dashboard:
        """Replace a dashboard, checking the version under the write lock.

        Args:
            dashboard: New content based on ``dashboard.version``.

        Returns:
            Dashboard: The stored record.

        Raises:
            NotFoundError, ConflictError, DuplicateError, ValidationError,
                StorageError: As the port documents.
        """
        self._tx.begin_write()
        stored = self.get(dashboard.id)
        if stored is None:
            raise NotFoundError("dashboard", dashboard.id)
        if stored.version != dashboard.version:
            raise ConflictError("dashboard", dashboard.id, expected_version=dashboard.version,
                                actual_version=stored.version)  # fmt: skip
        for name in _FIXED:
            if getattr(stored, name) != getattr(dashboard, name):
                raise ValidationError(f"{name} cannot change", field=name)
        taken = self._tx.query("SELECT id FROM dashboard WHERE name = ? AND id != ?",
                               (dashboard.name, dashboard.id.bytes))  # fmt: skip
        if taken:
            raise DuplicateError("dashboard", "name", dashboard.name)
        new = replace(dashboard, version=stored.version + 1)
        values = (
            new.name, new.description, new.visibility.value, new.refresh_s, WIDGET_SCHEMA,
            _widgets_text(new), new.updated_by, encode_time(new.updated_at), new.version,
            new.id.bytes,
        )  # fmt: skip
        self._tx.execute(_UPDATE, values, ErrorContext("dashboard", new.id, dashboard.version))
        return new

    def delete(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Delete a dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: What was removed, or ``None``.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        stored = self.get(dashboard_id)
        if stored is not None:
            self._tx.execute("DELETE FROM dashboard WHERE id = ?", (dashboard_id.bytes,))
        return stored

    def count(self) -> int:
        """Return how many dashboards exist.

        Args:
            None

        Returns:
            int: Count.

        Raises:
            StorageError: If the table cannot be read.
        """
        return int(self._tx.query("SELECT count(*) FROM dashboard")[0][0])
