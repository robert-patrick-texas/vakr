"""Build one device-list row from inventory and per-collector judgements (12.3, 13.2, TUI-002).

The row shows the composite status across positions (MPOS-007) and the
details of the position with the newest evidence, labelled with that
position, so an operator always knows whose view they are reading
(STATE-004). Every value is a ``Reading``: missing evidence becomes "—"
with a reason, collector trouble becomes "unknown (collector)", and values
from stale evidence say "stale" (invariants 1-3, TIME-006).

Dependencies:
    vakr.query.evidence, labels, text, views: Judgement, words, formats, models.
    vakr.domain: Records and state vocabularies.

Safety:
    Pure: callers pass ``now``. No credential reference reaches a row (TUI-010).

Example:
    >>> icmp_readings.__name__, snmp_reading.__name__
    ('icmp_readings', 'snmp_reading')
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.catalog import Collector
from vakr.domain.enums import CollectorState, Freshness, IcmpState, SnmpState, UptimeContinuity
from vakr.domain.inventory import Device
from vakr.logic.natural import natural_key
from vakr.logic.status import DeviceStatus
from vakr.query.evidence import Judgement
from vakr.query.labels import ICMP_WORDS, SNMP_WORDS, mark_stale, status_view
from vakr.query.snapshot import Inventory
from vakr.query.text import age_text, interval_text, milliseconds, percent, uptime_text
from vakr.query.views import DeviceRow, PositionView, Reading, missing


@dataclass(frozen=True, slots=True)
class Seen:
    """One collector's judgement of a device, with the collector record.

    Attributes:
        collector: The collector, or ``None`` when no collector is registered.
        judgement: Its evidence and status.
    """

    collector: Collector | None
    judgement: Judgement


def _collector_unknown(j: Judgement) -> bool:
    """Tell whether collector trouble, not the target, explains missing evidence.

    Args:
        j: The judgement.

    Returns:
        bool: True when the collector condition is not healthy.

    Raises:
        None
    """
    return j.collector is not CollectorState.HEALTHY


def icmp_readings(j: Judgement) -> tuple[Reading, Reading, Reading]:
    """Return the ICMP state, loss, and median-latency readings.

    Args:
        j: The judgement.

    Returns:
        tuple[Reading, Reading, Reading]: State, loss percent, median RTT.

    Raises:
        None
    """
    o = j.icmp
    if o is None or o.state is IcmpState.UNKNOWN:
        if _collector_unknown(j):
            state = Reading("unknown (collector)", None, f"collector {j.collector.value}")
        else:
            state = missing("no ICMP result in the latest poll")
        return state, missing("no probe result"), missing("no probe result")
    loss = Reading(percent(o.loss_pct), o.loss_pct)
    if o.median_rtt_us is None:
        latency = missing("no probe answered")
    else:
        latency = Reading(milliseconds(o.median_rtt_us), o.median_rtt_us / 1000)
    return Reading(ICMP_WORDS[o.state], o.state.value), loss, latency


def snmp_reading(j: Judgement) -> Reading:
    """Return the SNMP state reading for the newest poll.

    Args:
        j: The judgement.

    Returns:
        Reading: The state; "not polled" when the newest poll made no SNMP
        request (for example, gated off by ICMP).

    Raises:
        None
    """
    o = j.snmp
    if o is None or o.state is SnmpState.UNKNOWN:
        if _collector_unknown(j):
            return Reading("unknown (collector)", None, f"collector {j.collector.value}")
        return missing("the latest poll made no SNMP request")
    return Reading(SNMP_WORDS[o.state], o.state.value)


def uptime_reading(j: Judgement) -> Reading:
    """Return management-agent uptime from ``sysUpTime`` (UPTIME-003).

    Args:
        j: The judgement.

    Returns:
        Reading: Uptime in seconds, flagged when a restart was detected.

    Raises:
        None
    """
    o = j.snmp
    if o is None or o.sys_uptime_ticks is None:
        return missing("sysUpTime not read in the latest poll")
    text = uptime_text(o.sys_uptime_ticks)
    if o.uptime_continuity is UptimeContinuity.RESET_DETECTED:
        text = f"{text} (restarted)"
    return Reading(text, o.sys_uptime_ticks / 100)


def _poll_reading(j: Judgement, now: datetime) -> Reading:
    """Return the latest poll's outcome and age.

    Args:
        j: The judgement.
        now: Reference time (UTC).

    Returns:
        Reading: Such as ``"42 s ago, complete"``; missing without a poll record.

    Raises:
        None
    """
    poll = j.state.poll if j.state is not None else None
    if poll is None:
        return missing("no poll recorded")
    age = (now - poll.scheduled_at).total_seconds()
    execution = poll.execution.value.replace("_", " ")
    return Reading(f"{age_text(age)} ago, {execution}", poll.execution.value)


def _position_view(seen: Seen) -> PositionView | None:
    """Return one position's verdict for the comparison list.

    Args:
        seen: One collector's judgement.

    Returns:
        PositionView | None: ``None`` when no collector is registered.

    Raises:
        None
    """
    if seen.collector is None:
        return None
    state = seen.judgement.state
    age = Reading(age_text(state.age.total_seconds()), state.age.total_seconds()) if state else None
    return PositionView(
        seen.collector.id,
        seen.collector.position,
        status_view(seen.judgement.status),
        age or missing("no observation from this position"),
    )


def in_position_order(seen: list[Seen]) -> list[Seen]:
    """Sort judgements by natural position order (``east`` before ``west2``).

    Args:
        seen: Judgements.

    Returns:
        list[Seen]: Sorted copy.

    Raises:
        None
    """
    return sorted(seen, key=lambda s: natural_key(s.collector.position if s.collector else ""))


def shown(seen: list[Seen]) -> Seen:
    """Pick the position whose details a row shows: the newest evidence wins.

    Args:
        seen: Every position's judgement, at least one, in position order.

    Returns:
        Seen: Newest ``observed_at``; ties and no evidence go to the first position.

    Raises:
        None
    """
    dated = [(s.judgement.state.observed_at, s) for s in seen if s.judgement.state is not None]
    if not dated:
        return seen[0]
    # max() keeps the first of equal keys, so ties go to the earlier position.
    return max(dated, key=lambda pair: pair[0])[1]


def device_row(
    device: Device, inventory: Inventory, seen: list[Seen], status: DeviceStatus, now: datetime
) -> DeviceRow:
    """Assemble one list row.

    Args:
        device: The device.
        inventory: Sites, tags, and interface counts.
        seen: Every position's judgement, at least one.
        status: The composite status (MPOS-007).
        now: Reference time (UTC).

    Returns:
        DeviceRow: The row.

    Raises:
        None
    """
    seen = in_position_order(seen)
    best = shown(seen)
    j = best.judgement
    stale = j.state is not None and j.state.freshness is Freshness.STALE
    icmp, loss, latency = (mark_stale(r, stale) for r in icmp_readings(j))
    site = inventory.sites.get(device.site_id) if device.site_id else None
    counts = inventory.interface_counts.get(device.id)
    if counts is not None:
        interfaces = Reading(f"{counts[0]}/{counts[1]}", counts[0])
    elif inventory.counts_truncated:
        interfaces = missing("too many interfaces to count")
    else:
        interfaces = missing("no interfaces in inventory")
    age_s = j.state.age.total_seconds() if j.state else None
    return DeviceRow(
        device_id=device.id,
        name=device.name,
        address=device.poll_address,
        site=Reading(site.name, site.name) if site else missing("no site assigned"),
        tags=tuple(sorted(inventory.tags[t].name for t in device.tags if t in inventory.tags)),
        admin_state=device.admin_state,
        status=status_view(status),
        freshness=Reading(j.state.freshness.value, j.state.freshness.value)
        if j.state
        else missing("never observed"),
        age=Reading(age_text(age_s), age_s) if age_s is not None else missing("never observed"),
        observed_at=j.state.observed_at if j.state else None,
        last_poll=_poll_reading(j, now),
        icmp=icmp,
        loss=loss,
        latency=latency,
        snmp=mark_stale(snmp_reading(j), stale),
        uptime=mark_stale(uptime_reading(j), stale),
        collector=Reading(best.collector.position, best.collector.name)
        if best.collector
        else missing("no collector registered"),
        interfaces=interfaces,
        interval=Reading(interval_text(j.interval_s), j.interval_s),
        positions=tuple(p for s in seen if (p := _position_view(s)) is not None),
    )
