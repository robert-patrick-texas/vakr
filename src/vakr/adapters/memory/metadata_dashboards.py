"""In-memory custom-dashboard repository (UI-004, DB-003).

The reference implementation of ``DashboardRepository``: versioned records
staged in the unit of work's transaction, unique by ID and name, checked
for a moved version at commit (DB-003), and deleted with a tombstone.

Dependencies:
    vakr.adapters.memory.metadata_store: The transaction.
    vakr.domain: The dashboard record and errors.

Safety:
    Errors name the record kind, ID, or name only.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemoryDashboardRepository(Transaction(MemoryMetadataStore())).count()
    0
"""

from dataclasses import replace

from vakr.adapters.memory.metadata_store import Transaction
from vakr.domain.dashboards import Dashboard
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import DashboardId

FIXED = ("id", "created_by", "created_at", "managed_by")
"""Fields an update may not change."""


class MemoryDashboardRepository:
    """Custom dashboards, by ``(name, id)``."""

    def __init__(self, tx: Transaction) -> None:
        """Bind the repository to a transaction.

        Args:
            tx: The unit of work's transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def add(self, dashboard: Dashboard) -> Dashboard:
        """Stage a new dashboard.

        Args:
            dashboard: The record.

        Returns:
            Dashboard: The record.

        Raises:
            ValidationError, DuplicateError: As the port documents.
        """
        if dashboard.version != 1:
            raise ValidationError("a new dashboard must have version 1", field="version")
        if self._tx.get("dashboard", dashboard.id) is not None:
            raise DuplicateError("dashboard", "id", dashboard.id)
        if self.get_by_name(dashboard.name) is not None:
            raise DuplicateError("dashboard", "name", dashboard.name)
        self._tx.put("dashboard", dashboard, None)
        return dashboard

    def get(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Return one dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: The record, or ``None``.

        Raises:
            None
        """
        return self._tx.get("dashboard", dashboard_id)

    def get_by_name(self, name: str) -> Dashboard | None:
        """Return the dashboard with exactly this name.

        Args:
            name: The name.

        Returns:
            Dashboard | None: The record, or ``None``.

        Raises:
            None
        """
        return next((d for d in self._tx.records("dashboard") if d.name == name), None)

    def list_all(self, *, limit: int) -> tuple[Dashboard, ...]:
        """List dashboards by ``(name, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[Dashboard, ...]: The records.

        Raises:
            None
        """
        ordered = sorted(self._tx.records("dashboard"), key=lambda d: (d.name, str(d.id)))
        return tuple(ordered[: max(limit, 0)])

    def update(self, dashboard: Dashboard) -> Dashboard:
        """Stage a changed dashboard, checking the version.

        Args:
            dashboard: The new content, based on ``dashboard.version``.

        Returns:
            Dashboard: The stored record.

        Raises:
            NotFoundError, ConflictError, DuplicateError, ValidationError: As the port documents.
        """
        stored: Dashboard | None = self._tx.get("dashboard", dashboard.id)
        if stored is None:
            raise NotFoundError("dashboard", dashboard.id)
        if stored.version != dashboard.version:
            raise ConflictError("dashboard", dashboard.id, expected_version=dashboard.version,
                                actual_version=stored.version)  # fmt: skip
        for name in FIXED:
            if getattr(stored, name) != getattr(dashboard, name):
                raise ValidationError(f"{name} cannot change", field=name)
        other = self.get_by_name(dashboard.name)
        if other is not None and other.id != dashboard.id:
            raise DuplicateError("dashboard", "name", dashboard.name)
        new = replace(dashboard, version=stored.version + 1)
        self._tx.put("dashboard", new, stored.version)
        return new

    def delete(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Stage the removal of a dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: What was removed, or ``None``.

        Raises:
            None
        """
        stored: Dashboard | None = self._tx.get("dashboard", dashboard_id)
        if stored is not None:
            self._tx.delete("dashboard", dashboard_id)
        return stored

    def count(self) -> int:
        """Return how many dashboards exist.

        Args:
            None

        Returns:
            int: Count.

        Raises:
            None
        """
        return len(self._tx.records("dashboard"))
