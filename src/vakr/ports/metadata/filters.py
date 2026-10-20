"""List filters for metadata repositories (API-005).

A filter narrows a list; ``None`` in a field means "do not filter on this".
Retired devices and interfaces are hidden unless the filter asks for them,
because retired records keep their history but leave default views
(blueprint 7.5, LIFE-002). Every filter is a frozen value, so a cursor taken
under one filter is meaningless under another and callers keep the pair.

Dependencies:
    vakr.domain: Enums and ID types.

Safety:
    Filters hold IDs and enum values only, never free text that reaches
    a query unparameterised.

Example:
    >>> DeviceFilter().include_retired, InterfaceFilter().include_retired
    (False, False)
"""

from dataclasses import dataclass
from uuid import UUID

from vakr.domain.enums import AdminState, InterestClass, InterfaceLifecycle
from vakr.domain.ids import DeviceId, GroupId, SiteId, TagId


@dataclass(frozen=True, slots=True)
class NoFilter:
    """The filter of entities that cannot be filtered yet (sites, tags, groups, ...).

    Attributes:
        None
    """


@dataclass(frozen=True, slots=True)
class DeviceFilter:
    """Which devices to list.

    Attributes:
        site_id: Only devices at this site.
        tag_id: Only devices with this tag.
        group_id: Only devices in this group.
        admin_state: Only devices in this state; overrides ``include_retired``.
        include_retired: Include ``RETIRED`` devices when ``admin_state`` is ``None``.
    """

    site_id: SiteId | None = None
    tag_id: TagId | None = None
    group_id: GroupId | None = None
    admin_state: AdminState | None = None
    include_retired: bool = False


@dataclass(frozen=True, slots=True)
class InterfaceFilter:
    """Which interfaces to list; filtering by ``device_id`` lists a device's interfaces.

    Attributes:
        device_id: Only interfaces of this device.
        lifecycle: Only interfaces in this state; overrides ``include_retired``.
        interest: Only interfaces of this interest class.
        tag_id: Only interfaces with this tag, operator-added or description-derived.
        is_wan_circuit: Only WAN circuits (``True``) or only non-WAN (``False``).
        include_retired: Include ``RETIRED`` interfaces when ``lifecycle`` is ``None``.
    """

    device_id: DeviceId | None = None
    lifecycle: InterfaceLifecycle | None = None
    interest: InterestClass | None = None
    tag_id: TagId | None = None
    is_wan_circuit: bool | None = None
    include_retired: bool = False


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """Which audit events to list.

    Attributes:
        object_id: Only events about this object.
        actor: Only events by this actor.
    """

    object_id: UUID | None = None
    actor: str | None = None
