"""The custom-dashboard repository every metadata unit of work exposes (UI-004, DASH-007, DB-003).

Rules every backend follows (the contract suite in
``tests/contract/contract_dashboards.py`` checks them):

- ``add`` takes ``version == 1`` and raises ``DuplicateError`` for a reused
  ID or name; names compare exactly, as typed;
- ``update`` raises ``NotFoundError`` for an unknown dashboard,
  ``ConflictError`` when the stored version is not the one the change was
  based on (DB-003), ``DuplicateError`` for a name another dashboard has,
  and ``ValidationError`` when ``id``, ``created_by``, ``created_at``, or
  ``managed_by`` would change; it stores ``version + 1``;
- ``delete`` removes the row and returns what was removed; dashboards are
  presentation, not inventory, so META-009's soft lifecycle does not apply
  (Appendix E.31), and the audit record keeps the name;
- lists are ordered by ``(name, id)`` and bounded by ``limit``;
- none of it moves ``change_counter``: a dashboard edit must not make the
  collector reload its inventory.

``dashboards`` was added with M4 custom dashboards (port extension, E.31).

Dependencies:
    vakr.domain: The dashboard record and its ID.

Safety:
    Implementations return domain records, never driver rows (rule 2).

Example:
    >>> sorted(n for n in dir(DashboardRepository) if not n.startswith("_"))
    ['add', 'count', 'delete', 'get', 'get_by_name', 'list_all', 'update']
"""

from typing import Protocol

from vakr.domain.dashboards import Dashboard
from vakr.domain.ids import DashboardId


class DashboardRepository(Protocol):
    """Custom dashboards, listed by ``(name, id)``."""

    def add(self, dashboard: Dashboard) -> Dashboard:
        """Store a new dashboard.

        Args:
            dashboard: The record, ``version == 1``.

        Returns:
            Dashboard: The stored record.

        Raises:
            DuplicateError: If the ID or name is taken.
            ValidationError: If ``version`` is not 1.
            StorageError: If the write fails.
        """
        ...

    def get(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Return one dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        ...

    def get_by_name(self, name: str) -> Dashboard | None:
        """Return the dashboard with exactly this name.

        Args:
            name: The name.

        Returns:
            Dashboard | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        ...

    def list_all(self, *, limit: int) -> tuple[Dashboard, ...]:
        """List dashboards by ``(name, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[Dashboard, ...]: The records.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        ...

    def update(self, dashboard: Dashboard) -> Dashboard:
        """Replace a dashboard, checking the optimistic version.

        Args:
            dashboard: New content; ``version`` is the version it was based on.

        Returns:
            Dashboard: The stored record, with ``version + 1``.

        Raises:
            NotFoundError, ConflictError, DuplicateError, ValidationError: As
                the module docstring says.
            StorageError: If the write fails.
        """
        ...

    def delete(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Remove a dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: What was removed, or ``None`` when it did not exist.

        Raises:
            StorageError: If the write fails.
        """
        ...

    def count(self) -> int:
        """Return how many dashboards exist.

        Args:
            None

        Returns:
            int: Count.

        Raises:
            StorageError: If the table cannot be read.
        """
        ...


class DashboardRepositories(Protocol):
    """The dashboard part of a metadata unit of work."""

    @property
    def dashboards(self) -> DashboardRepository:
        """Custom dashboards in this unit of work.

        Args:
            None

        Returns:
            DashboardRepository: The repository.

        Raises:
            None
        """
        ...
