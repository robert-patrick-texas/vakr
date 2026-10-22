"""``DashboardService``: create, change, and delete custom dashboards, audited (UI-004, SEC-018).

Every change runs in one metadata unit of work with its audit record, so a
dashboard is never stored without the record of who stored it (DB-002):

- ``create`` refuses a taken name (409), more than ``MAX_DASHBOARDS``
  dashboards (422), and widgets that name missing inventory (422, with the
  widget's field path);
- ``update`` takes the version the admin's copy was based on; a stale one
  is a conflict (409, DB-003), never a silent overwrite;
- ``delete`` removes the dashboard; the audit record keeps its name.

Callers are the API (as a username or ``local:<account>``) and tests; no
CLI exists for dashboards in this release (Appendix E.31).

Dependencies:
    vakr.dashboards.references: Reference checks.
    vakr.domain: Records, audit events, IDs, ownership.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Summaries hold the dashboard name and widget count only.

Example:
    >>> service = DashboardService(memory_uow_factory(), SystemClock())  # doctest: +SKIP
    >>> service.create(DashboardDraft("NOC", "", 60, ()), actor="alice").version  # doctest: +SKIP
    1
"""

from dataclasses import dataclass, replace

from vakr.dashboards.references import check_references
from vakr.domain.control import AuditEvent
from vakr.domain.dashboard_widgets import Widget
from vakr.domain.dashboards import MAX_DASHBOARDS, Dashboard, Visibility
from vakr.domain.enums import AuditOutcome
from vakr.domain.errors import NotFoundError, ValidationError
from vakr.domain.ids import AuditEventId, DashboardId, new_id
from vakr.domain.ownership import API_MANAGED
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class DashboardDraft:
    """What an admin asks a dashboard to be: everything but identity and bookkeeping.

    Attributes:
        name: Display name.
        description: Optional text.
        refresh_s: Seconds between refreshes.
        widgets: Widgets in display order.
    """

    name: str
    description: str
    refresh_s: int
    widgets: tuple[Widget, ...]


def _audit(uow: MetadataUnitOfWork, action: str, board: Dashboard, at: Dashboard | None,
           *, actor: str, request_id: str | None) -> None:  # fmt: skip
    """Append the audit record of a dashboard change (SEC-018).

    Args:
        uow: The unit of work making the change.
        action: ``dashboard.create``, ``dashboard.update``, or ``dashboard.delete``.
        board: The dashboard as stored (or as removed).
        at: The stored record whose ``updated_at`` dates the event; ``board`` when ``None``.
        actor: Who made the change.
        request_id: The API request, if any.

    Returns:
        None

    Raises:
        ValidationError: If the actor is not a valid audit actor.
    """
    dated = at or board
    summary = f"dashboard {board.name!r}, {len(board.widgets)} widgets, version {board.version}"
    uow.audit.append(AuditEvent(new_id(AuditEventId), dated.updated_at, actor, action,
                                "dashboard", board.id, AuditOutcome.SUCCESS, summary,
                                request_id))  # fmt: skip


class DashboardService:
    """Admin writes and plain reads of custom dashboards."""

    def __init__(self, metadata: MetadataUnitOfWorkFactory, clock: Clock) -> None:
        """Bind the service.

        Args:
            metadata: Opens metadata units of work.
            clock: Time source for ``updated_at`` and the audit records.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._clock = clock

    def list(self) -> tuple[Dashboard, ...]:
        """Return every dashboard by name (at most ``MAX_DASHBOARDS``).

        Args:
            None

        Returns:
            tuple[Dashboard, ...]: The dashboards.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            return uow.dashboards.list_all(limit=MAX_DASHBOARDS)

    def get(self, dashboard_id: DashboardId) -> Dashboard | None:
        """Return one dashboard.

        Args:
            dashboard_id: The UUID.

        Returns:
            Dashboard | None: The dashboard, or ``None``.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            return uow.dashboards.get(dashboard_id)

    def create(self, draft: DashboardDraft, *, actor: str, request_id: str | None = None
               ) -> Dashboard:  # fmt: skip
        """Store a new dashboard and its audit record.

        Args:
            draft: Name, description, refresh, and widgets.
            actor: Who creates it.
            request_id: The API request, if any.

        Returns:
            Dashboard: The stored dashboard, version 1.

        Raises:
            ValidationError: Bounds, missing references, or the dashboard limit (422).
            DuplicateError: The name is taken (409).
            StorageError: If the metadata store fails.
        """
        now = self._clock.now_utc()
        board = Dashboard(new_id(DashboardId), draft.name, draft.description, draft.widgets,
                          draft.refresh_s, API_MANAGED, Visibility.PRIVATE, actor, now, actor,
                          now)  # fmt: skip
        with self._metadata() as uow:
            if uow.dashboards.count() >= MAX_DASHBOARDS:
                raise ValidationError(f"at most {MAX_DASHBOARDS} dashboards can exist; "
                                      "delete one first", field="name")  # fmt: skip
            check_references(uow, board.widgets)
            stored = uow.dashboards.add(board)
            _audit(uow, "dashboard.create", stored, None, actor=actor, request_id=request_id)
            uow.commit()
        return stored

    def update(self, dashboard_id: DashboardId, draft: DashboardDraft, *, version: int,
               actor: str, request_id: str | None = None) -> Dashboard:  # fmt: skip
        """Replace a dashboard's content, if it is still at ``version`` (DB-003).

        Args:
            dashboard_id: The dashboard.
            draft: The new content.
            version: The version the admin's copy was based on.
            actor: Who changes it.
            request_id: The API request, if any.

        Returns:
            Dashboard: The stored dashboard, one version later.

        Raises:
            NotFoundError: No such dashboard (404).
            ConflictError: Someone changed it since ``version`` (409).
            ValidationError, DuplicateError, StorageError: As for ``create``.
        """
        with self._metadata() as uow:
            stored = uow.dashboards.get(dashboard_id)
            if stored is None:
                raise NotFoundError("dashboard", dashboard_id)
            changed = replace(stored, name=draft.name, description=draft.description,
                              refresh_s=draft.refresh_s, widgets=draft.widgets,
                              updated_by=actor, updated_at=self._clock.now_utc(),
                              version=version)  # fmt: skip
            check_references(uow, changed.widgets)
            new = uow.dashboards.update(changed)
            _audit(uow, "dashboard.update", new, None, actor=actor, request_id=request_id)
            uow.commit()
        return new

    def delete(self, dashboard_id: DashboardId, *, actor: str, request_id: str | None = None
               ) -> Dashboard:  # fmt: skip
        """Delete a dashboard and record who did.

        Args:
            dashboard_id: The dashboard.
            actor: Who deletes it.
            request_id: The API request, if any.

        Returns:
            Dashboard: What was deleted.

        Raises:
            NotFoundError: No such dashboard (404).
            StorageError: If the metadata store fails.
        """
        with self._metadata() as uow:
            gone = uow.dashboards.delete(dashboard_id)
            if gone is None:
                raise NotFoundError("dashboard", dashboard_id)
            dated = replace(gone, updated_at=self._clock.now_utc())
            _audit(uow, "dashboard.delete", gone, dated, actor=actor, request_id=request_id)
            uow.commit()
        return gone
