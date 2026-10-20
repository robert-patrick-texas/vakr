"""Parse ``inventory.toml`` tables into checked entries (11.3, 11.4, META-010).

Each table is read through ``TableReader`` and the field readers in
``parse_fields``, so unknown keys, wrong types, and out-of-range values are
reported with their path, and every problem in the file is listed in one
run. Array lengths are bounded (E.26).

Dependencies:
    vakr.policy.reader: Typed table reads that collect problems.
    vakr.inventory.parse_fields: Field readers.

Safety:
    Pure: no I/O. Values are data; nothing is executed or included.

Example:
    >>> doc = parse_document({"devices": [{"name": "r1", "address": "192.0.2.1"}]}, "x")
    >>> doc.devices[0].address, doc.devices[0].admin_state.value
    ('192.0.2.1', 'enabled')
"""

from collections.abc import Callable, Mapping
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vakr.domain.enums import AddressFamily, AdminState, InterestClass, ReachabilityStrategy
from vakr.inventory import parse_fields as fields
from vakr.inventory.errors import InventoryError, Issue
from vakr.inventory.model import AnnotationEntry, DeviceEntry, InventoryFile, SiteEntry
from vakr.policy.reader import TableReader

MAX_SITES = 1_000
MAX_DEVICES = 5_000
MAX_ANNOTATIONS = 20_000

_TOP = {"sites", "devices", "interface_annotations"}
_SITE = {"name", "code", "region", "timezone", "description", "uuid"}
_DEVICE = {
    "name", "address", "site", "credential", "tags", "groups", "comment", "admin_state",
    "interval", "discovery_interval", "reachability", "address_family", "uuid",
}  # fmt: skip
_ANNOTATION = {
    "device", "ifname", "tags", "interest", "high_priority", "wan_circuit", "sticky", "provider",
    "circuit_id", "contracted_bps", "capacity_override_bps", "public_dashboard_eligible",
    "display_rank", "comment",
}  # fmt: skip


def _site(table: object, at: str, issues: list[Issue]) -> SiteEntry | None:
    """Parse one ``[[sites]]`` table.

    Args:
        table: The table as loaded.
        at: Its location.
        issues: Problems are appended here.

    Returns:
        SiteEntry | None: The entry, or ``None`` when it has problems.

    Raises:
        None
    """
    before, r = len(issues), TableReader(table, at, issues, _SITE)
    name, code = fields.name(r, "name", required=True), fields.name(r, "code")
    region, zone = fields.name(r, "region"), fields.name(r, "timezone")
    if zone is not None:
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError, ValueError:
            r.problem("timezone", f"{zone!r} is not an IANA timezone")
    description, uuid = (r.text("description") or "").strip(), fields.uuid_value(r, "uuid")
    if name is None or len(issues) > before:
        return None
    return SiteEntry(at, name, code or name.upper(), region, zone, description, uuid)


def _device(table: object, at: str, issues: list[Issue]) -> DeviceEntry | None:
    """Parse one ``[[devices]]`` table.

    Args:
        table: The table as loaded.
        at: Its location.
        issues: Problems are appended here.

    Returns:
        DeviceEntry | None: The entry, or ``None`` when it has problems.

    Raises:
        None
    """
    before, r = len(issues), TableReader(table, at, issues, _DEVICE)
    family = fields.enum_value(r, "address_family", AddressFamily)
    name, address = fields.name(r, "name", required=True), fields.address(r, family)
    entry = DeviceEntry(
        at, name or "", address or "", site=fields.name(r, "site"),
        credential=fields.name(r, "credential"), tags=fields.labels(r, "tags"),
        groups=fields.labels(r, "groups"), comment=fields.comment(r),
        admin_state=fields.enum_value(r, "admin_state", AdminState) or AdminState.ENABLED,
        interval_s=fields.seconds(r, "interval", "polling_interval"),
        discovery_interval_s=fields.seconds(r, "discovery_interval", "discovery_interval"),
        reachability=fields.enum_value(r, "reachability", ReachabilityStrategy),
        address_family=family, uuid=fields.uuid_value(r, "uuid"),
    )  # fmt: skip
    return None if len(issues) > before else entry


def _interest(r: TableReader) -> InterestClass:
    """Read ``interest`` and its alias ``high_priority`` (META-011), which must agree.

    Args:
        r: The table reader.

    Returns:
        InterestClass: ``PRIMARY`` or ``SECONDARY``.

    Raises:
        None
    """
    interest, high = fields.enum_value(r, "interest", InterestClass), r.boolean("high_priority")
    implied = None if high is None else (InterestClass.PRIMARY if high else InterestClass.SECONDARY)
    if interest is not None and implied is not None and interest is not implied:
        r.problem("high_priority", f"contradicts interest = {interest.value!r}")
    return interest or implied or InterestClass.SECONDARY


def _annotation(table: object, at: str, issues: list[Issue]) -> AnnotationEntry | None:
    """Parse one ``[[interface_annotations]]`` table.

    Args:
        table: The table as loaded.
        at: Its location.
        issues: Problems are appended here.

    Returns:
        AnnotationEntry | None: The entry, or ``None`` when it has problems.

    Raises:
        None
    """
    before, r = len(issues), TableReader(table, at, issues, _ANNOTATION)
    device, ifname = (
        fields.name(r, "device", required=True),
        fields.name(r, "ifname", required=True),
    )
    entry = AnnotationEntry(
        at, device or "", ifname or "", tags=fields.labels(r, "tags"), interest=_interest(r),
        wan_circuit=bool(r.boolean("wan_circuit")), sticky=bool(r.boolean("sticky")),
        provider=fields.name(r, "provider"),
        circuit_id=fields.name(r, "circuit_id"), contracted_bps=r.integer("contracted_bps", 1),
        capacity_override_bps=r.integer("capacity_override_bps", 1),
        public_dashboard=bool(r.boolean("public_dashboard_eligible")),
        display_rank=r.integer("display_rank", 0, 1_000_000), comment=fields.comment(r),
    )  # fmt: skip
    return None if len(issues) > before else entry


def _entries[T](
    data: Mapping[str, Any], key: str, limit: int,
    parse: Callable[[object, str, list[Issue]], T | None], issues: list[Issue],
) -> tuple[T, ...]:  # fmt: skip
    """Parse one bounded array of tables.

    Args:
        data: The document.
        key: Array name.
        limit: Most entries allowed (E.26).
        parse: Parser for one table.
        issues: Problems are appended here.

    Returns:
        tuple[T, ...]: The valid entries, in file order.

    Raises:
        None
    """
    tables = data.get(key, [])
    if not isinstance(tables, list):
        issues.append(Issue(key, "expected an array of tables, such as [[" + key + "]]"))
        return ()
    if len(tables) > limit:
        issues.append(Issue(key, f"at most {limit} entries are allowed (E.26)"))
        return ()
    return tuple(e for i, t in enumerate(tables) if (e := parse(t, f"{key}[{i}]", issues)))


def parse_document(data: Mapping[str, Any], source: str) -> InventoryFile:
    """Check a loaded ``inventory.toml`` document, table by table.

    Args:
        data: The top-level table from ``tomllib``.
        source: File name for messages.

    Returns:
        InventoryFile: Every entry, in file order.

    Raises:
        InventoryError: Listing every problem with its path.
    """
    issues: list[Issue] = []
    TableReader(data, "", issues, _TOP)
    sites = _entries(data, "sites", MAX_SITES, _site, issues)
    devices = _entries(data, "devices", MAX_DEVICES, _device, issues)
    notes = _entries(data, "interface_annotations", MAX_ANNOTATIONS, _annotation, issues)
    if issues:
        raise InventoryError(source, issues)
    return InventoryFile(source, sites, devices, notes)
