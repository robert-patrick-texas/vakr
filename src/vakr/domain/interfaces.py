"""Logical interfaces, their annotations, and identity history (7.1, META-002, IFID-005).

An interface's identity is an application UUID, never its ``ifIndex``: a
router reload can renumber every port, and history must follow the circuit,
not the number (invariant 6). Fields therefore fall into two groups with
different writers:

- *observed* attributes come from SNMP discovery (``if_index``, names,
  MAC, speeds, addresses, description-derived tags) and change only through
  ``InterfaceRepository.update_observed``;
- *owned* fields are the operator's (comment, tags, annotation) and change
  only through ``InterfaceRepository.update`` with an ownership check.

``lifecycle`` and ``inactive_since``, the start of the unbroken INACTIVE or
STALE period that the ten-day stale clock counts from (LIFE-003), change
together and only through ``set_lifecycle``. An annotation or tag edit
moves ``updated_at`` but never the stale clock. Retiring an interface is a
soft change that keeps every observation and history entry (META-009).

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, addressing, timestamps: Shared invariant checks.

Safety:
    No field holds credentials. Descriptions are operator-visible text.

Example:
    >>> a = InterfaceAnnotation(interest=InterestClass.PRIMARY, is_wan_circuit=True)
    >>> a.interest, a.sticky
    (<InterestClass.PRIMARY: 'primary'>, False)
"""

from dataclasses import dataclass, field
from datetime import datetime

from vakr.domain.addressing import require_interface_addresses, require_mac
from vakr.domain.checks import (
    require_bool,
    require_enum,
    require_instance,
    require_int,
    require_name,
    require_order,
    require_text,
    require_uuid,
    require_uuid_set,
)
from vakr.domain.enums import InterestClass, InterfaceLifecycle
from vakr.domain.errors import ValidationError
from vakr.domain.ids import DeviceId, InterfaceId, SiteId, TagId
from vakr.domain.ownership import ManagedBy, require_record
from vakr.domain.timestamps import require_optional_utc, require_utc

MAX_IF_INDEX = 2_147_483_647  # IF-MIB InterfaceIndex is 1..2^31-1

INTERFACE_OBSERVED_FIELDS = (
    "if_index",
    "if_name",
    "if_descr",
    "if_alias",
    "if_type",
    "mac",
    "if_speed",
    "if_high_speed",
    "addresses",
    "description_tags",
)
"""Fields that discovery writes through ``update_observed``."""

INTERFACE_OWNED_FIELDS = ("comment", "tags", "annotation")
"""Fields the owning source writes through ``update``."""

INTERFACE_LIFECYCLE_FIELDS = ("lifecycle", "inactive_since")
"""Fields only ``set_lifecycle`` writes, always together."""

INTERFACE_FIXED_FIELDS = (
    "id",
    "device_id",
    "managed_by",
    "created_at",
    *INTERFACE_LIFECYCLE_FIELDS,
)
"""Fields neither ``update`` nor ``update_observed`` may change."""

INACTIVE_STATES = frozenset({InterfaceLifecycle.INACTIVE, InterfaceLifecycle.STALE})
"""States with a running or expired stale clock, so with an ``inactive_since``."""


@dataclass(frozen=True, slots=True)
class InterfaceAnnotation:
    """Operator metadata about what an interface is for (META-007, META-011).

    ``interest`` ``PRIMARY`` is the high-priority flag, not a second concept.

    Attributes:
        interest: How often operators look at the interface.
        sticky: Keep monitoring even when discovery stops seeing it.
        is_wan_circuit: The interface is a WAN circuit (``wan_circuit`` in files).
        provider: ISP or carrier name.
        circuit_id: Carrier circuit ID.
        contracted_bps: Contracted or committed rate, bits per second.
        capacity_override_bps: Capacity to use instead of ``ifSpeed``.
        site_override: Site for this interface when it differs from the device's.
        public_dashboard: Eligible for public dashboards (invariant 12).
        display_rank: Ordering hint for lists; lower comes first.
    """

    interest: InterestClass = InterestClass.SECONDARY
    sticky: bool = False
    is_wan_circuit: bool = False
    provider: str | None = None
    circuit_id: str | None = None
    contracted_bps: int | None = None
    capacity_override_bps: int | None = None
    site_override: SiteId | None = None
    public_dashboard: bool = False
    display_rank: int | None = None

    def __post_init__(self) -> None:
        """Validate the annotation.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_enum(self.interest, InterestClass, "interest")
        require_bool(self.sticky, "sticky")
        require_bool(self.is_wan_circuit, "is_wan_circuit")
        require_name(self.provider, "provider", optional=True)
        require_name(self.circuit_id, "circuit_id", optional=True)
        require_int(self.contracted_bps, "contracted_bps", minimum=1, optional=True)
        require_int(self.capacity_override_bps, "capacity_override_bps", minimum=1, optional=True)
        require_uuid(self.site_override, "site_override", optional=True)
        require_bool(self.public_dashboard, "public_dashboard")
        require_int(self.display_rank, "display_rank", minimum=0, optional=True)


@dataclass(frozen=True, slots=True)
class Interface:
    """A logical interface with a persistent identity (META-002, META-004).

    Attributes:
        id: Immutable interface UUID, independent of ``ifIndex``.
        device_id: The device the interface belongs to; never changes.
        if_index: Currently observed ``ifIndex``, or ``None`` when not seen.
        if_name: ``ifName`` (may be empty on old agents).
        if_descr: ``ifDescr``.
        if_alias: ``ifAlias``, the operator description on the device.
        if_type: IANAifType number, if reported.
        mac: ``ifPhysAddress`` as lower-case colon hex, if any.
        if_speed: ``ifSpeed`` in bits per second, if reported.
        if_high_speed: ``ifHighSpeed`` in megabits per second, if reported.
        addresses: IP interface addresses such as ``10.0.0.1/30``.
        lifecycle: Interface lifecycle state (LIFE-001).
        managed_by: Owner of the operator fields.
        created_at: When the identity was created (UTC).
        updated_at: When the record last changed (UTC).
        comment: Free-form operator comment.
        tags: Tags added in vakr; never removed by a description change (META-013).
        description_tags: Tags derived from ``ifAlias`` keywords (META-012).
        annotation: Operator circuit and interest metadata.
        inactive_since: Start of the unbroken INACTIVE/STALE period (UTC); set
            exactly while ``lifecycle`` is INACTIVE or STALE (LIFE-003).
        version: Optimistic-concurrency version.
    """

    id: InterfaceId
    device_id: DeviceId
    if_index: int | None
    if_name: str
    if_descr: str
    if_alias: str
    lifecycle: InterfaceLifecycle
    managed_by: ManagedBy
    created_at: datetime
    updated_at: datetime
    if_type: int | None = None
    mac: str | None = None
    if_speed: int | None = None
    if_high_speed: int | None = None
    addresses: tuple[str, ...] = ()
    comment: str = ""
    tags: frozenset[TagId] = frozenset()
    description_tags: frozenset[TagId] = frozenset()
    annotation: InterfaceAnnotation = field(default_factory=InterfaceAnnotation)
    inactive_since: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the interface.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_uuid(self.device_id, "device_id")
        require_int(self.if_index, "if_index", minimum=1, maximum=MAX_IF_INDEX, optional=True)
        for name in ("if_name", "if_descr", "if_alias", "comment"):
            require_text(getattr(self, name), name)
        require_enum(self.lifecycle, InterfaceLifecycle, "lifecycle")
        require_int(self.if_type, "if_type", minimum=1, optional=True)
        require_mac(self.mac, "mac")
        require_int(self.if_speed, "if_speed", minimum=0, optional=True)
        require_int(self.if_high_speed, "if_high_speed", minimum=0, optional=True)
        require_interface_addresses(self.addresses, "addresses")
        require_uuid_set(self.tags, "tags")
        require_uuid_set(self.description_tags, "description_tags")
        require_instance(self.annotation, InterfaceAnnotation, "annotation")
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        require_order(self.created_at, self.updated_at, "updated_at")
        require_optional_utc(self.inactive_since, "inactive_since")
        if (self.lifecycle in INACTIVE_STATES) != (self.inactive_since is not None):
            raise ValidationError(
                "inactive_since is set exactly while an interface is inactive or stale",
                field="inactive_since",
            )
        require_record(self.version, self.managed_by)
