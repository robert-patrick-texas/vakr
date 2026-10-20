"""Interface-table rows and their order for the device screen (TUI-005, TUI-006, RATE-008..010).

Order (E.29 decision 6): sticky interfaces first, because ``sticky`` means
"pin to the top of the device's interface list"; then WAN circuits; then
interest class ``primary`` (the high-priority annotation, META-011); then
the operator's ``display_rank``; then the name in natural order
(``Gi1/0/2`` before ``Gi1/0/10``); then the interface UUID, so the order is
total. ``ifIndex`` never decides anything (invariant 6).

Utilization is shown only against a known effective capacity, and the row
names the denominator (RATE-008); without one it is "—" with the reason,
never a guess (RATE-010, ``vakr.query.interface_capacity``). A rate that
could not be derived says why: an agent restart, a counter discontinuity,
a first sample (RATE-004, 18.3).

Dependencies:
    vakr.query.interface_capacity, vakr.logic.natural: Denominator and name order.
    vakr.query.labels, text, views: Words, formats, and models.

Safety:
    Pure.

Example:
    >>> utilization(500_000_000.0, 1_000_000_000).text
    '50.0%'
"""

from dataclasses import replace
from typing import NamedTuple

from vakr.domain.enums import Freshness, InterestClass
from vakr.domain.interfaces import Interface
from vakr.domain.quality import Quality
from vakr.domain.state import CurrentInterfaceState
from vakr.logic.capacity import Capacity
from vakr.logic.natural import natural_key
from vakr.query.detail_views import InterfaceRow
from vakr.query.interface_capacity import capacity_of, no_capacity_reason, utilization
from vakr.query.labels import if_state_text, mark_stale, rate_gap_reason
from vakr.query.text import age_text, bits_per_second, packets_per_second
from vakr.query.views import DASH, Reading, missing

type OrderKey = tuple[bool, bool, bool, tuple[int, int], tuple[tuple[str | int, ...], str], str]


def display_name(interface: Interface) -> str:
    """Return the name an operator knows the interface by.

    Args:
        interface: The interface.

    Returns:
        str: ``ifName``, else ``ifDescr``, else ``"(no name)"``.

    Raises:
        None
    """
    return interface.if_name or interface.if_descr or "(no name)"


def order_key(interface: Interface) -> OrderKey:
    """Return the TUI-006 sort key: sticky, WAN, primary, rank, natural name, UUID.

    Args:
        interface: The interface.

    Returns:
        OrderKey: Smaller sorts first.

    Raises:
        None
    """
    a = interface.annotation
    rank = (0, a.display_rank) if a.display_rank is not None else (1, 0)
    return (
        not a.sticky,
        not a.is_wan_circuit,
        a.interest is not InterestClass.PRIMARY,
        rank,
        natural_key(display_name(interface)),
        str(interface.id),
    )


def _rate(value: float | None, unit: str, reason: str) -> Reading:
    """Return a bit or packet rate reading.

    Args:
        value: The rate, if derivable.
        unit: ``"bps"`` or ``"pps"``.
        reason: Why it is missing, when it is.

    Returns:
        Reading: The formatted rate, or "—" with the reason.

    Raises:
        None
    """
    if value is None:
        return missing(reason)
    return Reading(bits_per_second(value) if unit == "bps" else packets_per_second(value), value)


def _errors(state: CurrentInterfaceState, reason: str) -> Reading:
    """Return input/output errors since the previous sample.

    Args:
        state: The sample.
        reason: Why a delta is missing, when it is.

    Returns:
        Reading: ``"in/out"`` counts; the value is their sum when both are known.

    Raises:
        None
    """
    o = state.observation
    deltas = (o.in_error_delta, o.out_error_delta)
    if all(d is None for d in deltas):
        return missing(reason)
    text = "/".join(DASH if d is None else str(d) for d in deltas)
    total = None if None in deltas else sum(d for d in deltas if d is not None)
    return Reading(text, total, None if total is not None else reason)


class _Measured(NamedTuple):
    """The sampled columns of one row, in ``InterfaceRow`` field order."""

    state: Reading
    in_rate: Reading
    out_rate: Reading
    in_util: Reading
    out_util: Reading
    in_pps: Reading
    out_pps: Reading
    errors: Reading
    age: Reading


def _measured(
    state: CurrentInterfaceState, cap: Capacity | None, interface: Interface
) -> _Measured:
    """Return the sampled columns of a row with a current sample.

    Args:
        state: The sample.
        cap: The effective capacity, if known.
        interface: The interface, for why it may have no capacity.

    Returns:
        _Measured: Readings, each marked stale when the sample is stale.

    Raises:
        None
    """
    o = state.observation
    why = rate_gap_reason(o.quality)
    cap_bps = cap.bps if cap else None
    over = Quality.RATE_EXCEEDS_CAPACITY in o.quality
    no_cap = no_capacity_reason(interface)
    in_util, out_util = (
        _over(utilization(v, cap_bps, why, no_cap), over) for v in (o.in_bps, o.out_bps)
    )
    if_state = if_state_text(o.admin_status, o.oper_status)
    age = state.age.total_seconds()
    readings = _Measured(
        state=Reading(if_state, if_state),
        in_rate=_rate(o.in_bps, "bps", why),
        out_rate=_rate(o.out_bps, "bps", why),
        in_util=in_util,
        out_util=out_util,
        in_pps=_rate(o.in_pps, "pps", why),
        out_pps=_rate(o.out_pps, "pps", why),
        errors=_errors(state, why),
        age=Reading(age_text(age), age),
    )
    stale = state.freshness is Freshness.STALE
    return _Measured(*(mark_stale(r, stale) for r in readings))


def _over(reading: Reading, over: bool) -> Reading:
    """Mark a utilization above the RATE-009 plausibility bound; it is kept, not clipped.

    Args:
        reading: The utilization.
        over: Whether the sample carries ``RATE_EXCEEDS_CAPACITY``.

    Returns:
        Reading: ``"... (over capacity)"`` when flagged and present.

    Raises:
        None
    """
    if not over or reading.value is None:
        return reading
    return replace(reading, text=f"{reading.text} (over capacity)")


def interface_row(
    interface: Interface, state: CurrentInterfaceState | None, position: str
) -> InterfaceRow:
    """Assemble one interface-table row.

    Args:
        interface: Inventory record.
        state: Its current sample from the shown position, if any.
        position: That position's label, for the "no sample" reason.

    Returns:
        InterfaceRow: The row.

    Raises:
        None
    """
    a = interface.annotation
    cap = capacity_of(interface)
    flags = (("sticky", a.sticky), ("WAN", a.is_wan_circuit))
    flags += (("primary", a.interest is InterestClass.PRIMARY),)
    if state is None:
        m = _Measured(*([missing(f"no sample from {position}")] * len(_Measured._fields)))
    else:
        m = _measured(state, cap, interface)
    return InterfaceRow(
        interface_id=interface.id,
        name=display_name(interface),
        alias=interface.if_alias,
        markers=tuple(name for name, on in flags if on),
        lifecycle=interface.lifecycle,
        capacity=Reading(f"{bits_per_second(cap.bps)} ({cap.label})", cap.bps)
        if cap
        else missing(no_capacity_reason(interface)),
        state=m.state,
        in_rate=m.in_rate,
        out_rate=m.out_rate,
        in_util=m.in_util,
        out_util=m.out_util,
        in_pps=m.in_pps,
        out_pps=m.out_pps,
        errors=m.errors,
        age=m.age,
    )
