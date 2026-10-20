"""Operator-language report for ``vakr snmp poll``, including rates from two polls.

One poll shows Stage 1 (agent uptime and interface states) and Stage 2 (raw
cumulative counters). Counters only mean something as a difference, so with
two polls the report derives traffic rates exactly as the collector does,
through ``vakr.logic.rates``, and explains every unavailable rate instead
of showing ``0`` (blueprint 18.3: "why a rate is unavailable after a reset").

Dependencies:
    vakr.collectors.snmp: Poll results and uptime continuity.
    vakr.logic: Capacity and rate derivation.
    vakr.cli.snmp_report: Shared formatting.

Safety:
    Results never contain a community.

Example:
    >>> rate_text(40_000_000.0)
    '40.0 Mb/s'
"""

from collections.abc import Sequence

from vakr.cli.snmp_report import UNKNOWN, UPTIME_NOTE, effort_text, table, uptime_text
from vakr.collectors.snmp.counters import InterfaceSample
from vakr.collectors.snmp.observations import UptimeReading, uptime_continuity
from vakr.collectors.snmp.poll import SnmpPollResult, snmp_state
from vakr.discovery.model import DiscoveryResult
from vakr.domain.quality import Quality
from vakr.logic.baseline import RateContext
from vakr.logic.capacity import CapacityInputs
from vakr.logic.rates import RateResult, compute_rates

REASONS = {
    Quality.BASELINE_UPTIME_RESET: "the SNMP agent restarted between the samples",
    Quality.BASELINE_DISCONTINUITY: "the device reset this interface's counters",
    Quality.BASELINE_WIDTH_CHANGE: "the counter width changed between the samples",
    Quality.BASELINE_DECREASE: "a counter went backwards without a reset to explain it",
    Quality.BASELINE_GAP: "the samples are too far apart",
    Quality.BASELINE_ELAPSED_INVALID: "no time passed between the samples",
    Quality.BASELINE_MISSING_PRIOR: "the first sample is missing",
    Quality.BASELINE_FIRST_SAMPLE: "only one sample",
}
"""Why a rate is unavailable, in operator language (RATE-003, RATE-004)."""


def rate_text(bps: float | None) -> str:
    """Render bits per second.

    Args:
        bps: The rate, or ``None``.

    Returns:
        str: Such as ``40.0 Mb/s``, or ``?``.

    Raises:
        None
    """
    if bps is None:
        return UNKNOWN
    for unit, scale in (("Gb/s", 1e9), ("Mb/s", 1e6), ("kb/s", 1e3)):
        if bps >= scale:
            return f"{bps / scale:.1f} {unit}"
    return f"{bps:.0f} b/s"


def _why(rates: RateResult) -> str:
    """Name the first reason a rate is unavailable.

    Args:
        rates: The derivation result.

    Returns:
        str: The reason, or ``unavailable``.

    Raises:
        None
    """
    flags = rates.quality | rates.in_octets.quality | rates.out_octets.quality
    return next((text for flag, text in REASONS.items() if flags & flag), "unavailable")


def derive_rates(
    discovery: DiscoveryResult, first: SnmpPollResult, second: SnmpPollResult
) -> dict[int, RateResult]:
    """Derive each interface's rates between two polls, as the collector would.

    Args:
        discovery: For each interface's speed (the capacity denominator).
        first: The earlier poll.
        second: The later poll.

    Returns:
        dict[int, RateResult]: Rates by ifIndex.

    Raises:
        None
    """
    ticks = (first.stage1.sys_uptime_ticks, second.stage1.sys_uptime_ticks)
    readings = [
        None if t is None else UptimeReading(t, p.stage1.sampled_at)
        for t, p in zip(ticks, (first, second), strict=True)
    ]
    continuity = uptime_continuity(readings[0], readings[1])[0]
    interval = max(1.0, second.started_monotonic - first.started_monotonic)
    speeds = {i.if_index: (i.if_high_speed, i.if_speed) for i in discovery.interfaces}
    before = {s.if_index: s for s in (first.stage2.samples if first.stage2 else ())}
    found: dict[int, RateResult] = {}
    for sample in second.stage2.samples if second.stage2 else ():
        prev = before.get(sample.if_index)
        high, speed = speeds.get(sample.if_index, (None, None))
        context = RateContext(
            elapsed_s=None if prev is None else sample.sampled_monotonic - prev.sampled_monotonic,
            interval_s=interval,
            continuity=continuity,
            capacity=CapacityInputs(if_high_speed_mbps=high, if_speed_bps=speed),
        )
        previous = None if prev is None else prev.counter_sample()
        found[sample.if_index] = compute_rates(previous, sample.counter_sample(), context)
    return found


def _sample_rows(
    samples: Sequence[InterfaceSample], names: dict[int, str], rates: dict[int, RateResult] | None
) -> list[str]:
    """Render Stage 2 samples, with rates when two polls were made.

    Args:
        samples: The samples.
        names: ``ifName`` by ifIndex.
        rates: Rates by ifIndex, or ``None`` for a single poll.

    Returns:
        list[str]: Table lines.

    Raises:
        None
    """
    head = ["IFINDEX", "NAME", "COUNTERS", "IN-OCTETS", "OUT-OCTETS", "IN-PKTS", "OUT-PKTS"]
    head += ["IN-ERR", "OUT-ERR"] + ([] if rates is None else ["IN-RATE", "OUT-RATE"])
    rows = [tuple(head)]
    for s in samples:
        values = (s.in_octets, s.out_octets, s.in_packets, s.out_packets, s.in_errors, s.out_errors)
        row = [str(s.if_index), names.get(s.if_index, UNKNOWN), s.family.value]
        row += [UNKNOWN if v is None else str(v) for v in values]
        if rates is not None:
            r = rates.get(s.if_index)
            pair = (UNKNOWN, UNKNOWN) if r is None else (r.in_octets.rate, r.out_octets.rate)
            if r is not None and r.in_octets.rate is None and r.out_octets.rate is None:
                pair = (f"unavailable: {_why(r)}", "")
            row += [p if isinstance(p, str) else rate_text(p) for p in pair]
        rows.append(tuple(row))
    return table(rows)


def poll_report(
    discovery: DiscoveryResult, polls: Sequence[SnmpPollResult], credential: str
) -> list[str]:
    """Render one or two polls of a device.

    Args:
        discovery: The discovery that preceded the polls.
        polls: One or two poll results.
        credential: The credential name used.

    Returns:
        list[str]: Report lines.

    Raises:
        None
    """
    last = polls[-1]
    s1, s2 = last.stage1, last.stage2
    up = sum(status.up_up for status in s1.statuses)
    state = snmp_state(last).value
    lines = [
        f"SNMP poll of {last.target} with credential {credential!r} (SNMPv2c): {state}",
        f"Stage 1: sysUpTime {uptime_text(s1.sys_uptime_ticks)} ({UPTIME_NOTE});",
        f"  {len(s1.statuses)} interfaces checked, {up} admin up and oper up;",
        f"  {effort_text(s1.stats)}",
    ]
    if s2 is None:
        return [*lines, "Stage 2 was not run: Stage 1 got no answer."]
    lines.append(f"Stage 2: {len(s2.samples)} up/up interfaces sampled; {effort_text(s2.stats)}")
    names = {i.if_index: i.if_name or UNKNOWN for i in discovery.interfaces}
    rates = derive_rates(discovery, polls[0], last) if len(polls) > 1 else None
    lines += _sample_rows(s2.samples, names, rates)
    if rates is None:
        lines.append("Counters are cumulative; add --rate-after SECONDS to see traffic rates.")
    if s2.changes:
        learned = ", ".join(f"{c.if_index} {c.capability}" for c in s2.changes)
        lines.append(
            f"Learned unsupported counters (not requested again until re-probe): {learned}"
        )
    if last.quality & (Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS):
        lines.append("Some values did not arrive; they are shown as ?, never as 0.")
    return lines
