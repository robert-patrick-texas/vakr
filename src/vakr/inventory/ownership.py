"""Which stored record a file entry manages, and when that is a conflict (CONF-008, 11.2).

An entry with ``uuid`` manages the record with that UUID, so renaming a
device in the file keeps its identity and history (META-001); an entry
without one manages the record with its name. A file may only manage
records it created: an entry that lands on an API-managed record, or a
device address an API-managed device already polls, is an error that
names both sides, never a silent takeover (CONF-008).

Dependencies:
    vakr.inventory.snapshot: Stored records.

Safety:
    Pure.

Example:
    >>> from vakr.inventory.model import DeviceEntry
    >>> from vakr.inventory.snapshot import EMPTY
    >>> match_device(DeviceEntry("devices[0]", "edge1", "192.0.2.1"), EMPTY)
    (None, None)
"""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from vakr.domain.enums import AdminState
from vakr.domain.inventory import Site
from vakr.domain.ownership import ManagedBy
from vakr.inventory.errors import Issue
from vakr.inventory.model import FILE_OWNER, DeviceEntry, InventoryFile, SiteEntry
from vakr.inventory.snapshot import Snapshot, StoredDevice

_CONF008 = "remove the entry, or remove or retire the API-managed record first (CONF-008)"


class Owned(Protocol):
    """A stored record with an identity and an owner."""

    @property
    def id(self) -> UUID:
        """The record's UUID.

        Args:
            None

        Returns:
            UUID: The identity.

        Raises:
            None
        """
        ...

    @property
    def managed_by(self) -> ManagedBy:
        """The record's owner.

        Args:
            None

        Returns:
            ManagedBy: The owning source.

        Raises:
            None
        """
        ...


def match_record[R: Owned](
    entry: SiteEntry | DeviceEntry, find: Callable[[UUID | str], R | None], what: str
) -> tuple[R | None, str | None]:
    """Find the stored record an entry manages, or explain the conflict (CONF-008).

    Args:
        entry: The file entry.
        find: Looks a record up by UUID or name.
        what: ``site`` or ``device``.

    Returns:
        tuple[R | None, str | None]: The record (``None``: create it) and a
        conflict message (``None``: no conflict).

    Raises:
        None
    """
    by_name = find(entry.name)
    if entry.uuid is not None and by_name is not None and by_name.id != entry.uuid:
        return None, (f"{what} name {entry.name!r} belongs to {what} {by_name.id} "
                      f"({by_name.managed_by}), not to uuid {entry.uuid}")  # fmt: skip
    target = find(entry.uuid) if entry.uuid is not None else by_name
    if target is not None and target.managed_by != FILE_OWNER:
        return None, f"{what} {target.id} is {target.managed_by}; {_CONF008}"
    return target, None


def match_site(entry: SiteEntry, snapshot: Snapshot) -> tuple[Site | None, str | None]:
    """Find the stored site an entry manages.

    Args:
        entry: The site entry.
        snapshot: Stored records.

    Returns:
        tuple[Site | None, str | None]: The site and any conflict.

    Raises:
        None
    """
    return match_record(entry, snapshot.site, "site")


def match_device(entry: DeviceEntry, snapshot: Snapshot) -> tuple[StoredDevice | None, str | None]:
    """Find the stored device an entry manages.

    Args:
        entry: The device entry.
        snapshot: Stored records.

    Returns:
        tuple[StoredDevice | None, str | None]: The device and any conflict.

    Raises:
        None
    """
    return match_record(entry, snapshot.device_named, "device")


def ownership_problems(inv: InventoryFile, snapshot: Snapshot, problems: list[Issue]) -> None:
    """Refuse entries that collide with API-managed records (CONF-008).

    Args:
        inv: The file.
        snapshot: Stored records.
        problems: Problems are appended here.

    Returns:
        None

    Raises:
        None
    """
    claimed: dict[UUID, str] = {}
    for s in inv.sites:
        _, conflict = match_site(s, snapshot)
        if conflict:
            problems.append(Issue(f"{s.at}.{'uuid' if s.uuid else 'name'}", conflict))
    others = {d.device.poll_address.lower(): d for d in snapshot.devices
              if not d.file_managed and d.device.admin_state is not AdminState.RETIRED}  # fmt: skip
    for d in inv.devices:
        stored, conflict = match_device(d, snapshot)
        if conflict:
            problems.append(Issue(f"{d.at}.{'uuid' if d.uuid else 'name'}", conflict))
        elif stored is not None and stored.device.id in claimed:
            why = f"manages the same device as {claimed[stored.device.id]}"
            problems.append(Issue(f"{d.at}.uuid", why))
        elif stored is not None:
            claimed[stored.device.id] = d.at
        if (other := others.get(d.address.lower())) is not None:
            problems.append(Issue(f"{d.at}.address", f"already polled as {other.device.managed_by} "
                                  f"device {other.device.name!r}; {_CONF008}"))  # fmt: skip
