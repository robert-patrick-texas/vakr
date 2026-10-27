"""``AnnotationEditor``: annotate an interface and tag many at once, audited (META-007, META-013).

- ``annotate`` merges a change over an interface's owned fields: interest
  (``high_priority`` is its alias, META-011), sticky, WAN circuit,
  provider, circuit ID, contracted rate, capacity override, comment, and
  vakr tags. The file's ``[[interface_annotations]]`` rules apply. A
  change based on an older ``version`` is a conflict (DB-003); an
  interface ``inventory.toml`` governs (claimed, E.19) is refused naming
  the file.
- ``tag`` adds and removes vakr tags on up to ``MAX_BULK_INTERFACES``
  interfaces in one unit of work, all or nothing (the web UI's bulk
  select-and-tag and ``vakr interface tag``). A tag change is incremental,
  so it needs no version: it cannot undo someone else's edit.

vakr tags are stored apart from description-derived tags (META-013): a
description change never removes them, and removing a tag here never
touches the description's. Fields the edit does not name keep their
values, including ``site_override``, which no edit sets.

Dependencies:
    vakr.inventory.edits: Fields, ownership, records.
    vakr.inventory.catalog: Tags by name.

Safety:
    Synchronous: the API calls it in a worker thread. Bounded.

Example:
    >>> AnnotationEditor(factory, SystemClock()).get(interface_id)  # doctest: +SKIP
"""

from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from typing import Any

from vakr.domain.errors import NotFoundError
from vakr.domain.ids import InterfaceId, TagId
from vakr.domain.interfaces import Interface
from vakr.domain.ownership import API_MANAGED
from vakr.inventory.catalog import ensure_tags
from vakr.inventory.edits.bulk import TagOutcome, check_bulk, labels, require_api_managed
from vakr.inventory.edits.fields import (
    ANNOTATION_KEYS,
    annotation_table,
    changed_field,
    merge_annotation,
    only_keys,
    parse_annotation,
)
from vakr.inventory.edits.ownership import require_api_owner
from vakr.inventory.edits.records import FROM_VAKR, AnnotationRecord, annotation_record, audit
from vakr.inventory.plan_interfaces import wanted_annotation
from vakr.inventory.plan_model import diff
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory


def _found(uow: MetadataUnitOfWork, interface_id: InterfaceId) -> Interface:
    """Read an interface that must exist.

    Args:
        uow: The open unit of work.
        interface_id: The interface.

    Returns:
        Interface: The interface.

    Raises:
        NotFoundError: If there is no such interface.
    """
    iface = uow.interfaces.get(interface_id)
    if iface is None:
        raise NotFoundError("interface", interface_id)
    return iface


class AnnotationEditor:
    """Manual interface annotation writes and the record reads they need."""

    def __init__(self, metadata: MetadataUnitOfWorkFactory, clock: Clock) -> None:
        """Bind the editor.

        Args:
            metadata: Opens metadata units of work.
            clock: Time source for ``updated_at`` and the audit events.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._clock = clock

    def get(self, interface_id: InterfaceId) -> AnnotationRecord | None:
        """Return an interface's owned fields.

        Args:
            interface_id: The interface.

        Returns:
            AnnotationRecord | None: The record, or ``None`` when there is no such interface.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            iface = uow.interfaces.get(interface_id)
            return annotation_record(uow, iface) if iface else None

    def annotate(self, interface_id: InterfaceId, changes: Mapping[str, Any], *,
                 version: int | None, actor: str, request_id: str | None = None
                 ) -> AnnotationRecord:  # fmt: skip
        """Change an interface's annotation, if it is API-managed and still at ``version``.

        Args:
            interface_id: The interface.
            changes: ``ANNOTATION_KEYS`` that change; ``None`` clears a field.
            version: The version the change is based on; ``None`` is the stored one (CLI).
            actor: Who changes it.
            request_id: The API request, if any.

        Returns:
            AnnotationRecord: The interface, one version later unless nothing changed.

        Raises:
            NotFoundError: No such interface (404).
            FileManagedError: ``inventory.toml`` governs it (409 ``managed_by_file``).
            ConflictError: Someone changed it since ``version`` (409).
            InventoryError: A field problem (422).
        """
        with self._metadata() as uow:
            iface = _found(uow, interface_id)
            before = annotation_record(uow, iface)
            table = merge_annotation(annotation_table(iface, before.tags),
                                     only_keys(changes, ANNOTATION_KEYS))  # fmt: skip
            entry = parse_annotation(table)
            want = replace(wanted_annotation(entry), site_override=iface.annotation.site_override)
            have = iface.annotation
            details = diff([*((f.name, getattr(have, f.name), getattr(want, f.name))
                              for f in fields(have)),
                            ("tags", before.tags, entry.tags),
                            ("comment", iface.comment, entry.comment)])  # fmt: skip
            name = f"{before.device} {iface.if_name or iface.if_descr}"
            require_api_owner("interface", name, iface.managed_by,
                              [changed_field(d) for d in details])  # fmt: skip
            if not details:
                return before
            at = max(self._clock.now_utc(), iface.updated_at)
            new = uow.interfaces.update(replace(
                iface, comment=entry.comment, annotation=want, updated_at=at,
                tags=ensure_tags(uow, entry.tags, API_MANAGED, FROM_VAKR),
                version=iface.version if version is None else version,
            ), writer=API_MANAGED)  # fmt: skip
            audit(uow, at, actor, "interface.annotate", ("interface", new.id),
                  f"{name}: {'; '.join(details)}", request_id)  # fmt: skip
            record = annotation_record(uow, new, details)
            uow.commit()
        return record

    def tag(self, interface_ids: Sequence[InterfaceId], *, add: Sequence[str] = (),
            remove: Sequence[str] = (), actor: str, request_id: str | None = None
            ) -> TagOutcome:  # fmt: skip
        """Add and remove vakr tags on many interfaces at once, all or nothing (META-013).

        Args:
            interface_ids: 1 to ``MAX_BULK_INTERFACES`` distinct interfaces.
            add: Tag names to add; created API-managed when missing.
            remove: Tag names to remove; a name no interface has is ignored.
            actor: Who tags them.
            request_id: The API request, if any.

        Returns:
            TagOutcome: Which interfaces changed.

        Raises:
            ValidationError: Too many or too few interfaces, nothing to do, or a
                name both added and removed (422).
            InventoryError: A tag name breaks the label rule (422).
            NotFoundError: An interface does not exist (404).
            FileManagedError: ``inventory.toml`` governs some of them (409).
        """
        adding, removing = labels(add, "add"), labels(remove, "remove")
        check_bulk(interface_ids, adding, removing)
        with self._metadata() as uow:
            found = [_found(uow, i) for i in interface_ids]
            require_api_managed(uow, found)
            plus = ensure_tags(uow, adding, API_MANAGED, FROM_VAKR) if adding else frozenset()
            minus = {t.id for n in removing if (t := uow.tags.get_by_name(n)) is not None}
            changed, same = [], []
            for iface in found:
                tags = (iface.tags | plus) - minus
                if tags == iface.tags:
                    same.append(iface.id)
                    continue
                self._retag(uow, iface, tags, actor, request_id)
                changed.append(iface.id)
            uow.commit()
        return TagOutcome(tuple(changed), tuple(same))

    def _retag(self, uow: MetadataUnitOfWork, iface: Interface, tags: frozenset[TagId],
               actor: str, request_id: str | None) -> None:  # fmt: skip
        """Store one interface's new vakr tags with its audit event.

        Args:
            uow: The open unit of work.
            iface: The interface as read.
            tags: Its new tag IDs.
            actor: Who tags it.
            request_id: The API request, if any.

        Returns:
            None

        Raises:
            ConflictError: If another writer changed it inside this unit of work's window.
        """
        before = annotation_record(uow, iface)
        at = max(self._clock.now_utc(), iface.updated_at)
        new = uow.interfaces.update(replace(iface, tags=tags, updated_at=at), writer=API_MANAGED)
        after = annotation_record(uow, new)
        line = diff([("tags", before.tags, after.tags)])
        audit(uow, at, actor, "interface.tag", ("interface", iface.id),
              f"{before.device} {iface.if_name or iface.if_descr}: {'; '.join(line)}",
              request_id)  # fmt: skip
