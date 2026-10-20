"""Writes shared by ``inventory apply`` and ``interface claim`` (META-008, E.19).

Dependencies:
    vakr.inventory.catalog, plan_interfaces: Tags and the wanted annotation.

Safety:
    Writes only inside the caller's unit of work, as ``file:inventory.toml``.

Example:
    >>> found(1, "device", None)
    1
"""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from vakr.domain.errors import NotFoundError
from vakr.domain.ids import InterfaceId
from vakr.domain.interfaces import Interface, InterfaceAnnotation
from vakr.inventory.catalog import ensure_tags
from vakr.inventory.model import FILE_OWNER, AnnotationEntry
from vakr.inventory.plan_interfaces import wanted_annotation
from vakr.ports.metadata import MetadataUnitOfWork

FROM_FILE = "Created from inventory.toml."
"""Description of tags and groups the file creates."""


def found[T](record: T | None, kind: str, key: UUID | None) -> T:
    """Return a record read for a planned change, which must still exist.

    Args:
        record: The record, or ``None``.
        kind: Entity kind, for the error.
        key: Its UUID, if known.

    Returns:
        T: The record.

    Raises:
        NotFoundError: If it vanished, which nothing in vakr does (META-009).
    """
    if record is None:
        raise NotFoundError(kind, key)
    return record


def annotate(
    uow: MetadataUnitOfWork, key: InterfaceId, entry: AnnotationEntry | None, now: datetime
) -> Interface:
    """Write the owned fields of a file-governed interface.

    Args:
        uow: The open unit of work.
        key: The interface.
        entry: The annotation, or ``None`` to restore the defaults.
        now: Change time (UTC).

    Returns:
        Interface: The stored interface.

    Raises:
        DomainError: From the repository.
    """
    stored = found(uow.interfaces.get(key), "interface", key)
    changed = replace(
        stored, comment=entry.comment if entry else "",
        tags=ensure_tags(uow, entry.tags if entry else (), FILE_OWNER, FROM_FILE),
        annotation=wanted_annotation(entry) if entry else InterfaceAnnotation(),
        updated_at=max(now, stored.updated_at),
    )  # fmt: skip
    return uow.interfaces.update(changed, writer=FILE_OWNER)
