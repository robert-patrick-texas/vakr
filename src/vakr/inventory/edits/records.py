"""What a manual edit answers, how its records are found, and its audit event (META-008, SEC-018).

``DeviceRecord`` and ``AnnotationRecord`` carry a record with its
references as names, the change lines the edit made (empty when it changed
nothing), and the warnings it raised. Devices are found by UUID or exact
name, interfaces by UUID or by device and ``ifName`` (exactly, then
case-insensitively, never a retired one), the way ``vakr interface claim``
finds them.

Dependencies:
    vakr.inventory.catalog, snapshot: Names of sites, tags, and groups.
    vakr.ports.metadata: The open unit of work.

Safety:
    Audit summaries list fields and values but never a comment, and are
    bounded (``MAX_SUMMARY``).

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> with memory_uow_factory()() as uow:
    ...     find_device(uow, "nowhere") is None
    True
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from vakr.domain.control import AuditEvent
from vakr.domain.enums import AuditOutcome, InterfaceLifecycle
from vakr.domain.errors import NotFoundError, ValidationError
from vakr.domain.ids import AuditEventId, DeviceId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.inventory.catalog import group_names, tag_names
from vakr.inventory.errors import Issue
from vakr.inventory.reads import device_interfaces
from vakr.inventory.snapshot import StoredDevice
from vakr.ports.metadata import MetadataUnitOfWork

MAX_SUMMARY = 1_000  # as inventory apply: audit summaries stay readable and bounded
FROM_VAKR = "Created in vakr."
"""Description of tags and groups a manual edit creates."""


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """A device as an edit left it.

    Attributes:
        stored: The device with its site, tag, and group names.
        changes: What the edit changed; empty when nothing did.
        warnings: Advice that did not block the edit, by field.
    """

    stored: StoredDevice
    changes: tuple[str, ...] = ()
    warnings: tuple[Issue, ...] = ()


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """An interface's owned fields as an edit left them.

    Attributes:
        interface: The interface.
        device: Its device's name.
        tags: Its vakr tag names (META-013).
        description_tags: Tags its description keywords gave it (META-012).
        changes: What the edit changed; empty when nothing did.
    """

    interface: Interface
    device: str
    tags: tuple[str, ...]
    description_tags: tuple[str, ...]
    changes: tuple[str, ...] = ()


def stored_device(uow: MetadataUnitOfWork, device: Device) -> StoredDevice:
    """Give a device its site, tag, and group names.

    Args:
        uow: The open unit of work.
        device: The device.

    Returns:
        StoredDevice: The device with names.

    Raises:
        None
    """
    site = uow.sites.get(device.site_id) if device.site_id else None
    return StoredDevice(device, site.name if site else None, tag_names(uow, device.tags),
                        group_names(uow, device.groups))  # fmt: skip


def annotation_record(uow: MetadataUnitOfWork, iface: Interface, changes: tuple[str, ...] = ()
                      ) -> AnnotationRecord:  # fmt: skip
    """Give an interface its device's name and its tag names.

    Args:
        uow: The open unit of work.
        iface: The interface.
        changes: What an edit changed.

    Returns:
        AnnotationRecord: The record.

    Raises:
        None
    """
    device = uow.devices.get(iface.device_id)
    return AnnotationRecord(iface, device.name if device else str(iface.device_id),
                            tag_names(uow, iface.tags), tag_names(uow, iface.description_tags),
                            changes)  # fmt: skip


def find_device(uow: MetadataUnitOfWork, key: str) -> Device | None:
    """Find a device by UUID or exact name, as the CLI names it.

    Args:
        uow: The open unit of work.
        key: A UUID or a name.

    Returns:
        Device | None: The device, retired or not.

    Raises:
        None
    """
    try:
        found = uow.devices.get(DeviceId(UUID(key)))
    except ValueError:
        found = None
    return found or uow.devices.get_by_name(key)


def find_interface(uow: MetadataUnitOfWork, device: Device, ifname: str) -> Interface:
    """Find a device's one live interface by ``ifName``: exactly, then case-insensitively.

    Args:
        uow: The open unit of work.
        device: The device.
        ifname: The name as the device reports it.

    Returns:
        Interface: The interface.

    Raises:
        NotFoundError: If no interface has that name.
        ValidationError: If two have it, which means an identity review is open.
    """
    live = [i for i in device_interfaces(uow, device.id)
            if i.lifecycle is not InterfaceLifecycle.RETIRED]  # fmt: skip
    found = [i for i in live if i.if_name == ifname] or [
        i for i in live if i.if_name.casefold() == ifname.casefold()
    ]
    if not found:
        raise NotFoundError("interface", f"{device.name} {ifname}")
    if len(found) > 1:
        raise ValidationError(f"{len(found)} interfaces of {device.name} are named {ifname!r}; "
                              "resolve the identity review first", field="ifname")  # fmt: skip
    return found[0]


def audit(uow: MetadataUnitOfWork, at: datetime, actor: str, action: str,
          target: tuple[str, UUID], summary: str, request_id: str | None) -> None:  # fmt: skip
    """Append the audit event of one edit (SEC-018).

    Args:
        uow: The unit of work making the change.
        at: When (UTC).
        actor: Who: a username, ``local:<account>``, or ``cli:<login>``.
        action: Such as ``device.create`` or ``interface.annotate``.
        target: The object type and its UUID.
        summary: What changed, without comments.
        request_id: The API request, if any.

    Returns:
        None

    Raises:
        ValidationError: If the actor is not a valid audit actor.
    """
    kind, object_id = target
    event = AuditEvent(new_id(AuditEventId), at, actor, action, kind, object_id,
                       AuditOutcome.SUCCESS, summary[:MAX_SUMMARY], request_id)  # fmt: skip
    uow.audit.append(event)
