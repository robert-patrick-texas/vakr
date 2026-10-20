"""Bounded whole-list reads over paged metadata repositories (API-005, E.26).

Repositories return pages of at most 500 records. Inventory planning and
reconciliation need whole lists (every device, every interface of one
device), so these helpers follow cursors up to a fixed record bound and
refuse to go further: an unbounded loop over storage would turn a corrupt
or runaway table into an unbounded memory allocation (blueprint 17).

Dependencies:
    vakr.domain.paging, vakr.ports.metadata: Pages and filters.

Safety:
    Read-only.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> with memory_uow_factory()() as uow:
    ...     all_devices(uow)
    ()
"""

from collections.abc import Callable

from vakr.domain.errors import StorageError
from vakr.domain.ids import DeviceId
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device, Site
from vakr.domain.paging import Page, PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.ports.metadata import (
    DeviceFilter,
    InterfaceFilter,
    MetadataUnitOfWork,
    ReviewFilter,
)

PAGE = 500
"""Records per page read (the E.19 page maximum)."""

MAX_DEVICES = 10_000
"""Most devices one list returns (E.26): 2.5 times the 4,000-device design point."""

MAX_INTERFACES_PER_DEVICE = 200_000
"""Most stored interfaces read for one device (E.26): twice the largest walk (E.23)."""

MAX_SITES = 10_000
"""Most sites one list returns (E.26)."""


def all_pages[T](fetch: Callable[[PageRequest], Page[T]], limit: int, what: str) -> tuple[T, ...]:
    """Follow cursors until the list ends, refusing more than ``limit`` records.

    Args:
        fetch: Returns one page for a request.
        limit: Most records accepted.
        what: What is listed, for the error message.

    Returns:
        tuple[T, ...]: Every record, in the repository's order.

    Raises:
        StorageError: If the list holds more than ``limit`` records.
    """
    items: list[T] = []
    request = PageRequest(limit=PAGE)
    while True:
        page = fetch(request)
        items.extend(page.items)
        if len(items) > limit:
            raise StorageError(f"more than {limit} {what}; refusing to read further",
                               retryable=False)  # fmt: skip
        if page.next_cursor is None:
            return tuple(items)
        request = PageRequest(limit=PAGE, cursor=page.next_cursor)


def all_devices(uow: MetadataUnitOfWork, *, include_retired: bool = True) -> tuple[Device, ...]:
    """Return every device, ordered by ``(name, id)``.

    Args:
        uow: The open unit of work.
        include_retired: Include ``RETIRED`` devices.

    Returns:
        tuple[Device, ...]: The devices.

    Raises:
        StorageError: If there are more than ``MAX_DEVICES``.
    """
    where = DeviceFilter(include_retired=include_retired)
    return all_pages(lambda p: uow.devices.list_page(p, where), MAX_DEVICES, "devices")


def all_sites(uow: MetadataUnitOfWork) -> tuple[Site, ...]:
    """Return every site, ordered by ``(name, id)``.

    Args:
        uow: The open unit of work.

    Returns:
        tuple[Site, ...]: The sites.

    Raises:
        StorageError: If there are more than ``MAX_SITES``.
    """
    return all_pages(uow.sites.list_page, MAX_SITES, "sites")


def device_interfaces(uow: MetadataUnitOfWork, device_id: DeviceId) -> tuple[Interface, ...]:
    """Return every stored interface of a device, retired ones included.

    Args:
        uow: The open unit of work.
        device_id: The device.

    Returns:
        tuple[Interface, ...]: Ordered ``(device_id, if_name, id)``.

    Raises:
        StorageError: If there are more than ``MAX_INTERFACES_PER_DEVICE``.
    """
    where = InterfaceFilter(device_id=device_id, include_retired=True)
    return all_pages(
        lambda p: uow.interfaces.list_page(p, where), MAX_INTERFACES_PER_DEVICE, "interfaces"
    )


def open_reviews(uow: MetadataUnitOfWork, device_id: DeviceId | None) -> tuple[IdentityReview, ...]:
    """Return the open identity reviews of one device, or of every device.

    Args:
        uow: The open unit of work.
        device_id: The device, or ``None`` for all.

    Returns:
        tuple[IdentityReview, ...]: Oldest first.

    Raises:
        StorageError: If there are more than ``MAX_INTERFACES_PER_DEVICE``.
    """
    where = ReviewFilter(device_id=device_id, state=ReviewState.OPEN)
    return all_pages(
        lambda p: uow.reviews.list_page(p, where), MAX_INTERFACES_PER_DEVICE, "open reviews"
    )
