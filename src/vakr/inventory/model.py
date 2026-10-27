"""The parsed ``inventory.toml``: sites, devices, and interface annotations (11.3, META-010).

These records hold exactly what the operator wrote, already type-checked
and range-checked by ``vakr.inventory.parse`` but not yet compared with
other records or with storage (``vakr.inventory.validate`` does that).
Every entry remembers its location, such as ``devices[3]``, so any later
problem is reported where the operator can find it.

Every record vakr creates from the file is owned by ``FILE_OWNER``
(``file:inventory.toml``, META-008). The owner names the file, not its
directory, so relocating the configuration with ``VAKR_ROOT`` never
changes who owns a record. ``vakr inventory import`` reads the same
entries from a CSV or TOML file but writes API-managed records, owned by
``IMPORT_OWNER`` when it creates them (E.38): an import is a bulk API
write, not another file source. ``given`` records which fields an import
row wrote, because an import changes only those (an absent column keeps
the stored value).

Dependencies:
    dataclasses: Frozen records.
    vakr.domain: Enums and the ownership type.

Safety:
    A device names its credential only; the file never holds a secret.

Example:
    >>> str(FILE_OWNER)
    'file:inventory.toml'
"""

from dataclasses import dataclass
from uuid import UUID

from vakr.domain.enums import (
    AddressFamily,
    AdminState,
    InterestClass,
    ManagedByKind,
    ReachabilityStrategy,
)
from vakr.domain.ownership import ManagedBy

INVENTORY_SOURCE = "inventory.toml"
"""The ``source`` of every file-managed inventory record."""

FILE_OWNER = ManagedBy.file(INVENTORY_SOURCE)
"""Owner of records created from ``inventory.toml``."""

IMPORT_OWNER = ManagedBy(ManagedByKind.API, "import")
"""Owner of records ``vakr inventory import`` creates; API-managed (E.38)."""


@dataclass(frozen=True, slots=True)
class SiteEntry:
    """One ``[[sites]]`` table.

    Attributes:
        at: Location, such as ``sites[0]``.
        name: Unique site name; the natural key.
        code: Short code; defaults to the name upper-cased.
        region: Optional region label.
        timezone: Optional IANA timezone.
        description: Free-form text.
        uuid: Explicit UUID to keep across re-creation, if given.
        given: Fields the entry wrote; an import changes only these.
        managed_by: Owner an import row states (CSV ``managed_by``), if any.
    """

    at: str
    name: str
    code: str
    region: str | None = None
    timezone: str | None = None
    description: str = ""
    uuid: UUID | None = None
    given: frozenset[str] = frozenset()
    managed_by: ManagedBy | None = None


@dataclass(frozen=True, slots=True)
class DeviceEntry:
    """One ``[[devices]]`` table.

    Attributes:
        at: Location, such as ``devices[3]``.
        name: Unique device name; the natural key (META-001: never identity).
        address: Poll address: canonical IP literal or DNS name.
        site: Site name, if any.
        credential: Credential name in the protected provider (SEC-001).
        tags: Tag names.
        groups: Group names.
        comment: Operator comment (META-003).
        admin_state: Scheduling state (7.5).
        interval_s: Poll interval override in seconds (E.18 limits).
        discovery_interval_s: Rediscovery interval override in seconds.
        reachability: Reachability strategy override (ICMP-006).
        address_family: Preferred family when a name resolves to both.
        uuid: Explicit UUID, if given.
        given: Fields the entry wrote; an import changes only these.
        managed_by: Owner an import row states (CSV ``managed_by``), if any.
    """

    at: str
    name: str
    address: str
    site: str | None = None
    credential: str | None = None
    tags: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    comment: str = ""
    admin_state: AdminState = AdminState.ENABLED
    interval_s: int | None = None
    discovery_interval_s: int | None = None
    reachability: ReachabilityStrategy | None = None
    address_family: AddressFamily | None = None
    uuid: UUID | None = None
    given: frozenset[str] = frozenset()
    managed_by: ManagedBy | None = None


@dataclass(frozen=True, slots=True)
class AnnotationEntry:
    """One ``[[interface_annotations]]`` table (META-007, META-011).

    Attributes:
        at: Location, such as ``interface_annotations[0]``.
        device: Device name.
        ifname: ``ifName`` of the interface, as the device reports it.
        tags: Tags added in vakr (META-013).
        interest: Interest class; ``high_priority = true`` means ``primary``.
        wan_circuit: WAN-circuit flag.
        sticky: Sticky flag (LIFE-007).
        provider: Carrier or ISP.
        circuit_id: Carrier circuit ID.
        contracted_bps: Contracted rate, bits per second.
        capacity_override_bps: Capacity to use instead of ``ifSpeed``.
        public_dashboard: Eligible for public dashboards.
        display_rank: Ordering hint; lower first.
        comment: Operator comment (META-004).
        uuid: The interface's UUID, if given; it then names the interface
            even after a rename, and ``ifname`` is only a label.
        given: Fields the entry wrote; an import changes only these.
        managed_by: Owner an import row states (CSV ``managed_by``), if any.
    """

    at: str
    device: str
    ifname: str
    tags: tuple[str, ...] = ()
    interest: InterestClass = InterestClass.SECONDARY
    wan_circuit: bool = False
    sticky: bool = False
    provider: str | None = None
    circuit_id: str | None = None
    contracted_bps: int | None = None
    capacity_override_bps: int | None = None
    public_dashboard: bool = False
    display_rank: int | None = None
    comment: str = ""
    uuid: UUID | None = None
    given: frozenset[str] = frozenset()
    managed_by: ManagedBy | None = None


@dataclass(frozen=True, slots=True)
class InventoryFile:
    """Everything one ``inventory.toml`` declares.

    Attributes:
        source: Display path of the file, used in messages.
        sites: Sites in file order.
        devices: Devices in file order.
        annotations: Interface annotations in file order.
    """

    source: str
    sites: tuple[SiteEntry, ...] = ()
    devices: tuple[DeviceEntry, ...] = ()
    annotations: tuple[AnnotationEntry, ...] = ()
