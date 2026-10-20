"""Utilization denominators and readings for interface rows (RATE-008..010, E.17 #15).

A percentage is shown only against a known capacity: an operator's
override, or the reported speed of a physical port or port-channel. A
tunnel, loopback, VLAN interface, or subinterface reports a configured or
nominal speed (IOS XE: 100 kb/s on many tunnels), so its traffic is shown
without a percentage unless ``capacity_override_bps`` is set (owner decision
2026-10-20). Missing never reads as zero: the reason is shown instead.

Dependencies:
    vakr.logic.capacity: The denominator rule.
    vakr.query.text, views: Formats and readings.

Safety:
    Pure.

Example:
    >>> utilization(500_000_000.0, 1_000_000_000).text
    '50.0%'
"""

from vakr.domain.interfaces import Interface
from vakr.logic.capacity import (
    Capacity,
    CapacityInputs,
    effective_capacity,
    reports_link_capacity,
)
from vakr.query.text import percent
from vakr.query.views import Reading, missing

UNKNOWN_SPEED = "speed unknown"
NOT_A_LINK = "no percentage on a logical interface; set capacity_override_bps to show one"


def no_capacity_reason(interface: Interface) -> str:
    """Say why an interface has no utilization denominator (RATE-010, E.17 #15).

    Args:
        interface: The interface.

    Returns:
        str: Its speed is not a link capacity (a tunnel, say), or it is unknown.

    Raises:
        None
    """
    reported = interface.if_speed or interface.if_high_speed
    if reported and not reports_link_capacity(interface.if_type, interface.if_name):
        return NOT_A_LINK
    return UNKNOWN_SPEED


def capacity_of(interface: Interface) -> Capacity | None:
    """Choose the utilization denominator in RATE-008 order.

    A contracted rate is never the denominator yet: no setting can declare
    it one (E.29 decision 7).

    Args:
        interface: The interface with its annotation and reported speeds.

    Returns:
        Capacity | None: The denominator, or ``None`` when speed is unknown.

    Raises:
        None
    """
    a = interface.annotation
    return effective_capacity(
        CapacityInputs(
            override_bps=a.capacity_override_bps,
            contracted_bps=a.contracted_bps,
            if_high_speed_mbps=interface.if_high_speed,
            if_speed_bps=interface.if_speed,
            link_speed=reports_link_capacity(interface.if_type, interface.if_name),
        )
    )


def utilization(
    bps: float | None, capacity_bps: int | None, reason: str = "", no_capacity: str = UNKNOWN_SPEED
) -> Reading:
    """Return a utilization reading; unavailable without both a rate and a capacity.

    Args:
        bps: The rate, if derivable.
        capacity_bps: The effective capacity, if known.
        reason: Why the rate is missing, when it is.
        no_capacity: Why there is no denominator, when there is none.

    Returns:
        Reading: Percent of capacity, or "—" with the reason.

    Raises:
        None
    """
    if capacity_bps is None:
        return missing(no_capacity)
    if bps is None:
        return missing(reason or "rate not available")
    value = 100.0 * bps / capacity_bps
    return Reading(percent(value), value)
