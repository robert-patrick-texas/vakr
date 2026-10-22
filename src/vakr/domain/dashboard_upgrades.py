"""Widget-schema upgrades of stored dashboards (DASH-007).

Dashboards store their widgets as JSON beside the ``widget_schema`` that
wrote them. When a release changes the widget JSON, it raises
``vakr.domain.dashboards.WIDGET_SCHEMA`` and adds ``UPGRADES[n]``, a pure
function from schema-``n`` JSON to schema ``n + 1``. Reading older JSON
applies the steps in order (``vakr.domain.dashboard_codec`` calls
``upgrade`` first); the next write stores the current schema. JSON from a
newer schema than this release knows is refused rather than guessed at,
so a downgrade never silently drops widgets.

Dependencies:
    vakr.domain.dashboards, errors: The current schema and the error type.

Safety:
    Pure functions over plain JSON values.

Example:
    >>> upgrade(1, [])
    []
"""

from collections.abc import Callable
from typing import Any

from vakr.domain import dashboards
from vakr.domain.errors import ValidationError

UPGRADES: dict[int, Callable[[list[Any]], list[Any]]] = {}
"""Upgrade steps, by the schema they upgrade from (none yet: schema 1 is the first)."""


def upgrade(schema: int, widgets: list[Any]) -> list[Any]:
    """Bring widget JSON of an older schema to the current one (DASH-007).

    Args:
        schema: The version that wrote ``widgets``.
        widgets: The stored array.

    Returns:
        list[Any]: The same widgets in the current schema.

    Raises:
        ValidationError: If the schema is newer than this release, below 1, or
            has no upgrade step.
    """
    current = dashboards.WIDGET_SCHEMA  # read at call time, so a test can move it
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise ValidationError("widget_schema must be a positive integer", field="widget_schema")
    if schema > current:
        raise ValidationError(
            f"these widgets use schema {schema}, newer than this release's {current}; "
            "update vakr before reading or changing this dashboard",
            field="widget_schema",
        )
    while schema < current:
        step = UPGRADES.get(schema)
        if step is None:
            raise ValidationError(f"no upgrade from widget schema {schema}", field="widget_schema")
        widgets, schema = step(widgets), schema + 1
    return widgets
