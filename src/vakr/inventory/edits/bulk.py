"""Bulk tag changes: bounds, the outcome, and the file-ownership refusal (META-013, E.37).

``AnnotationEditor.tag`` checks a change here before it reads anything, and
refuses the whole change when ``inventory.toml`` governs any interface it
names, listing each one by its position in the request.

Dependencies:
    vakr.inventory.edits: Fields, ownership, records.

Safety:
    Pure checks, plus reads inside the caller's unit of work.

Example:
    >>> MAX_BULK_INTERFACES
    500
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from vakr.domain.errors import ValidationError
from vakr.domain.ids import InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.ownership import API_MANAGED
from vakr.inventory.edits.fields import parse_annotation
from vakr.inventory.edits.ownership import FileManagedError
from vakr.inventory.edits.records import annotation_record
from vakr.inventory.errors import InventoryError
from vakr.ports.metadata import MetadataUnitOfWork

MAX_BULK_INTERFACES = 500
"""Interfaces one tag change may name: the largest list page (E.19, E.37)."""


@dataclass(frozen=True, slots=True)
class TagOutcome:
    """What a bulk tag change did.

    Attributes:
        changed: Interfaces whose vakr tags changed, in request order.
        unchanged: Interfaces that already had the result.
    """

    changed: tuple[InterfaceId, ...]
    unchanged: tuple[InterfaceId, ...]


def labels(names: Sequence[str], field: str) -> tuple[str, ...]:
    """Check tag names with the file's label rule.

    Args:
        names: Tag names.
        field: ``add`` or ``remove``.

    Returns:
        tuple[str, ...]: The names, sorted and without repeats.

    Raises:
        InventoryError: If a name breaks the rule or there are too many.
    """
    try:
        return parse_annotation({"tags": list(names)}, field).tags
    except InventoryError as exc:
        raise InventoryError(field, [replace(i, path=field) for i in exc.issues]) from None


def check_bulk(ids: Sequence[InterfaceId], add: tuple[str, ...], remove: tuple[str, ...]
                ) -> None:  # fmt: skip
    """Check a bulk tag change's bounds before reading anything.

    Args:
        ids: The interfaces.
        add: Tags to add.
        remove: Tags to remove.

    Returns:
        None

    Raises:
        ValidationError: As ``AnnotationEditor.tag`` documents.
    """
    if not 1 <= len(ids) <= MAX_BULK_INTERFACES:
        raise ValidationError(f"name 1 to {MAX_BULK_INTERFACES} interfaces", field="interfaces")
    if len(set(ids)) != len(ids):
        raise ValidationError("an interface is named twice", field="interfaces")
    if not add and not remove:
        raise ValidationError("name a tag to add or to remove", field="add")
    if both := sorted(set(add) & set(remove)):
        raise ValidationError(f"added and removed at once: {', '.join(both)}", field="remove")


def require_api_managed(uow: MetadataUnitOfWork, found: Sequence[Interface]) -> None:
    """Refuse a bulk change that names an interface a file governs, naming the first.

    Args:
        uow: The open unit of work.
        found: The interfaces, in request order.

    Returns:
        None

    Raises:
        FileManagedError: Listing ``interfaces[n]`` for each one.
    """
    owned = [(n, i) for n, i in enumerate(found) if i.managed_by != API_MANAGED]
    if owned:
        first = annotation_record(uow, owned[0][1])
        name = f"{first.device} {owned[0][1].if_name}" + (
            f" and {len(owned) - 1} more" if len(owned) > 1 else "")  # fmt: skip
        raise FileManagedError("interface", name, owned[0][1].managed_by,
                               [f"interfaces[{n}]" for n, _ in owned])  # fmt: skip
