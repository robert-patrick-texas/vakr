"""Plan records: actions, changes, waiting annotations, and change lines (META-010).

Dependencies:
    dataclasses, enum: Frozen records.

Safety:
    Pure data. A change line never echoes a comment, which may hold
    anything an operator typed.

Example:
    >>> diff([("interval_s", 60, 120), ("comment", "a", "b"), ("site", None, None)])
    ('interval_s: 60 -> 120', 'comment changed')
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from vakr.inventory.model import AnnotationEntry, DeviceEntry, SiteEntry


class Action(StrEnum):
    """What applying the plan does to one record."""

    CREATE = "create"
    UPDATE = "update"
    DISABLE = "disable"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class Change:
    """One record the plan changes.

    Attributes:
        action: What happens.
        kind: ``site``, ``device``, or ``interface``.
        name: Display name; ``device ifname`` for interfaces.
        record_id: The stored record, or ``None`` for a create.
        details: What changes, one item per field.
        entry: The file entry that asks for it; ``None`` for disable and release.
    """

    action: Action
    kind: str
    name: str
    record_id: UUID | None
    details: tuple[str, ...]
    entry: SiteEntry | DeviceEntry | AnnotationEntry | None = None


@dataclass(frozen=True, slots=True)
class Waiting:
    """An annotation that cannot apply yet.

    Attributes:
        entry: The annotation.
        reason: Why, with the command that resolves it when there is one.
        interface_id: The discovered interface, if there is exactly one.
    """

    entry: AnnotationEntry
    reason: str
    interface_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class InventoryPlan:
    """Everything applying the file would change.

    Attributes:
        source: The file name.
        changes: Changes in apply order.
        waiting: Annotations that cannot apply yet, in file order.
        unchanged: Records already matching the file.
    """

    source: str
    changes: tuple[Change, ...] = ()
    waiting: tuple[Waiting, ...] = ()
    unchanged: int = 0

    @property
    def summary(self) -> str:
        """One line counting each action.

        Args:
            None

        Returns:
            str: Such as ``2 create, 1 disable, 4 unchanged``.

        Raises:
            None
        """
        parts = [f"{sum(c.action is a for c in self.changes)} {a.value}" for a in Action
                 if any(c.action is a for c in self.changes)]  # fmt: skip
        if self.unchanged:
            parts.append(f"{self.unchanged} unchanged")
        return ", ".join(parts) if self.changes else "no changes"


def _show(value: object) -> str:
    """Render a field value for a change line.

    Args:
        value: The value.

    Returns:
        str: ``none``, ``[a, b]``, or the value.

    Raises:
        None
    """
    if value is None or value == ():
        return "none"
    if isinstance(value, tuple):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def diff(pairs: Sequence[tuple[str, object, object]]) -> tuple[str, ...]:
    """List ``field: old -> new`` for every pair that differs.

    Args:
        pairs: ``(field, stored, wanted)`` triples.

    Returns:
        tuple[str, ...]: One item per changed field; comments are not echoed.

    Raises:
        None
    """
    return tuple(
        "comment changed" if name == "comment" else f"{name}: {_show(old)} -> {_show(new)}"
        for name, old, new in pairs
        if old != new
    )
