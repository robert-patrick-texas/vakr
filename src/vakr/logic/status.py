"""Device status: per collector (E.2), composite across positions (MPOS-007), list order (7.7).

``device_status`` evaluates the E.2 rows in ``vakr.logic.status_table`` for
one collector's evidence; the first match wins. ``composite_status`` merges
the per-position results of a two-position deployment without merging their
evidence (MPOS-007, MPOL-004). ``device_sort_key`` orders the device list:
bucket, then severity, then most recent change, then natural name order.

Composite rules (E.2): red "Down" only when every collector with usable
evidence says Down; amber "Reachable from <position> only" when some say
Down and others reach the device, which is often the first sign of a
partition; otherwise the most severe result. A collector whose result is
row 3 (unknown) has no usable evidence, so it neither confirms an outage
nor turns a healthy device amber; it decides only when no collector knows.

Dependencies:
    dataclasses, datetime, enum: Immutable results; aware times.
    vakr.logic.freshness, vakr.logic.natural, vakr.logic.status_table:
        Staleness, name order, and the E.2 rows.
    vakr.domain.enums: Status buckets and colours.

Safety:
    Pure: callers pass ``now``. Collector trouble yields amber Unknown, never
    red Down (invariants 2 and 3, OBS-003).

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.enums import IcmpState, SnmpState
    >>> now = datetime(2026, 1, 1, tzinfo=UTC)
    >>> s = device_status(StatusEvidence(IcmpState.UNREACHABLE, SnmpState.SKIPPED, now), now)
    >>> s.label, s.color.value, s.row
    ('Down', 'red', 4)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vakr.domain.enums import ReachabilityStrategy, StatusBucket, StatusColor
from vakr.logic.freshness import assess_freshness, require_aware
from vakr.logic.natural import natural_key
from vakr.logic.status_table import PARTITION_SEVERITY, ROW_SEVERITY, ROWS, Facts, StatusEvidence

RECENT_RESTART_WINDOW_S = 3600.0  # E.2: status.recent_restart_window = "60m"
_BUCKET_ORDER = {StatusBucket.ATTENTION: 0, StatusBucket.ACTIVE: 1, StatusBucket.OFFLINE: 2}
_KNOWN_ROWS = frozenset(range(4, 11))  # rows backed by usable evidence


class CompositeRule(StrEnum):
    """Which MPOS-007 rule produced a composite status."""

    NO_EVIDENCE = "no_evidence"  # no position has reported
    ADMIN = "admin"  # rows 1-2: operator intent, the same at every position
    ALL_UNKNOWN = "all_unknown"  # every position is row 3
    ALL_DOWN = "all_down"  # every position with evidence says Down
    PARTITION = "partition"  # Down from some positions, reachable from others
    MOST_SEVERE = "most_severe"


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """Device-list status with the reason for it.

    Attributes:
        bucket: Attention, Active, or Offline (7.7).
        color: Display colour; always paired with ``label`` (UI-011).
        label: Short operator text, such as ``"Down"``.
        row: Matched E.2 row number; None for a split composite.
        severity: Sort rank, lower is more severe.
    """

    bucket: StatusBucket
    color: StatusColor
    label: str
    row: int | None
    severity: int


def device_status(
    evidence: StatusEvidence, now: datetime, restart_window_s: float = RECENT_RESTART_WINDOW_S
) -> DeviceStatus:
    """Evaluate the E.2 table for one collector's evidence; the first match wins.

    Args:
        evidence: What the collector knows about the device.
        now: The current time.
        restart_window_s: How long a detected restart keeps row 9 active.

    Returns:
        DeviceStatus: Bucket, colour, label, matched row, and severity.

    Raises:
        ValueError: If a datetime is naive or the interval is not positive.
    """
    stale = assess_freshness(evidence.observed_at, now, evidence.interval_s).stale
    reset_at = evidence.last_reset_at
    if reset_at is not None:
        require_aware(reset_at, "last_reset_at")
    recent = reset_at is not None and (now - reset_at).total_seconds() <= restart_window_s
    strategy = evidence.strategy
    facts = Facts(
        evidence,
        stale,
        recent,
        icmp=strategy is not ReachabilityStrategy.SNMP_ONLY,
        snmp=strategy is not ReachabilityStrategy.ICMP_ONLY,
    )
    for row, bucket, color, rule in ROWS:
        if (label := rule(facts)) is not None:
            return DeviceStatus(bucket, color, label, row, ROW_SEVERITY[row])
    return DeviceStatus(StatusBucket.ACTIVE, StatusColor.GREEN, "OK", 10, ROW_SEVERITY[10])


@dataclass(frozen=True, slots=True)
class PositionStatus:
    """One position's status for a device (MPOS-007).

    Attributes:
        position: Position label, such as ``"east"`` (27.5 item 8).
        status: That collector's E.2 result.
    """

    position: str
    status: DeviceStatus


@dataclass(frozen=True, slots=True)
class CompositeStatus:
    """Composite device status that keeps every position's evidence (MPOL-004).

    Attributes:
        status: The merged status; ``row`` is None for a partition.
        rule: The composite rule that produced it.
        positions: Per-position results in natural position order.
    """

    status: DeviceStatus
    rule: CompositeRule
    positions: tuple[PositionStatus, ...]


def composite_status(per_position: Sequence[PositionStatus]) -> CompositeStatus:
    """Merge per-position results into one device status (E.2, MPOS-007).

    Args:
        per_position: Each position's result for the same device.

    Returns:
        CompositeStatus: The merged status, the rule used, and the inputs.

    Raises:
        None
    """
    ordered = tuple(sorted(per_position, key=lambda p: natural_key(p.position)))
    if not ordered:
        unknown = DeviceStatus(
            StatusBucket.ATTENTION,
            StatusColor.AMBER,
            "Unknown — no observation",
            3,
            ROW_SEVERITY[3],
        )
        return CompositeStatus(unknown, CompositeRule.NO_EVIDENCE, ordered)
    statuses = [p.status for p in ordered]
    if admin := [s for s in statuses if s.row in (1, 2)]:
        # Rows 1-2 come from shared metadata; if replication lags, the earlier row wins.
        return CompositeStatus(min(admin, key=lambda s: s.row or 0), CompositeRule.ADMIN, ordered)
    known = [p for p in ordered if p.status.row in _KNOWN_ROWS]
    if not known:
        return CompositeStatus(statuses[0], CompositeRule.ALL_UNKNOWN, ordered)
    up = [p.position for p in known if p.status.row != 4]
    if len(up) == len(known):
        best = min((p.status for p in known), key=lambda s: s.severity)
        return CompositeStatus(best, CompositeRule.MOST_SEVERE, ordered)
    if not up:
        return CompositeStatus(known[0].status, CompositeRule.ALL_DOWN, ordered)
    label = f"Reachable from {', '.join(up)} only"
    split = DeviceStatus(StatusBucket.ATTENTION, StatusColor.AMBER, label, None, PARTITION_SEVERITY)
    return CompositeStatus(split, CompositeRule.PARTITION, ordered)


def device_sort_key(
    status: DeviceStatus, last_change: datetime | None, display_name: str
) -> tuple[int, int, tuple[int, float], tuple[tuple[str | int, ...], str]]:
    """Return the device-list sort key (7.7): bucket, severity, recency, name.

    Args:
        status: The device's status (per position or composite).
        last_change: When its state last changed; None sorts after any time.
        display_name: Name shown in the list, compared in natural order.

    Returns:
        tuple: Attention before Active before Offline; more severe first;
        most recent change first; then ``ge-0/0/2`` before ``ge-0/0/10``.

    Raises:
        ValueError: If ``last_change`` is naive.
    """
    if last_change is None:
        recency = (1, 0.0)
    else:
        require_aware(last_change, "last_change")
        recency = (0, -last_change.timestamp())
    return _BUCKET_ORDER[status.bucket], status.severity, recency, natural_key(display_name)
