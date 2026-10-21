"""Interface scopes and references shared by lists, rankings, and the overview (UI-008, UI-009).

``InterfaceScope`` is the filter set every M4 interface view accepts: by
device, site (the interface's override, else its device's), tag (the
interface's operator or description tags, or its device's tags), device
group, provider (case-insensitive exact name), WAN circuits only, interest
class (``primary`` is the high-priority annotation, META-011), and
lifecycle. Retired interfaces and interfaces of retired devices are left
out unless asked for (7.5, LIFE-002). ``interface_ref`` names an interface
the way every view shows it.

Dependencies:
    vakr.query.catalog_index, catalog_views, interface_rows: Index, views, names.

Safety:
    Pure; filters compare IDs, enums, and case-folded names only.

Example:
    >>> InterfaceScope().include_retired, InterfaceScope(provider="ACME").provider
    (False, 'ACME')
"""

from dataclasses import dataclass

from vakr.domain.enums import AdminState, InterestClass, InterfaceLifecycle
from vakr.domain.ids import DeviceId, GroupId, SiteId, TagId
from vakr.domain.interfaces import Interface
from vakr.logic.natural import natural_key
from vakr.query.catalog_index import CatalogIndex
from vakr.query.catalog_views import InterfaceRef
from vakr.query.interface_rows import display_name

type RefKey = tuple[tuple[tuple[str | int, ...], str], tuple[tuple[str | int, ...], str], str]


@dataclass(frozen=True, slots=True)
class InterfaceScope:
    """Which interfaces a view covers; ``None`` fields do not filter.

    Attributes:
        device_id: Only this device's interfaces.
        site_id: Only interfaces at this site.
        tag_id: Only interfaces with this tag, or on a device with it.
        group_id: Only interfaces of devices in this group.
        provider: Only circuits of this provider (case-insensitive).
        wan_only: Only WAN circuits.
        interest: Only this interest class.
        lifecycle: Only this lifecycle state; overrides ``include_retired``.
        include_retired: Include retired interfaces and retired devices.
    """

    device_id: DeviceId | None = None
    site_id: SiteId | None = None
    tag_id: TagId | None = None
    group_id: GroupId | None = None
    provider: str | None = None
    wan_only: bool = False
    interest: InterestClass | None = None
    lifecycle: InterfaceLifecycle | None = None
    include_retired: bool = False

    def _inventory_ok(self, index: CatalogIndex, iface: Interface) -> bool:
        """Apply the device, site, tag, and group filters.

        Args:
            index: The inventory.
            iface: The interface.

        Returns:
            bool: True when every set filter matches.

        Raises:
            None
        """
        device = index.devices.get(iface.device_id)
        if device is None or (self.device_id is not None and device.id != self.device_id):
            return False
        if not self.include_retired and device.admin_state is AdminState.RETIRED:
            return False
        site = index.site_of(iface)
        if self.site_id is not None and (site is None or site.id != self.site_id):
            return False
        tags = iface.tags | iface.description_tags | device.tags
        if self.tag_id is not None and self.tag_id not in tags:
            return False
        return self.group_id is None or self.group_id in device.groups

    def matches(self, index: CatalogIndex, iface: Interface) -> bool:
        """Tell whether an interface is in scope.

        Args:
            index: The inventory.
            iface: The interface.

        Returns:
            bool: True when every set filter matches.

        Raises:
            None
        """
        a = iface.annotation
        if self.lifecycle is not None:
            if iface.lifecycle is not self.lifecycle:
                return False
        elif not self.include_retired and iface.lifecycle is InterfaceLifecycle.RETIRED:
            return False
        if self.wan_only and not a.is_wan_circuit:
            return False
        if self.interest is not None and a.interest is not self.interest:
            return False
        wanted = self.provider.casefold() if self.provider else None
        if wanted is not None and (a.provider or "").casefold() != wanted:
            return False
        return self._inventory_ok(index, iface)


def markers_of(iface: Interface) -> tuple[str, ...]:
    """Return the list markers of an interface (E.29 #6).

    Args:
        iface: The interface.

    Returns:
        tuple[str, ...]: ``sticky``, ``WAN``, and ``primary`` as they apply.

    Raises:
        None
    """
    a = iface.annotation
    flags = (("sticky", a.sticky), ("WAN", a.is_wan_circuit))
    flags += (("primary", a.interest is InterestClass.PRIMARY),)
    return tuple(name for name, on in flags if on)


def interface_ref(index: CatalogIndex, iface: Interface) -> InterfaceRef:
    """Name an interface the way every M4 view shows it.

    Args:
        index: The inventory.
        iface: The interface.

    Returns:
        InterfaceRef: The reference.

    Raises:
        None
    """
    device = index.devices.get(iface.device_id)
    site = index.site_of(iface)
    a = iface.annotation
    return InterfaceRef(
        iface.id, iface.device_id, device.name if device else str(iface.device_id),
        display_name(iface), iface.if_alias, iface.if_descr, site.name if site else None,
        a.provider, a.circuit_id, markers_of(iface), iface.lifecycle.value,
    )  # fmt: skip


def ref_key(ref: InterfaceRef) -> RefKey:
    """Natural order of interfaces across devices: device name, interface name, UUID.

    Args:
        ref: The reference.

    Returns:
        RefKey: Smaller sorts first.

    Raises:
        None
    """
    return natural_key(ref.device), natural_key(ref.name), str(ref.interface_id)
