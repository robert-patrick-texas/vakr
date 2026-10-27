"""Read stored inventory for ``vakr inventory export``, bounded and ordered (META-010, E.38).

An export lists sites, devices, and interface annotations with their UUIDs
and owners, so a spreadsheet edit or a move to another node keeps every
identity. What it includes (E.38):

- sites and devices whose owner passes ``--owner`` (``all``, ``file``, or
  ``api``); devices that are enabled or in maintenance, plus a disabled or
  retired device whose interfaces ``inventory.toml`` still governs, so a
  ``--owner file`` export plans against storage as no changes;
- interfaces of those devices that are not retired and have an annotation,
  a vakr tag, or a comment, or that ``inventory.toml`` governs; with
  ``unannotated`` every other one too, the META-013 classification list.
  An interface whose name no file could write (empty, over 255
  characters, control characters) is left out.

Order is by name, then UUID, so the same storage gives the same bytes.
The counts are bounded by what one import file may hold (E.26), so every
export can be imported again.

Dependencies:
    vakr.inventory.reads, catalog, snapshot: Bounded reads and names.

Safety:
    Read-only. Device records name credentials, never secrets.

Example:
    >>> keeps("api", API_MANAGED), keeps("file", API_MANAGED)
    (True, False)
"""

from dataclasses import dataclass

from vakr.domain.enums import AdminState, InterfaceLifecycle, ManagedByKind
from vakr.domain.interfaces import Interface, InterfaceAnnotation
from vakr.domain.inventory import Site
from vakr.domain.ownership import API_MANAGED, ManagedBy
from vakr.inventory.catalog import group_names, tag_names
from vakr.inventory.parse import MAX_ANNOTATIONS, MAX_DEVICES
from vakr.inventory.parse_fields import MAX_NAME
from vakr.inventory.reads import all_devices, all_sites, device_interfaces
from vakr.inventory.snapshot import StoredDevice, StoredInterface
from vakr.ports.metadata import MetadataUnitOfWork

__all__ = ["API_MANAGED", "OWNERS", "ExportError", "ExportSet", "keeps", "load_export"]

OWNERS = ("all", "file", "api")
_POLLED = frozenset({AdminState.ENABLED, AdminState.MAINTENANCE})


class ExportError(ValueError):
    """The export would exceed what one import file may hold."""


@dataclass(frozen=True, slots=True)
class ExportSet:
    """What one export lists, already ordered.

    Attributes:
        owner: The ``--owner`` filter.
        sites: Sites by name.
        devices: Devices by name.
        interfaces: Interfaces by device name, then interface name.
    """

    owner: str
    sites: tuple[Site, ...]
    devices: tuple[StoredDevice, ...]
    interfaces: tuple[StoredInterface, ...]


def keeps(owner: str, managed_by: ManagedBy) -> bool:
    """Tell whether a record's owner passes the ``--owner`` filter.

    Args:
        owner: ``all``, ``file``, or ``api``.
        managed_by: The record's owner.

    Returns:
        bool: ``True`` when the record is exported.

    Raises:
        None
    """
    return owner in ("all", managed_by.kind.value)


def _listed(i: Interface, unannotated: bool) -> bool:
    """Tell whether an interface gets an annotation row.

    Args:
        i: The interface.
        unannotated: Include interfaces with nothing set.

    Returns:
        bool: ``True`` to export it.

    Raises:
        None
    """
    if i.lifecycle is InterfaceLifecycle.RETIRED or not i.if_name or len(i.if_name) > MAX_NAME:
        return False
    if not i.if_name.isprintable() or i.if_name != i.if_name.strip():
        return False
    noted = i.annotation != InterfaceAnnotation() or bool(i.tags) or bool(i.comment)
    return unannotated or noted or i.managed_by.kind is ManagedByKind.FILE


def _device(
    uow: MetadataUnitOfWork, d: StoredDevice, owner: str, unannotated: bool
) -> tuple[bool, list[StoredInterface]]:
    """Decide whether a device is exported and which of its interfaces are.

    Args:
        uow: The open unit of work.
        d: The device.
        owner: The ``--owner`` filter.
        unannotated: Include interfaces with nothing set.

    Returns:
        tuple[bool, list[StoredInterface]]: Whether the device row is
        written, and its interface rows.

    Raises:
        StorageError: If a read fails or exceeds its bound.
    """
    polled = d.device.admin_state in _POLLED
    if not polled and not d.file_managed:
        return False, []  # an ended API-managed device governs no interface
    found = [i for i in device_interfaces(uow, d.device.id) if _listed(i, unannotated)]
    governed = any(i.managed_by.kind is ManagedByKind.FILE for i in found)
    if not polled and not governed:
        return False, []
    rows = [StoredInterface(i, d.device.name, tag_names(uow, i.tags)) for i in found
            if keeps(owner, i.managed_by)]  # fmt: skip
    return keeps(owner, d.managed_by), rows


def load_export(uow: MetadataUnitOfWork, owner: str, *, unannotated: bool = False) -> ExportSet:
    """Read and order everything one export lists.

    Args:
        uow: The open unit of work.
        owner: ``all``, ``file``, or ``api``.
        unannotated: Also list interfaces with nothing set.

    Returns:
        ExportSet: Sites, devices, and interfaces in export order.

    Raises:
        ExportError: If there are more than 5,000 devices or 20,000
            interface rows (E.26).
        StorageError: If a read fails or exceeds its bound.
    """
    stored_sites = all_sites(uow)
    names = {s.id: s.name for s in stored_sites}
    sites = sorted((s for s in stored_sites if keeps(owner, s.managed_by)),
                   key=lambda s: (s.name, str(s.id)))  # fmt: skip
    devices: list[StoredDevice] = []
    interfaces: list[StoredInterface] = []
    for d in sorted(all_devices(uow), key=lambda d: (d.name, str(d.id))):
        stored = StoredDevice(d, names.get(d.site_id) if d.site_id else None,
                              tag_names(uow, d.tags), group_names(uow, d.groups))  # fmt: skip
        listed, rows = _device(uow, stored, owner, unannotated)
        devices.extend([stored] if listed else [])
        interfaces.extend(sorted(rows, key=lambda i: (i.interface.if_name, str(i.interface.id))))
        if len(devices) > MAX_DEVICES or len(interfaces) > MAX_ANNOTATIONS:
            why = f"more than {MAX_DEVICES} devices or {MAX_ANNOTATIONS} interfaces"
            raise ExportError(f"{why}; one import file holds no more (E.26); narrow with --owner")
    return ExportSet(owner, tuple(sites), tuple(devices), tuple(interfaces))
