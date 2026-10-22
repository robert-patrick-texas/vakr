"""Custom dashboards: named, ordered sets of widgets that admins define (UI-004, DASH-007).

The built-in dashboards (overview, rankings, high-priority, WAN) are pages;
a custom dashboard is a metadata record, so it survives restarts, travels
with metadata backups (BAK-T01), and can be shown on a kiosk display.

- **Ownership (META-008).** Every dashboard is API-managed in this
  release; file-managed dashboards from ``/etc/vakr/dashboards.toml`` are
  a recorded gap, and the field is stored so they can arrive without a
  migration.
- **Visibility.** Always ``private``: public publication is deferred past
  the MVP (27.4), and the field is stored for it.
- **Widget schema (DASH-007).** ``widget_schema`` names the version of the
  stored widget JSON; ``vakr.domain.dashboard_codec`` upgrades older
  versions when a dashboard is read.
- **Concurrency (DB-003).** ``version`` starts at 1 and grows with each
  change; a change based on an older version is a conflict, never a silent
  overwrite.

Dependencies:
    dataclasses, datetime, enum: Frozen records.
    vakr.domain.checks, dashboard_widgets, errors, ids, ownership, timestamps.

Safety:
    Names, descriptions, and titles are display text; the record holds no
    credentials.

Example:
    >>> MIN_REFRESH_S, DEFAULT_REFRESH_S, MAX_REFRESH_S, MAX_WIDGETS
    (15, 60, 3600, 12)
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vakr.domain.checks import require_enum, require_instance, require_int, require_name
from vakr.domain.dashboard_widgets import WIDGET_TYPES, RankingWidget, Widget
from vakr.domain.errors import ValidationError
from vakr.domain.ids import DashboardId
from vakr.domain.ownership import ManagedBy
from vakr.domain.timestamps import require_utc

MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 500
MAX_ACTOR_LENGTH = 128
MAX_WIDGETS = 12
MAX_RANKING_WIDGETS = 2
"""Rankings scan samples, so a dashboard holds at most two (12.9's 2 s budget, E.31)."""
MIN_REFRESH_S = 15
DEFAULT_REFRESH_S = 60
MAX_REFRESH_S = 3600
"""Seconds between refreshes of a dashboard page; the kiosk bounds of E.31."""
MAX_DASHBOARDS = 200
"""Dashboards one installation keeps, so the list stays one bounded read (E.31)."""
WIDGET_SCHEMA = 1
"""The widget JSON version this release writes (DASH-007)."""


class Visibility(StrEnum):
    """Who may see a dashboard; only ``PRIVATE`` exists before public dashboards (27.4)."""

    PRIVATE = "private"


def require_dashboard_text(name: object, description: object) -> None:
    """Require a dashboard name and description within their bounds.

    Args:
        name: Candidate name: 1 to ``MAX_NAME_LENGTH`` characters, one line, trimmed.
        description: Candidate description: at most ``MAX_DESCRIPTION_LENGTH``.

    Returns:
        None

    Raises:
        ValidationError: If either breaks its rule.
    """
    require_name(name, "name")
    if isinstance(name, str) and len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"name must be at most {MAX_NAME_LENGTH} characters", field="name")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValidationError(
            f"description must be text of at most {MAX_DESCRIPTION_LENGTH} characters",
            field="description",
        )


def require_widgets(widgets: object) -> None:
    """Require an ordered widget list within the E.31 bounds.

    Args:
        widgets: Candidate tuple of widgets.

    Returns:
        None

    Raises:
        ValidationError: If it is not a tuple of widgets, holds more than
            ``MAX_WIDGETS``, or more than ``MAX_RANKING_WIDGETS`` rankings.
    """
    if not isinstance(widgets, tuple) or len(widgets) > MAX_WIDGETS:
        raise ValidationError(f"a dashboard holds at most {MAX_WIDGETS} widgets", field="widgets")
    for widget in widgets:
        if not isinstance(widget, WIDGET_TYPES):
            raise ValidationError("widgets must be dashboard widgets", field="widgets")
    if sum(isinstance(w, RankingWidget) for w in widgets) > MAX_RANKING_WIDGETS:
        raise ValidationError(
            f"a dashboard holds at most {MAX_RANKING_WIDGETS} ranking widgets", field="widgets"
        )


@dataclass(frozen=True, slots=True)
class Dashboard:
    """A custom dashboard (5.x Dashboard; UI-004, DASH-007).

    Attributes:
        id: Immutable UUID.
        name: Unique display name, 1 to ``MAX_NAME_LENGTH`` characters.
        description: Optional text, at most ``MAX_DESCRIPTION_LENGTH`` characters.
        widgets: Up to ``MAX_WIDGETS`` widgets, in display order.
        refresh_s: Seconds between refreshes, ``MIN_REFRESH_S`` to ``MAX_REFRESH_S``.
        managed_by: Owner (META-008); API-managed in this release.
        visibility: ``PRIVATE`` until public dashboards arrive.
        created_by: Who created it: a username, ``local:<account>``, or ``cli:<login>``.
        created_at: When (UTC).
        updated_by: Who changed it last.
        updated_at: When (UTC).
        version: Optimistic-concurrency version (DB-003).
    """

    id: DashboardId
    name: str
    description: str
    widgets: tuple[Widget, ...]
    refresh_s: int
    managed_by: ManagedBy
    visibility: Visibility
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the dashboard.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_dashboard_text(self.name, self.description)
        require_widgets(self.widgets)
        require_int(self.refresh_s, "refresh_s", minimum=MIN_REFRESH_S, maximum=MAX_REFRESH_S)
        require_instance(self.managed_by, ManagedBy, "managed_by")
        require_enum(self.visibility, Visibility, "visibility")
        for name in ("created_by", "updated_by"):
            actor = getattr(self, name)
            require_name(actor, name)
            if len(actor) > MAX_ACTOR_LENGTH:
                raise ValidationError(
                    f"{name} must be at most {MAX_ACTOR_LENGTH} characters", field=name
                )
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        require_int(self.version, "version", minimum=1)
