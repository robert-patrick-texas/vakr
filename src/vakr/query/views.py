"""Presentation-ready, immutable view models for the status CLI and the TUI (TUI-002..006).

Every value an operator sees is a ``Reading``: the text to print, the
machine value behind it, and, when the value is missing or unusable, the
reason in operator words. A missing value has ``value=None`` and text
``"—"``; it is never ``0`` (invariant 1). A value taken from stale evidence
has ``stale=True`` and says so in its text (TIME-006). Status always carries
a text label and an icon beside its colour (12.8, UI-011).

The same models feed ``vakr status`` (text and ``--json``) and ``vakr tui``,
so both front ends say exactly the same thing about a device.

Dependencies:
    dataclasses, datetime: Frozen records and aware times.
    vakr.domain: IDs and state vocabularies.

Safety:
    Views hold names, addresses, and measurements only. They never carry
    credential references or secrets (TUI-010).

Example:
    >>> missing("first sample").text, missing("first sample").value
    ('—', None)
    >>> Reading("12 s", 12.0).value
    12.0
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.enums import AdminState, StatusBucket, StatusColor
from vakr.domain.ids import CollectorId, DeviceId

DASH = "—"
"""Text shown for a missing value; never ``0``."""

type Scalar = float | int | str | None


@dataclass(frozen=True, slots=True)
class Reading:
    """One displayed value.

    Attributes:
        text: What to print, such as ``"12.3 Mb/s"`` or ``"—"``.
        value: Machine value behind the text; ``None`` when missing.
        reason: Why the value is missing or unusable, in operator words.
        stale: True when the value comes from stale evidence (E.3).
    """

    text: str
    value: Scalar = None
    reason: str | None = None
    stale: bool = False


def missing(reason: str) -> Reading:
    """Return the reading for a value that is not available.

    Args:
        reason: Why, in operator words, such as ``"speed unknown"``.

    Returns:
        Reading: ``"—"`` with no value and the reason.

    Raises:
        None
    """
    return Reading(DASH, None, reason)


@dataclass(frozen=True, slots=True)
class StatusView:
    """A device status as shown: bucket, colour, icon, and label together (7.7, 12.8).

    Attributes:
        bucket: Attention, Active, or Offline.
        color: Display colour; never shown without ``icon`` and ``label``.
        icon: A shape that differs per meaning, so grayscale still reads.
        label: Operator text such as ``"Down"`` or ``"Unknown — collector degraded"``.
        row: Matched Appendix E.2 row; ``None`` for a split composite.
        severity: Sort rank within the bucket, lower is more severe.
    """

    bucket: StatusBucket
    color: StatusColor
    icon: str
    label: str
    row: int | None
    severity: int


@dataclass(frozen=True, slots=True)
class PositionView:
    """One collector's own verdict on a device (STATE-004, MPOS-007).

    Attributes:
        collector_id: The collector.
        position: Its position label, such as ``east``.
        status: That collector's E.2 result.
        age: Age of its newest evidence.
    """

    collector_id: CollectorId
    position: str
    status: StatusView
    age: Reading


@dataclass(frozen=True, slots=True)
class DeviceRow:
    """One line of the device list (12.3, 13.2, TUI-002, TUI-004).

    Attributes:
        device_id: Device UUID.
        name: Display name.
        address: Poll address.
        site: Site name, or missing.
        tags: Tag names, sorted.
        admin_state: Operator-set state (7.5).
        status: Composite status across positions.
        freshness: ``fresh``, ``stale``, or missing (TIME-005).
        age: Age of the newest evidence.
        observed_at: Time of the newest evidence, if any (UTC).
        last_poll: Outcome and age of the latest poll record.
        icmp: ICMP reachability state.
        loss: ICMP loss percent of the latest probe group.
        latency: Median RTT of the latest probe group.
        snmp: SNMP state of the latest poll.
        uptime: Management-agent uptime (``sysUpTime``).
        collector: Position whose evidence the row shows.
        interfaces: Active / total interfaces in inventory.
        interval: Effective poll interval.
        positions: Every position's own verdict, in natural position order.
    """

    device_id: DeviceId
    name: str
    address: str
    site: Reading
    tags: tuple[str, ...]
    admin_state: AdminState
    status: StatusView
    freshness: Reading
    age: Reading
    observed_at: datetime | None
    last_poll: Reading
    icmp: Reading
    loss: Reading
    latency: Reading
    snmp: Reading
    uptime: Reading
    collector: Reading
    interfaces: Reading
    interval: Reading
    positions: tuple[PositionView, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectorHealthView:
    """A collector's latest self-report, as the header shows it (TUI-009, OBS-002).

    Attributes:
        collector_id: The collector.
        position: Its position label.
        state: Health state, ``unavailable`` when the report is stale, or missing.
        age: Age of the latest report.
        spool: Spool fill level, when reported.
    """

    collector_id: CollectorId
    position: str
    state: Reading
    age: Reading
    spool: Reading


@dataclass(frozen=True, slots=True)
class HealthView:
    """Storage and collector health shown above every list (13.2, 13.4).

    Attributes:
        metadata: Whether inventory could be read.
        telemetry: Whether telemetry could be read, and why not.
        collectors: Each collector's latest self-report.
        replication: Replication with the other position (27.5 item 7, MPOS-006);
            ``None`` on a node without a partner.
    """

    metadata: Reading
    telemetry: Reading
    collectors: tuple[CollectorHealthView, ...] = ()
    replication: Reading | None = None

    @property
    def telemetry_ok(self) -> bool:
        """Tell whether telemetry was readable.

        Args:
            None

        Returns:
            bool: True when telemetry state was read.

        Raises:
            None
        """
        return self.telemetry.value == "ok"


@dataclass(frozen=True, slots=True)
class BucketCounts:
    """How many matching devices fall in each bucket.

    Attributes:
        attention: Devices needing attention.
        active: Healthy devices.
        offline: Disabled, retired, or in maintenance.
    """

    attention: int = 0
    active: int = 0
    offline: int = 0


@dataclass(frozen=True, slots=True)
class DeviceListView:
    """One page of the device list plus everything shown around it.

    Attributes:
        rows: Devices on this page, in list order.
        total: Devices matching the request, across pages.
        offset: Index of the first row in the full ordered list.
        limit: Page size used.
        counts: Bucket counts over every matching device.
        truncated: True when more devices exist than one list reads (E.29).
        generated_at: When the view was computed (UTC).
        health: Storage and collector health.
    """

    rows: tuple[DeviceRow, ...]
    total: int
    offset: int
    limit: int
    counts: BucketCounts
    truncated: bool
    generated_at: datetime
    health: HealthView
