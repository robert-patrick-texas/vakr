"""The Appendix E.2 device-status rows, one predicate per row.

Rows are evaluated in order and the first match wins, so an operator's
intent (disabled, maintenance) beats evidence, missing evidence beats a
verdict, and a confirmed outage beats a degraded one:

====  ==========================================================  ===========
Row   Condition                                                   Result
====  ==========================================================  ===========
1     Admin state DISABLED or RETIRED                             gray
2     Admin state MAINTENANCE                                     gray
3     Stale, NOT_ATTEMPTED, unhealthy collector, or no result     amber
4     ICMP UNREACHABLE and SNMP did not answer                    red "Down"
5     ICMP UNREACHABLE and SNMP answered (``snmp_fallback``)      amber
6     ICMP IMPAIRED                                               amber
7     ICMP REACHABLE and SNMP TIMEOUT or ERROR                    amber
8     SNMP PARTIAL, partial discovery, or identity review         amber
9     Management-agent restart within the last 60 minutes         amber
10    Otherwise                                                   green "OK"
====  ==========================================================  ===========

SNMP "answered" means RESPONDING or PARTIAL: any successful response proves
the target was reachable (SNMP-013), so a partial reply is never "Down".
``icmp_only`` devices ignore SNMP: row 4 is ICMP UNREACHABLE alone, and rows
5, 7, 9 and the SNMP part of row 8 never match. ``snmp_only`` devices have
only SNMP as reachability evidence: row 4 is an SNMP TIMEOUT, row 7 an SNMP
ERROR, and rows 5 and 6 never match. Row 3 also covers a missing
reachability result, so a device is never green without evidence (UI-015).

``vakr.logic.status`` evaluates these rows in order.

Dependencies:
    dataclasses, datetime: Immutable evidence; aware times.
    vakr.logic.icmp: Impairment reasons for the row 6 label.
    vakr.domain.enums: Admin, ICMP, SNMP, collector, and status vocabularies.

Safety:
    Pure. Collector trouble matches row 3 (amber Unknown) before row 4 (red
    Down) can be reached (invariants 2 and 3, OBS-003).

Example:
    >>> from datetime import UTC, datetime
    >>> when = datetime(2026, 1, 1, tzinfo=UTC)
    >>> e = StatusEvidence(IcmpState.UNREACHABLE, SnmpState.TIMEOUT, when)
    >>> [row for row, _, _, rule in ROWS if rule(Facts(e, False, False, True, True))][:1]
    [4]
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from vakr.domain.enums import (
    AdminState,
    CollectorState,
    IcmpState,
    ReachabilityStrategy,
    SnmpState,
    StatusBucket,
    StatusColor,
)
from vakr.logic.icmp import Impairment

SNMP_ANSWERED = frozenset({SnmpState.RESPONDING, SnmpState.PARTIAL})
SNMP_FAILED = frozenset({SnmpState.TIMEOUT, SnmpState.ERROR})
ROW_SEVERITY = {4: 0, 3: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 2: 9, 1: 10}
"""Rank per E.2 row, most severe first; 1 is reserved for a split composite."""
PARTITION_SEVERITY = 1


@dataclass(frozen=True, slots=True)
class StatusEvidence:
    """What one collector knows about one device.

    Attributes:
        icmp: ICMP state of the newest observation.
        snmp: SNMP state of the newest observation.
        observed_at: When it was made; None if never.
        admin: Operator-set device state.
        strategy: Reachability strategy (ICMP-006..014).
        collector: Health of the collector.
        not_attempted: True when the last poll was ``NOT_ATTEMPTED``.
        partial_discovery: True when the last discovery was incomplete.
        identity_review_pending: True while an interface match awaits review.
        last_reset_at: When the last management-agent restart was detected.
        icmp_impairments: Why ICMP is IMPAIRED, for the row 6 label.
        interval_s: Effective polling interval, for freshness (E.3).
    """

    icmp: IcmpState
    snmp: SnmpState
    observed_at: datetime | None
    admin: AdminState = AdminState.ENABLED
    strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED
    collector: CollectorState = CollectorState.HEALTHY
    not_attempted: bool = False
    partial_discovery: bool = False
    identity_review_pending: bool = False
    last_reset_at: datetime | None = None
    icmp_impairments: tuple[Impairment, ...] = ()
    interval_s: float = 60.0


@dataclass(frozen=True, slots=True)
class Facts:
    """Evidence plus the derived facts every row needs.

    Attributes:
        e: The evidence.
        stale: No fresh observation (E.3).
        recent_reset: A restart was detected within the window.
        icmp: The strategy uses ICMP.
        snmp: The strategy uses SNMP.
    """

    e: StatusEvidence
    stale: bool
    recent_reset: bool
    icmp: bool
    snmp: bool


def _admin_offline(f: Facts) -> str | None:
    """Row 1: disabled or retired devices.

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    return {AdminState.DISABLED: "Disabled", AdminState.RETIRED: "Retired"}.get(f.e.admin)


def _maintenance(f: Facts) -> str | None:
    """Row 2: maintenance; collection continues and results are flagged.

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    return "Maintenance (collection continues)" if f.e.admin is AdminState.MAINTENANCE else None


def _unknown(f: Facts) -> str | None:
    """Row 3: no trustworthy evidence; the most specific reason is named.

    Args:
        f: Facts.

    Returns:
        str | None: ``"Unknown — <reason>"``, or None if the row does not match.

    Raises:
        None
    """
    e = f.e
    if e.collector is not CollectorState.HEALTHY:
        reason = f"collector {e.collector.value}"
    elif e.not_attempted:
        reason = "poll not attempted"
    elif f.stale:
        reason = "no fresh observation"
    elif f.icmp and e.icmp is IcmpState.UNKNOWN:
        reason = "no ICMP result"
    elif not f.icmp and e.snmp in (SnmpState.UNKNOWN, SnmpState.SKIPPED):
        reason = "no SNMP result"
    else:
        return None
    return f"Unknown — {reason}"


def _down(f: Facts) -> str | None:
    """Row 4: no reachability evidence at all from a healthy collector.

    Args:
        f: Facts.

    Returns:
        str | None: ``"Down"``, or None if the row does not match.

    Raises:
        None
    """
    if not f.icmp:
        return "Down" if f.e.snmp is SnmpState.TIMEOUT else None
    answered = f.snmp and f.e.snmp in SNMP_ANSWERED
    return "Down" if f.e.icmp is IcmpState.UNREACHABLE and not answered else None


def _icmp_lost_snmp_ok(f: Facts) -> str | None:
    """Row 5: ICMP lost but SNMP proves reachability (ICMP-013, SNMP-013).

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    match = f.icmp and f.snmp and f.e.icmp is IcmpState.UNREACHABLE
    return "ICMP lost, SNMP OK" if match and f.e.snmp in SNMP_ANSWERED else None


def _impaired(f: Facts) -> str | None:
    """Row 6: packet loss or latency, named from the ICMP impairments.

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    if not f.icmp or f.e.icmp is not IcmpState.IMPAIRED:
        return None
    latency = Impairment.LATENCY in f.e.icmp_impairments
    if Impairment.LOSS in f.e.icmp_impairments:
        return "Packet loss and latency" if latency else "Packet loss"
    # Without reasons from the ICMP summary, fall back to the E.2 wording.
    return "Latency" if latency else "Packet loss / Latency"


def _snmp_failing(f: Facts) -> str | None:
    """Row 7: the device is reachable but SNMP is failing.

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    if not f.icmp:
        failing = f.e.snmp is SnmpState.ERROR
    else:
        failing = f.snmp and f.e.icmp is IcmpState.REACHABLE and f.e.snmp in SNMP_FAILED
    return "SNMP failing" if failing else None


def _partial(f: Facts) -> str | None:
    """Row 8: some data is missing or an identity decision is pending.

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    snmp_partial = f.snmp and f.e.snmp is SnmpState.PARTIAL
    match = snmp_partial or f.e.partial_discovery or f.e.identity_review_pending
    return "Partial data" if match else None


def _restarted(f: Facts) -> str | None:
    """Row 9: the management agent restarted recently (UPTIME-005 wording).

    Args:
        f: Facts.

    Returns:
        str | None: The label, or None if the row does not match.

    Raises:
        None
    """
    return "Restarted" if f.snmp and f.recent_reset else None


ROWS: tuple[tuple[int, StatusBucket, StatusColor, Callable[[Facts], str | None]], ...] = (
    (1, StatusBucket.OFFLINE, StatusColor.GRAY, _admin_offline),
    (2, StatusBucket.OFFLINE, StatusColor.GRAY, _maintenance),
    (3, StatusBucket.ATTENTION, StatusColor.AMBER, _unknown),
    (4, StatusBucket.ATTENTION, StatusColor.RED, _down),
    (5, StatusBucket.ATTENTION, StatusColor.AMBER, _icmp_lost_snmp_ok),
    (6, StatusBucket.ATTENTION, StatusColor.AMBER, _impaired),
    (7, StatusBucket.ATTENTION, StatusColor.AMBER, _snmp_failing),
    (8, StatusBucket.ATTENTION, StatusColor.AMBER, _partial),
    (9, StatusBucket.ATTENTION, StatusColor.AMBER, _restarted),
)
