"""A bounded in-memory index of the inventory, reused until metadata changes (12.1, 12.9).

Global search, the global interface list, rankings, and the overview all
need the whole inventory: names to match, sites and providers to group by,
annotations to filter on. At MVP scale (100 devices, a few thousand
discovered interfaces) one bounded read of every repository is cheap, and
``MetadataUnitOfWork.change_counter`` tells when it is stale, so
``IndexCache`` keeps the last index and rereads only after a change
(collectors are not counted, so they are always reread; there are few).
Reads stop at the E.29 bounds (5,000 devices, 20,000 interfaces) and say
so with ``truncated`` rather than growing without limit.

Create one cache per process and share it; it is thread-safe.

Dependencies:
    vakr.ports.metadata: Repositories and the change counter.
    vakr.query.snapshot, settings: Bounded paging and limits.

Safety:
    Read-only. Records carry credential *names* only; views built from the
    index never expose them (TUI-010).

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> IndexCache(memory_uow_factory()).get().interfaces
    ()
"""

import threading
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from vakr.domain.catalog import Collector, Group, Tag
from vakr.domain.ids import DeviceId, GroupId, SiteId, TagId
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device, Site
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.logic.natural import natural_key
from vakr.ports.metadata import (
    DeviceFilter,
    InterfaceFilter,
    MetadataUnitOfWork,
    MetadataUnitOfWorkFactory,
)
from vakr.query.settings import QuerySettings
from vakr.query.snapshot import pages


class Named(Protocol):
    """A record with a UUID and a display name (sites, tags, groups)."""

    @property
    def id(self) -> UUID:
        """The record's UUID.

        Args:
            None

        Returns:
            UUID: The ID.

        Raises:
            None
        """
        ...

    @property
    def name(self) -> str:
        """The record's display name.

        Args:
            None

        Returns:
            str: The name.

        Raises:
            None
        """
        ...


@dataclass(frozen=True, slots=True)
class CatalogIndex:
    """Every inventory record a list, search, or ranking needs.

    Attributes:
        devices: Devices by ID, retired included.
        interfaces: Interfaces in repository order, retired included.
        sites: Sites by ID.
        tags: Tags by ID.
        groups: Groups by ID.
        collectors: Collectors in natural position order.
        truncated: True when more devices or interfaces exist than were read.
        stamp: The change counter the index was read at.
    """

    devices: dict[DeviceId, Device]
    interfaces: tuple[Interface, ...]
    sites: dict[SiteId, Site]
    tags: dict[TagId, Tag]
    groups: dict[GroupId, Group]
    collectors: tuple[Collector, ...]
    truncated: bool
    stamp: int

    def site_of(self, interface: Interface) -> Site | None:
        """Return an interface's site: its override, else its device's.

        Args:
            interface: The interface.

        Returns:
            Site | None: The site, if any.

        Raises:
            None
        """
        override = interface.annotation.site_override
        if override is not None:
            return self.sites.get(override)
        device = self.devices.get(interface.device_id)
        return self.sites.get(device.site_id) if device and device.site_id else None


def read_collectors(uow: MetadataUnitOfWork) -> tuple[Collector, ...]:
    """Read every collector in natural position order.

    Args:
        uow: An open unit of work.

    Returns:
        tuple[Collector, ...]: The collectors.

    Raises:
        StorageError: If the store cannot be read.
    """
    found, _ = pages(uow.collectors.list_page, MAX_PAGE_LIMIT)
    return tuple(sorted(found, key=lambda c: (natural_key(c.position), c.name, str(c.id))))


def read_index(uow: MetadataUnitOfWork, settings: QuerySettings, stamp: int) -> CatalogIndex:
    """Read the bounded index in one unit of work.

    Args:
        uow: An open unit of work; nothing is written.
        settings: Read bounds (E.29).
        stamp: The change counter seen in this unit of work.

    Returns:
        CatalogIndex: The index.

    Raises:
        StorageError: If the store cannot be read.
    """
    limits = settings.limits
    devices, more_devices = pages(
        lambda p: uow.devices.list_page(p, DeviceFilter(include_retired=True)), limits.max_devices
    )
    interfaces, more_interfaces = pages(
        lambda p: uow.interfaces.list_page(p, InterfaceFilter(include_retired=True)),
        limits.max_interfaces_counted,
    )
    sites, _ = pages(uow.sites.list_page, limits.max_devices)
    tags, _ = pages(uow.tags.list_page, limits.max_devices)
    groups, _ = pages(uow.groups.list_page, limits.max_devices)
    return CatalogIndex(
        {d.id: d for d in devices},
        tuple(interfaces),
        {s.id: s for s in sites},
        {t.id: t for t in tags},
        {g.id: g for g in groups},
        read_collectors(uow),
        more_devices or more_interfaces,
        stamp,
    )


class IndexCache:
    """The last ``CatalogIndex``, reread when the change counter moves."""

    def __init__(
        self, metadata: MetadataUnitOfWorkFactory, settings: QuerySettings | None = None
    ) -> None:
        """Bind the cache.

        Args:
            metadata: Opens a metadata unit of work.
            settings: Read bounds; defaults when ``None``.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._settings = settings or QuerySettings()
        self._lock = threading.Lock()
        self._index: CatalogIndex | None = None

    def get(self) -> CatalogIndex:
        """Return a current index, rereading only after a metadata change.

        Args:
            None

        Returns:
            CatalogIndex: The index, with collectors always fresh.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            stamp = uow.change_counter()
            with self._lock:
                cached = self._index
            if cached is not None and cached.stamp == stamp:
                index = CatalogIndex(
                    cached.devices,
                    cached.interfaces,
                    cached.sites,
                    cached.tags,
                    cached.groups,
                    read_collectors(uow),
                    cached.truncated,
                    stamp,
                )
            else:
                index = read_index(uow, self._settings, stamp)
        with self._lock:
            self._index = index
        return index
