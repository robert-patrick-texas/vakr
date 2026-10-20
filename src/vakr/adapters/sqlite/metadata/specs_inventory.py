"""Table spec for devices, with the device list filter (DB-001, META-001, META-003..005).

Devices keep tags and group memberships in ``device_tag`` and
``device_group_member``. The filter hides retired devices unless asked
(7.5, LIFE-002). Interfaces are in ``specs_interface``.

Dependencies:
    vakr.adapters.sqlite.connection, metadata.paging, metadata.specs.
    vakr.domain: Device and interface records and enums.
    vakr.ports.metadata.filters: The filters.

Safety:
    Filters become fixed SQL fragments with bound values only.

Example:
    >>> device_where(None).sql, device_where(None).params
    ('(admin_state <> ?)', ('retired',))
"""

import sqlite3
from collections.abc import Iterable

from vakr.adapters.sqlite.encoding import (
    blob_to_uuid,
    decode_time,
    encode_time,
    optional_blob,
)
from vakr.adapters.sqlite.metadata.paging import Clause, every
from vakr.adapters.sqlite.metadata.specs import (
    OWNER_COLUMNS,
    Link,
    Links,
    Ref,
    Spec,
    enum_value,
    owner_of,
    owner_values,
    site_id_of,
    tag_ids,
)
from vakr.domain.enums import AddressFamily, AdminState, ReachabilityStrategy
from vakr.domain.ids import DeviceId, GroupId
from vakr.domain.inventory import DEVICE_FIXED_FIELDS, Device
from vakr.ports.metadata.filters import DeviceFilter

_DEVICE_COLUMNS = (
    "id", "name", "poll_address", "address_family", "site_id", "admin_state", "poll_interval_s",
    "discovery_interval_s", "reachability", "credential_ref", "comment", *OWNER_COLUMNS,
    "created_at", "updated_at", "version",
)  # fmt: skip


def _encode_device(d: Device) -> tuple[object, ...]:
    """Encode a device as a row.

    Args:
        d: The device.

    Returns:
        tuple[object, ...]: Values in ``_DEVICE_COLUMNS`` order.

    Raises:
        None
    """
    return (
        d.id.bytes, d.name, d.poll_address, enum_value(d.address_family), optional_blob(d.site_id),
        d.admin_state.value, d.poll_interval_s, d.discovery_interval_s, enum_value(d.reachability),
        d.credential_ref, d.comment, *owner_values(d.managed_by), encode_time(d.created_at),
        encode_time(d.updated_at), d.version,
    )  # fmt: skip


def _decode_device(r: sqlite3.Row, links: Links) -> Device:
    """Decode a device row.

    Args:
        r: The row.
        links: Its ``tags`` and ``groups``.

    Returns:
        Device: The device.

    Raises:
        ValueError: If a stored value breaks a domain rule.
        StorageError: If an ID or timestamp column is not valid.
    """
    family, reach = r["address_family"], r["reachability"]
    return Device(
        id=DeviceId(blob_to_uuid(r["id"])), name=r["name"], poll_address=r["poll_address"],
        managed_by=owner_of(r), created_at=decode_time(r["created_at"]),
        updated_at=decode_time(r["updated_at"]),
        address_family=None if family is None else AddressFamily(family),
        site_id=site_id_of(r["site_id"]), admin_state=AdminState(r["admin_state"]),
        poll_interval_s=r["poll_interval_s"], discovery_interval_s=r["discovery_interval_s"],
        reachability=None if reach is None else ReachabilityStrategy(reach),
        credential_ref=r["credential_ref"], comment=r["comment"], tags=tag_ids(links["tags"]),
        groups=frozenset(map(GroupId, links["groups"])), version=r["version"],
    )  # fmt: skip


def _device_refs(d: Device) -> Iterable[Ref]:
    """Return a device's site, tag, and group references.

    Args:
        d: The device.

    Returns:
        Iterable[Ref]: The references.

    Raises:
        None
    """
    site: list[Ref] = [] if d.site_id is None else [("site", d.site_id)]
    return [*site, *(("tag", t) for t in d.tags), *(("group", g) for g in d.groups)]


def device_where(where: DeviceFilter | None) -> Clause:
    """Translate a ``DeviceFilter``; retired devices are hidden by default.

    Args:
        where: The filter, or ``None``.

    Returns:
        Clause: The ``WHERE`` fragment.

    Raises:
        None
    """
    w = where or DeviceFilter()
    parts: list[Clause] = []
    if w.admin_state is not None:
        parts.append(Clause("admin_state = ?", (w.admin_state.value,)))
    elif not w.include_retired:
        parts.append(Clause("admin_state <> ?", (AdminState.RETIRED.value,)))
    if w.site_id is not None:
        parts.append(Clause("site_id = ?", (w.site_id.bytes,)))
    if w.tag_id is not None:
        member = "id IN (SELECT device_id FROM device_tag WHERE tag_id = ?)"
        parts.append(Clause(member, (w.tag_id.bytes,)))
    if w.group_id is not None:
        member = "id IN (SELECT device_id FROM device_group_member WHERE group_id = ?)"
        parts.append(Clause(member, (w.group_id.bytes,)))
    return every(parts)


DEVICE = Spec(
    "device", "device", _DEVICE_COLUMNS, _encode_device, _decode_device,
    links=(Link("tags", "device_tag", "device_id", "tag_id"),
           Link("groups", "device_group_member", "device_id", "group_id")),
    references=_device_refs, where=device_where, fixed=DEVICE_FIXED_FIELDS,
)  # fmt: skip
