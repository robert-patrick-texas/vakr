"""Inventory records: devices and sites (blueprint 7.1).

These are the things an operator configures. Each record has an immutable
UUID that never depends on a hostname or address (META-001), an integer
``version`` for optimistic concurrency (DB-003), and a ``ManagedBy`` that says
whether a configuration file or the API owns it (META-008). Records are
frozen: an edit produces a new record that a repository stores with the
version check.

This module is also the import point for the rest of the inventory model:
``ManagedBy`` (``vakr.domain.ownership``), ``Tag``, ``Group``, and
``Collector`` (``vakr.domain.catalog``) are re-exported here. Interfaces
live in ``vakr.domain.interfaces``.

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, addressing, timestamps: Shared invariant checks.

Safety:
    A device names its SNMP credential by reference only (SEC-001); the
    secret itself never enters a domain record, a repr, or storage.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.ids import DeviceId, new_id
    >>> now = datetime(2026, 10, 20, tzinfo=UTC)
    >>> d = Device(new_id(DeviceId), "core1", "192.0.2.1", API_MANAGED, now, now)
    >>> d.admin_state, d.version
    (<AdminState.ENABLED: 'enabled'>, 1)
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.addressing import require_poll_address
from vakr.domain.catalog import Collector, Group, Tag
from vakr.domain.checks import (
    require_enum,
    require_int,
    require_name,
    require_order,
    require_text,
    require_uuid,
    require_uuid_set,
)
from vakr.domain.enums import AddressFamily, AdminState, ReachabilityStrategy
from vakr.domain.ids import DeviceId, GroupId, SiteId, TagId
from vakr.domain.ownership import API_MANAGED, RECORD_FIXED_FIELDS, ManagedBy, require_record
from vakr.domain.timestamps import require_utc

__all__ = [
    "API_MANAGED",
    "DEVICE_FIXED_FIELDS",
    "RECORD_FIXED_FIELDS",
    "Collector",
    "Device",
    "Group",
    "ManagedBy",
    "Site",
    "Tag",
]

DEVICE_FIXED_FIELDS = ("id", "managed_by", "created_at")
"""Fields ``DeviceRepository.update`` may never change."""


@dataclass(frozen=True, slots=True)
class Site:
    """A place devices live, for browsing and dashboards (META-006).

    Attributes:
        id: Immutable site UUID.
        name: Unique display name; the natural key.
        code: Short code such as ``DAL1``.
        managed_by: Owning source.
        region: Optional region label.
        timezone: Optional IANA zone name for local displays (TIME-003).
        description: Free-form text.
        tags: Tags applied to the site.
        version: Optimistic-concurrency version.
    """

    id: SiteId
    name: str
    code: str
    managed_by: ManagedBy
    region: str | None = None
    timezone: str | None = None
    description: str = ""
    tags: frozenset[TagId] = frozenset()
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the site.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_name(self.name, "name")
        require_name(self.code, "code")
        require_name(self.region, "region", optional=True)
        require_name(self.timezone, "timezone", optional=True)
        require_text(self.description, "description")
        require_uuid_set(self.tags, "tags")
        require_record(self.version, self.managed_by)


@dataclass(frozen=True, slots=True)
class Device:
    """A monitored network device (META-001, META-003, META-005).

    Attributes:
        id: Immutable device UUID, independent of name and address.
        name: Unique display name; the natural key.
        poll_address: Canonical IP literal or DNS name to poll.
        managed_by: Owning source.
        created_at: When the record was created (UTC).
        updated_at: When the record last changed (UTC).
        address_family: Preferred family when a name resolves to both.
        site_id: Site the device belongs to, if any.
        admin_state: Operator-set scheduling state (7.5).
        poll_interval_s: Per-device poll interval override, in seconds.
        discovery_interval_s: Per-device rediscovery interval override, in seconds.
        reachability: Per-device ICMP/SNMP gating override.
        credential_ref: Name of the SNMP credential to use; never the secret.
        comment: Free-form operator comment.
        tags: Operator tags.
        groups: Explicit group memberships.
        version: Optimistic-concurrency version.
    """

    id: DeviceId
    name: str
    poll_address: str
    managed_by: ManagedBy
    created_at: datetime
    updated_at: datetime
    address_family: AddressFamily | None = None
    site_id: SiteId | None = None
    admin_state: AdminState = AdminState.ENABLED
    poll_interval_s: int | None = None
    discovery_interval_s: int | None = None
    reachability: ReachabilityStrategy | None = None
    credential_ref: str | None = None
    comment: str = ""
    tags: frozenset[TagId] = frozenset()
    groups: frozenset[GroupId] = frozenset()
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the device.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_name(self.name, "name")
        require_enum(self.address_family, AddressFamily, "address_family", optional=True)
        require_poll_address(self.poll_address, self.address_family)
        require_uuid(self.site_id, "site_id", optional=True)
        require_enum(self.admin_state, AdminState, "admin_state")
        require_int(self.poll_interval_s, "poll_interval_s", minimum=1, optional=True)
        require_int(self.discovery_interval_s, "discovery_interval_s", minimum=1, optional=True)
        require_enum(self.reachability, ReachabilityStrategy, "reachability", optional=True)
        require_name(self.credential_ref, "credential_ref", optional=True)
        require_text(self.comment, "comment")
        require_uuid_set(self.tags, "tags")
        require_uuid_set(self.groups, "groups")
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        require_order(self.created_at, self.updated_at, "updated_at")
        require_record(self.version, self.managed_by)
