"""What metadata storage holds, in the terms ``inventory.toml`` uses (META-010).

The file names sites, tags, and groups; storage holds UUIDs. A snapshot
reads, in one unit of work, every site, every device with its site, tag,
and group *names*, and the interfaces of every device the file manages or
annotates, so validation and the plan compare like with like and never
query storage again.

Dependencies:
    vakr.inventory.catalog, reads: Name lookups and bounded lists.

Safety:
    Read-only.

Example:
    >>> EMPTY.device_named("edge1") is None
    True
"""

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device, ManagedBy, Site
from vakr.inventory.catalog import group_names, tag_names
from vakr.inventory.model import FILE_OWNER, InventoryFile
from vakr.inventory.reads import all_devices, all_sites, device_interfaces
from vakr.ports.metadata import MetadataUnitOfWork


@dataclass(frozen=True, slots=True)
class StoredDevice:
    """A stored device with its references as names.

    Attributes:
        device: The record.
        site: Its site's name, if any.
        tags: Tag names, sorted.
        groups: Group names, sorted.
    """

    device: Device
    site: str | None
    tags: tuple[str, ...]
    groups: tuple[str, ...]

    @property
    def id(self) -> UUID:
        """The device's UUID.

        Args:
            None

        Returns:
            UUID: ``device.id``.

        Raises:
            None
        """
        return self.device.id

    @property
    def managed_by(self) -> ManagedBy:
        """The device's owner.

        Args:
            None

        Returns:
            ManagedBy: ``device.managed_by``.

        Raises:
            None
        """
        return self.device.managed_by

    @property
    def file_managed(self) -> bool:
        """Whether ``inventory.toml`` owns the device.

        Args:
            None

        Returns:
            bool: ``True`` for ``file:inventory.toml``.

        Raises:
            None
        """
        return self.device.managed_by == FILE_OWNER


@dataclass(frozen=True, slots=True)
class StoredInterface:
    """A stored interface with its device's name and its operator tags as names.

    Attributes:
        interface: The record.
        device: The device's name.
        tags: Operator tag names, sorted.
    """

    interface: Interface
    device: str
    tags: tuple[str, ...]


class Snapshot:
    """Stored sites, devices, and the interfaces inventory may govern.

    Attributes:
        sites: Every site.
        devices: Every device, retired ones included.
        interfaces: Interfaces of file-managed or annotated devices.
    """

    def __init__(
        self,
        sites: tuple[Site, ...] = (),
        devices: tuple[StoredDevice, ...] = (),
        interfaces: tuple[StoredInterface, ...] = (),
    ) -> None:
        """Keep the records and index them by ID and name.

        Args:
            sites: Every site.
            devices: Every device.
            interfaces: Governable interfaces.

        Returns:
            None

        Raises:
            None
        """
        self.sites, self.devices, self.interfaces = sites, devices, interfaces
        self._sites: dict[UUID | str, Site] = {s.id: s for s in sites} | {s.name: s for s in sites}
        self._devices: dict[UUID | str, StoredDevice] = {d.device.id: d for d in devices} | {
            d.device.name: d for d in devices
        }
        self._interfaces = {i.interface.id: i for i in interfaces}
        self._by_device: dict[UUID, list[StoredInterface]] = {}
        for i in interfaces:
            self._by_device.setdefault(i.interface.device_id, []).append(i)

    def site(self, key: UUID | str) -> Site | None:
        """Return a site by UUID or name.

        Args:
            key: UUID or name.

        Returns:
            Site | None: The site.

        Raises:
            None
        """
        return self._sites.get(key)

    def device_named(self, key: UUID | str) -> StoredDevice | None:
        """Return a device by UUID or name.

        Args:
            key: UUID or name.

        Returns:
            StoredDevice | None: The device.

        Raises:
            None
        """
        return self._devices.get(key)

    def interface(self, key: UUID) -> StoredInterface | None:
        """Return a read interface by UUID.

        Args:
            key: The interface UUID.

        Returns:
            StoredInterface | None: The interface, if it was read.

        Raises:
            None
        """
        return self._interfaces.get(key)

    def interfaces_of(self, device_id: UUID) -> tuple[StoredInterface, ...]:
        """Return the read interfaces of one device, in storage order.

        Args:
            device_id: The device.

        Returns:
            tuple[StoredInterface, ...]: Its interfaces; empty when none were read.

        Raises:
            None
        """
        return tuple(self._by_device.get(device_id, ()))


EMPTY = Snapshot()
"""The snapshot of a node with no metadata database yet."""


def load_snapshot(
    uow: MetadataUnitOfWork, inventory: InventoryFile | None, also: Collection[UUID] = ()
) -> Snapshot:
    """Read what the plan compares against, in one unit of work.

    Args:
        uow: The open unit of work.
        inventory: The file, whose annotated devices' interfaces are read too.
        also: Further devices whose interfaces are read (an import that
            renames a device and annotates it under the new name).

    Returns:
        Snapshot: Stored sites, devices, and governable interfaces.

    Raises:
        StorageError: If the store fails or a list exceeds its bound.
    """
    sites = all_sites(uow)
    site_names = {s.id: s.name for s in sites}
    devices = tuple(
        StoredDevice(d, site_names.get(d.site_id) if d.site_id else None,
                     tag_names(uow, d.tags), group_names(uow, d.groups))
        for d in all_devices(uow)
    )  # fmt: skip
    annotated = {a.device for a in inventory.annotations} if inventory else set()
    interfaces = []
    for stored in devices:
        if stored.file_managed or stored.device.name in annotated or stored.id in also:
            for i in device_interfaces(uow, stored.device.id):
                interfaces.append(StoredInterface(i, stored.device.name, tag_names(uow, i.tags)))
    return Snapshot(sites, devices, tuple(interfaces))
