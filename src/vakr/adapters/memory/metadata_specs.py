"""Per-table rules for the in-memory metadata adapter: order, filters, references.

These are the in-memory equivalents of an SQL adapter's ``ORDER BY``,
``WHERE``, and foreign keys, written once per table so every repository
behaves as ``vakr.ports.metadata.repository`` documents.

Dependencies:
    vakr.domain: Records and enums.
    vakr.ports.metadata.filters: The filter types.

Safety:
    Pure functions over frozen records.

Example:
    >>> DEVICE.table, POLICY.named
    ('device', True)
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from vakr.domain.enums import AdminState, InterfaceLifecycle
from vakr.domain.interfaces import INTERFACE_FIXED_FIELDS, Interface
from vakr.domain.inventory import DEVICE_FIXED_FIELDS, RECORD_FIXED_FIELDS, Device, Site
from vakr.domain.paging import CursorKey
from vakr.ports.metadata.filters import DeviceFilter, InterfaceFilter

type Ref = tuple[str, UUID]
"""A reference to another record: ``(table, id)``."""


@dataclass(frozen=True, slots=True)
class TableSpec:
    """How one table orders, filters, and references.

    Attributes:
        table: Table name in ``MemoryMetadataStore.tables``.
        named: Whether ``name`` is a unique natural key.
        sort_key: Total list order; ends with the record ID.
        matches: ``(record, where)`` to whether the record is listed.
        references: Records this record points at, which must exist.
        fixed: Fields ``update`` may not change.
    """

    table: str
    named: bool
    sort_key: Callable[[Any], CursorKey]
    matches: Callable[[Any, Any], bool]
    references: Callable[[Any], Iterable[Ref]]
    fixed: tuple[str, ...]


def _by_name(record: Any) -> CursorKey:
    """Sort key ``(name, id)``.

    Args:
        record: A named record.

    Returns:
        CursorKey: The key.

    Raises:
        None
    """
    return (record.name, str(record.id))


def _any(_record: Any, _where: Any) -> bool:
    """Match every record; the filter of unfilterable tables.

    Args:
        _record: Any record.
        _where: Ignored filter.

    Returns:
        bool: ``True``.

    Raises:
        None
    """
    return True


def _none(_record: Any) -> Iterable[Ref]:
    """Reference nothing.

    Args:
        _record: Any record.

    Returns:
        Iterable[Ref]: Empty.

    Raises:
        None
    """
    return ()


def _device_matches(device: Device, where: DeviceFilter | None) -> bool:
    """Apply a ``DeviceFilter``; retired devices are hidden by default.

    Args:
        device: The device.
        where: The filter, or ``None``.

    Returns:
        bool: Whether the device is listed.

    Raises:
        None
    """
    w = where or DeviceFilter()
    if w.admin_state is not None and device.admin_state is not w.admin_state:
        return False
    if w.admin_state is None and not w.include_retired and device.admin_state is AdminState.RETIRED:
        return False
    return (
        (w.site_id is None or device.site_id == w.site_id)
        and (w.tag_id is None or w.tag_id in device.tags)
        and (w.group_id is None or w.group_id in device.groups)
    )


def _interface_matches(interface: Interface, where: InterfaceFilter | None) -> bool:
    """Apply an ``InterfaceFilter``; retired interfaces are hidden by default.

    Args:
        interface: The interface.
        where: The filter, or ``None``.

    Returns:
        bool: Whether the interface is listed.

    Raises:
        None
    """
    w = where or InterfaceFilter()
    retired = interface.lifecycle is InterfaceLifecycle.RETIRED
    if w.lifecycle is not None and interface.lifecycle is not w.lifecycle:
        return False
    if w.lifecycle is None and not w.include_retired and retired:
        return False
    tags = interface.tags | interface.description_tags
    wan = interface.annotation.is_wan_circuit
    return (
        (w.device_id is None or interface.device_id == w.device_id)
        and (w.interest is None or interface.annotation.interest is w.interest)
        and (w.tag_id is None or w.tag_id in tags)
        and (w.is_wan_circuit is None or wan is w.is_wan_circuit)
    )


def _device_refs(device: Device) -> Iterable[Ref]:
    """Return a device's site, tag, and group references.

    Args:
        device: The device.

    Returns:
        Iterable[Ref]: The references.

    Raises:
        None
    """
    site = [("site", device.site_id)] if device.site_id is not None else []
    return [*site, *(("tag", t) for t in device.tags), *(("group", g) for g in device.groups)]


def _interface_refs(interface: Interface) -> Iterable[Ref]:
    """Return an interface's device, tag, and site-override references.

    Args:
        interface: The interface.

    Returns:
        Iterable[Ref]: The references.

    Raises:
        None
    """
    override = interface.annotation.site_override
    site = [("site", override)] if override is not None else []
    tags = interface.tags | interface.description_tags
    return [("device", interface.device_id), *site, *(("tag", t) for t in tags)]


def _site_refs(site: Site) -> Iterable[Ref]:
    """Return a site's tag references.

    Args:
        site: The site.

    Returns:
        Iterable[Ref]: The references.

    Raises:
        None
    """
    return [("tag", t) for t in site.tags]


DEVICE = TableSpec("device", True, _by_name, _device_matches, _device_refs, DEVICE_FIXED_FIELDS)
INTERFACE = TableSpec(
    "interface",
    False,
    lambda i: (str(i.device_id), i.if_name, str(i.id)),
    _interface_matches,
    _interface_refs,
    INTERFACE_FIXED_FIELDS,
)
SITE = TableSpec("site", True, _by_name, _any, _site_refs, RECORD_FIXED_FIELDS)
TAG = TableSpec("tag", True, _by_name, _any, _none, RECORD_FIXED_FIELDS)
GROUP = TableSpec("group", True, _by_name, _any, _none, RECORD_FIXED_FIELDS)
POLICY = TableSpec(
    "policy", True, lambda p: (p.order, p.name, str(p.id)), _any, _none, RECORD_FIXED_FIELDS
)
COLLECTOR = TableSpec("collector", True, _by_name, _any, _none, RECORD_FIXED_FIELDS)
