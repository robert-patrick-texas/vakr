"""Table spec for interfaces, with the interface list filter (DB-001, META-002, META-007..013).

One row per logical interface; its UUID, not ``ifIndex``, is the identity
(invariant 6). ``inactive_since`` (schema 7) holds the start of the stale
clock, so no other column's change can restart it (LIFE-003). Both tag
sets live in ``interface_tag``, told apart by ``kind``, because a
description change may replace description-derived tags but must never
remove an operator's (META-013). The filter hides retired interfaces
unless asked (LIFE-002), and a tag filter matches either kind, as the
reference adapter does.

Dependencies:
    vakr.adapters.sqlite.encoding, metadata.paging, metadata.specs.
    vakr.domain: Interface records and enums.
    vakr.ports.metadata.filters: The filter.

Safety:
    The filter becomes fixed SQL fragments with bound values only.

Example:
    >>> interface_where(None).params
    ('retired',)
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time, optional_blob
from vakr.adapters.sqlite.metadata.paging import Clause, KeyOrder, Part, every
from vakr.adapters.sqlite.metadata.specs import (
    OWNER_COLUMNS,
    Link,
    Links,
    Ref,
    Spec,
    owner_of,
    owner_values,
    site_id_of,
    tag_ids,
)
from vakr.domain.enums import InterestClass, InterfaceLifecycle
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.interfaces import INTERFACE_FIXED_FIELDS, Interface, InterfaceAnnotation
from vakr.domain.json_values import canonical_json
from vakr.ports.metadata.filters import InterfaceFilter

_INTERFACE_COLUMNS = (
    "id", "device_id", "if_index", "if_name", "if_descr", "if_alias", "if_type", "mac",
    "if_speed", "if_high_speed", "addresses", "lifecycle", "inactive_since", "comment",
    "interest", "sticky", "is_wan_circuit", "provider", "circuit_id", "contracted_bps",
    "capacity_override_bps", "site_override", "public_dashboard", "display_rank", *OWNER_COLUMNS,
    "created_at", "updated_at", "version",
)  # fmt: skip


def _encode_interface(i: Interface) -> tuple[object, ...]:
    """Encode an interface and its annotation as a row.

    Args:
        i: The interface.

    Returns:
        tuple[object, ...]: Values in ``_INTERFACE_COLUMNS`` order.

    Raises:
        None
    """
    a = i.annotation
    return (
        i.id.bytes, i.device_id.bytes, i.if_index, i.if_name, i.if_descr, i.if_alias, i.if_type,
        i.mac, i.if_speed, i.if_high_speed, canonical_json(list(i.addresses)).decode(),
        i.lifecycle.value, _optional_time(i.inactive_since), i.comment, a.interest.value,
        a.sticky, a.is_wan_circuit, a.provider, a.circuit_id, a.contracted_bps,
        a.capacity_override_bps, optional_blob(a.site_override), a.public_dashboard,
        a.display_rank, *owner_values(i.managed_by),
        encode_time(i.created_at), encode_time(i.updated_at), i.version,
    )  # fmt: skip


def _decode_interface(r: sqlite3.Row, links: Links) -> Interface:
    """Decode an interface row.

    Args:
        r: The row.
        links: Its ``tags`` and ``description_tags``.

    Returns:
        Interface: The interface.

    Raises:
        ValueError: If a stored value breaks a domain rule.
        StorageError: If an ID or timestamp column is not valid.
    """
    annotation = InterfaceAnnotation(
        interest=InterestClass(r["interest"]), sticky=bool(r["sticky"]),
        is_wan_circuit=bool(r["is_wan_circuit"]), provider=r["provider"],
        circuit_id=r["circuit_id"], contracted_bps=r["contracted_bps"],
        capacity_override_bps=r["capacity_override_bps"],
        site_override=site_id_of(r["site_override"]),
        public_dashboard=bool(r["public_dashboard"]), display_rank=r["display_rank"],
    )  # fmt: skip
    return Interface(
        id=InterfaceId(blob_to_uuid(r["id"])), device_id=DeviceId(blob_to_uuid(r["device_id"])),
        if_index=r["if_index"], if_name=r["if_name"], if_descr=r["if_descr"],
        if_alias=r["if_alias"], lifecycle=InterfaceLifecycle(r["lifecycle"]),
        managed_by=owner_of(r), created_at=decode_time(r["created_at"]),
        updated_at=decode_time(r["updated_at"]), if_type=r["if_type"], mac=r["mac"],
        if_speed=r["if_speed"], if_high_speed=r["if_high_speed"],
        addresses=tuple(_strings(r["addresses"])), comment=r["comment"],
        tags=tag_ids(links["tags"]), description_tags=tag_ids(links["description_tags"]),
        annotation=annotation, version=r["version"],
        inactive_since=None if r["inactive_since"] is None else decode_time(r["inactive_since"]),
    )  # fmt: skip


def _optional_time(value: datetime | None) -> int | None:
    """Encode an optional timestamp; ``None`` stays SQL ``NULL``.

    Args:
        value: The time, or ``None``.

    Returns:
        int | None: Microseconds since the epoch, or ``None``.

    Raises:
        None
    """
    return None if value is None else encode_time(value)


def _strings(text: str) -> list[str]:
    """Decode a JSON array of strings.

    Args:
        text: Stored JSON.

    Returns:
        list[str]: The strings.

    Raises:
        ValueError: If it is not a JSON array of strings.
    """
    value = json.loads(text)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected a JSON array of strings")
    return value


def _interface_refs(i: Interface) -> Iterable[Ref]:
    """Return an interface's device, site-override, and tag references.

    Args:
        i: The interface.

    Returns:
        Iterable[Ref]: The references.

    Raises:
        None
    """
    override = i.annotation.site_override
    site: list[Ref] = [] if override is None else [("site", override)]
    tags: frozenset[UUID] = i.tags | i.description_tags
    return [("device", i.device_id), *site, *(("tag", t) for t in tags)]


def interface_where(where: InterfaceFilter | None) -> Clause:
    """Translate an ``InterfaceFilter``; retired interfaces are hidden by default.

    Args:
        where: The filter, or ``None``.

    Returns:
        Clause: The ``WHERE`` fragment.

    Raises:
        None
    """
    w = where or InterfaceFilter()
    parts: list[Clause] = []
    if w.lifecycle is not None:
        parts.append(Clause("lifecycle = ?", (w.lifecycle.value,)))
    elif not w.include_retired:
        parts.append(Clause("lifecycle <> ?", (InterfaceLifecycle.RETIRED.value,)))
    if w.device_id is not None:
        parts.append(Clause("device_id = ?", (w.device_id.bytes,)))
    if w.interest is not None:
        parts.append(Clause("interest = ?", (w.interest.value,)))
    if w.tag_id is not None:
        member = "id IN (SELECT interface_id FROM interface_tag WHERE tag_id = ?)"
        parts.append(Clause(member, (w.tag_id.bytes,)))
    if w.is_wan_circuit is not None:
        parts.append(Clause("is_wan_circuit = ?", (int(w.is_wan_circuit),)))
    return every(parts)


INTERFACE = Spec(
    "interface", "interface", _INTERFACE_COLUMNS, _encode_interface, _decode_interface,
    order=KeyOrder(("device_id", "if_name", "id"), (Part.UUID, Part.TEXT, Part.UUID)),
    links=(Link("tags", "interface_tag", "interface_id", "tag_id", kind="operator"),
           Link("description_tags", "interface_tag", "interface_id", "tag_id",
                kind="description")),
    references=_interface_refs, where=interface_where, fixed=INTERFACE_FIXED_FIELDS,
    named=False,
)  # fmt: skip
