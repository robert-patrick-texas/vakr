"""Reads and summary facts for the device screen (TUI-004, TUI-005, 12.4).

The device screen reads one device: its record, its interfaces (bounded by
E.29 ``max_device_interfaces``), and the current interface samples of the
position whose evidence the screen shows. The summary facts are labelled
readings in a fixed order, so ``vakr status DEVICE`` and ``vakr tui`` print
the same lines.

``sysDescr``, ``sysObjectID``, ``sysName``, ``sysLocation``, and
``sysContact`` (12.4) are not kept in inventory yet: no metadata port
stores discovery facts. The screen says so instead of leaving the line out.

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry: Data access.
    vakr.query.*: Snapshot paging, judgements, labels, formats, views.

Safety:
    Read-only. Synchronous: async callers use ``asyncio.to_thread``.

Example:
    >>> UPTIME_NOTE.startswith("sysUpTime is how long")
    True
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.domain.state import CurrentInterfaceState
from vakr.ports.metadata import InterfaceFilter, MetadataUnitOfWork
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.evidence import Judgement
from vakr.query.snapshot import Inventory, pages
from vakr.query.text import milliseconds
from vakr.query.views import DeviceRow, Reading, missing

UPTIME_NOTE = (
    "sysUpTime is how long the device's SNMP agent has run, not proof of chassis or OS uptime: "
    "an agent restart resets it without a reload (UPTIME-003)."
)
MISSING_NOTE = "'—' means the value is not available, with the reason beside it; it is never zero."
SYSTEM_FACTS_GAP = "not kept in inventory yet; `vakr snmp discover DEVICE` shows it"


def find_device(uow: MetadataUnitOfWork, key: str) -> Device | None:
    """Find a device by exact name, else by UUID.

    Args:
        uow: An open unit of work.
        key: Display name or device UUID.

    Returns:
        Device | None: The device, retired or not; ``None`` when nothing matches.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    if (device := uow.devices.get_by_name(key)) is not None:
        return device
    try:
        return uow.devices.get(DeviceId(UUID(key)))
    except ValueError:
        return None


def read_detail_inventory(
    uow: MetadataUnitOfWork, device: Device, cap: int, base: Inventory
) -> tuple[Inventory, list[Interface], bool]:
    """Read one device's interfaces and fold their counts into an inventory.

    Args:
        uow: An open unit of work.
        device: The device.
        cap: Most interfaces to read (E.29).
        base: Sites, tags, and collectors already read.

    Returns:
        tuple: The inventory with this device only, its interfaces, and
        True when it has more interfaces than were read.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    where = InterfaceFilter(device_id=device.id)
    interfaces, truncated = pages(lambda p: uow.interfaces.list_page(p, where), cap)
    active = sum(1 for i in interfaces if i.lifecycle is InterfaceLifecycle.ACTIVE)
    counts = {device.id: (active, len(interfaces))} if interfaces else {}
    inventory = Inventory(
        (device,), False, base.sites, base.tags, base.collectors, counts, truncated
    )
    return inventory, interfaces, truncated


def read_interface_states(
    open_backend: Callable[[], TelemetryBackend] | None,
    device_id: DeviceId,
    collector_id: CollectorId,
    now: datetime,
    cap: int,
) -> tuple[dict[InterfaceId, CurrentInterfaceState], str | None]:
    """Read the current samples of a device's interfaces from one collector.

    Args:
        open_backend: Opens a telemetry view; ``None`` when there is no store.
        device_id: The device.
        collector_id: The collector whose samples to show.
        now: Reference time (UTC).
        cap: Most samples to read.

    Returns:
        tuple: Samples by interface, and why none could be read (or ``None``).

    Raises:
        None
    """
    if open_backend is None:
        return {}, "no telemetry database yet"
    try:
        current = open_backend().current
        states, _ = pages(
            lambda p: current.interface_states(device_id, collector_id, p, now=now), cap
        )
    except StorageError as exc:
        return {}, f"telemetry unreadable: {exc}"
    return {s.interface_id: s for s in states}, None


def _icmp_facts(j: Judgement) -> list[tuple[str, Reading]]:
    """Summarise the newest probe group (12.4 summary cards).

    Args:
        j: The shown position's judgement.

    Returns:
        list: Probes, latency, and jitter readings.

    Raises:
        None
    """
    o = j.icmp
    if o is None:
        none = missing("no probe group in the latest poll")
        return [("Probes", none), ("Latency", none), ("Jitter", none)]
    probes = Reading(f"{o.received}/{o.sent} answered, {o.loss_pct:.1f}% loss", o.received)
    lo, avg, med, hi = o.min_rtt_us, o.avg_rtt_us, o.median_rtt_us, o.max_rtt_us
    if lo is None or avg is None or med is None or hi is None:
        latency = missing("no probe answered")
    else:
        text = " / ".join(milliseconds(v) for v in (lo, avg, med, hi))
        latency = Reading(f"{text} (min/avg/median/max)", med / 1000)
    if o.jitter_us is None:
        jitter = missing("needs at least two replies")
    else:
        jitter = Reading(milliseconds(o.jitter_us), o.jitter_us / 1000)
    return [("Probes", probes), ("Latency", latency), ("Jitter", jitter)]


def _snmp_facts(j: Judgement) -> list[tuple[str, Reading]]:
    """Summarise the newest SNMP exchange.

    Args:
        j: The shown position's judgement.

    Returns:
        list: Request counts and elapsed time.

    Raises:
        None
    """
    o = j.snmp
    if o is None:
        return [("SNMP requests", missing("the latest poll made no SNMP request"))]
    text = (
        f"{o.request_count} sent, {o.response_count} answered, "
        f"{o.timeout_count} timed out, {o.retry_count} retries, {o.elapsed_ms} ms"
    )
    return [("SNMP requests", Reading(text, o.response_count))]


def facts(device: Device, row: DeviceRow, j: Judgement) -> tuple[tuple[str, Reading], ...]:
    """Return the device summary in display order (12.4).

    Args:
        device: The device record.
        row: Its list row.
        j: The shown position's judgement.

    Returns:
        tuple: ``(label, reading)`` pairs.

    Raises:
        None
    """
    family = f" ({device.address_family.value})" if device.address_family else ""
    poll = j.state.poll if j.state is not None else None
    interval = f"{j.interval_s} s"
    if poll is not None and poll.configured_interval_s != poll.effective_interval_s:
        interval += f" effective, {poll.configured_interval_s} s configured"
    tags = Reading(", ".join(row.tags), len(row.tags)) if row.tags else missing("no tags")
    comment = Reading(device.comment, device.comment) if device.comment else missing("none")
    return (
        ("Status", Reading(f"{row.status.icon} {row.status.label}", row.status.label)),
        ("Poll address", Reading(device.poll_address + family, device.poll_address)),
        ("Admin state", Reading(device.admin_state.value, device.admin_state.value)),
        ("Site", row.site),
        ("Tags", tags),
        ("Collector", row.collector),
        ("Reachability strategy", Reading(j.strategy.value, j.strategy.value)),
        ("Poll interval", Reading(interval, j.interval_s)),
        ("Last poll", row.last_poll),
        ("Freshness", row.freshness),
        ("Observation age", row.age),
        ("ICMP", row.icmp),
        *_icmp_facts(j),
        ("SNMP", row.snmp),
        *_snmp_facts(j),
        ("sysUpTime", row.uptime),
        ("Interfaces (active/total)", row.interfaces),
        ("System description", missing(SYSTEM_FACTS_GAP)),
        ("Comment", comment),
    )
