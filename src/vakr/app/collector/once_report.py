"""Operator-language report of one ``vakr collector once`` cycle (18.3, OBS-002).

The report says what the device did and what the collector could not do,
separately, because "the router is down" and "vakr could not look" need
different people (OBS-002). It ends with the E.2 verdict the views would
show for this one cycle, and the exit status follows the Nagios convention:
0 OK, 1 warning (amber), 2 critical (red Down), 3 unknown (collector-side
trouble, or no evidence).

Missing values are shown as missing (``?``), never as 0, and the single
cycle explains why it has counters but no rates.

Dependencies:
    vakr.logic.status: The E.2 decision table.
    vakr.cli.icmp_render, vakr.cli.snmp_report: Shared number formatting.

Safety:
    Prints names, addresses, states, and counters only.

Example:
    >>> EXIT_UNKNOWN
    3
"""

from datetime import datetime

from vakr.app.collector.once import OnceResult
from vakr.cli.icmp_render import fmt_ms
from vakr.cli.snmp_report import uptime_text
from vakr.domain.enums import CollectorState, IcmpState, SnmpState, StatusColor, UptimeContinuity
from vakr.domain.observations import IcmpObservation, InterfaceObservation, SnmpDeviceObservation
from vakr.logic.status import StatusEvidence, device_status
from vakr.scheduler.records import execution_of
from vakr.scheduler.steps import StepStatus

EXIT_UNKNOWN = 3
MAX_ROWS = 40
"""Interfaces listed at most; the count of the rest is printed."""

_SNMP_WORDS = {
    SnmpState.RESPONDING: "responding",
    SnmpState.PARTIAL: "answered part of the request",
    SnmpState.TIMEOUT: "no answer (timeout)",
    SnmpState.ERROR: "answered, but with nothing usable",
    SnmpState.SKIPPED: "skipped: fewer than 2 ICMP replies under icmp_gated (ICMP-007)",
    SnmpState.UNKNOWN: "unknown: the collector could not query it",
}


def _first[T](result: OnceResult, kind: type[T]) -> T | None:
    """Return the cycle's first observation of one type.

    Args:
        result: The cycle.
        kind: The observation type.

    Returns:
        T | None: The observation, if any.

    Raises:
        None
    """
    return next((o for o in result.progress.observations if isinstance(o, kind)), None)


def _icmp_line(result: OnceResult) -> str:
    """Describe the ICMP step.

    Args:
        result: The cycle.

    Returns:
        str: One line.

    Raises:
        None
    """
    obs = _first(result, IcmpObservation)
    errors = [s.error for s in result.progress.steps if s.status is StepStatus.ERROR]
    if obs is None:
        why = errors[0] if errors else "not configured for this device"
        return f"ICMP: no probe was sent ({why}); collector-side, not a device fault"
    got = [r for r in obs.rtts_us if r is not None and r >= 0]
    sent = sum(r is not None for r in obs.rtts_us)
    return (
        f"ICMP: {len(got)}/{sent} replies, median {fmt_ms(obs.median_rtt_us)} "
        f"(min {fmt_ms(obs.min_rtt_us)}, max {fmt_ms(obs.max_rtt_us)}, "
        f"jitter {fmt_ms(obs.jitter_us)}): {obs.state.value}"
    )


def _snmp_line(result: OnceResult) -> str:
    """Describe the SNMP device observation.

    Args:
        result: The cycle.

    Returns:
        str: One line.

    Raises:
        None
    """
    obs = _first(result, SnmpDeviceObservation)
    if obs is None:
        return "SNMP: not polled (icmp_only, or ICMP failed under snmp_fallback's single try)"
    text = f"SNMP: {_SNMP_WORDS.get(obs.state, obs.state.value)}"
    if obs.sys_uptime_ticks is not None:
        text += f"; agent uptime {uptime_text(obs.sys_uptime_ticks)} (management uptime)"
    if obs.error_code:
        text += f"; error {obs.error_code}"
    return f"{text}; {obs.request_count} requests, {obs.timeout_count} timeouts"


def _counter_lines(result: OnceResult) -> list[str]:
    """List each sampled interface's raw counters.

    Args:
        result: The cycle.

    Returns:
        list[str]: Lines; missing counters show as ``?``.

    Raises:
        None
    """
    rows = [o for o in result.progress.observations if isinstance(o, InterfaceObservation)]
    known = 0 if result.plan is None else len(result.plan.interfaces)
    lines = [f"Interfaces: {known} in the poll plan, {len(rows)} sampled (up/up and collected)"]
    if result.plan is None:
        lines.append("  not discovered yet: only sysUpTime.0 was read; the collector discovers it")
    for obs in rows[:MAX_ROWS]:
        values = [obs.in_octets, obs.out_octets, obs.in_errors, obs.out_errors]
        shown = ["?" if v is None else f"{v:,}" for v in values]
        family = "?" if obs.counter_family is None else obs.counter_family.value
        lines.append(
            f"  ifIndex {obs.if_index}: in {shown[0]} / out {shown[1]} octets, errors "
            f"{shown[2]} / {shown[3]} ({family})"
        )
    if len(rows) > MAX_ROWS:
        lines.append(f"  ... and {len(rows) - MAX_ROWS} more")
    if rows:
        lines.append("  rates need a previous sample; the running collector derives them")
    return lines


def _evidence(result: OnceResult) -> StatusEvidence:
    """Turn the cycle into E.2 evidence.

    Args:
        result: The cycle.

    Returns:
        StatusEvidence: What this one cycle knows.

    Raises:
        None
    """
    icmp, snmp = _first(result, IcmpObservation), _first(result, SnmpDeviceObservation)
    first = icmp if icmp is not None else snmp
    at = None if first is None else first.timestamp
    trouble = any(s.status is StepStatus.ERROR for s in result.progress.steps)
    reset = snmp is not None and snmp.uptime_continuity is UptimeContinuity.RESET_DETECTED
    return StatusEvidence(
        icmp=IcmpState.UNKNOWN if icmp is None else icmp.state,
        snmp=SnmpState.UNKNOWN if snmp is None else snmp.state,
        observed_at=at,
        admin=result.target.device.admin_state,
        strategy=result.target.strategy,
        collector=CollectorState.DEGRADED if trouble else CollectorState.HEALTHY,
        last_reset_at=at if reset else None,
        interval_s=float(result.target.effective_interval_s),
    )


def render_once(result: OnceResult, now: datetime) -> tuple[list[str], int]:
    """Render the cycle and choose the exit status.

    Args:
        result: The cycle.
        now: Reference time (UTC) for the verdict.

    Returns:
        tuple[list[str], int]: Report lines and the Nagios-style exit status.

    Raises:
        None
    """
    device, target = result.target.device, result.target
    evidence = _evidence(result)
    status = device_status(evidence, now)
    lines = [
        f"{device.name} ({device.poll_address}) polled once as collector {result.collector}: "
        f"{target.strategy.value}, every {target.effective_interval_s} s",
        _icmp_line(result),
        _snmp_line(result),
        *_counter_lines(result),
        f"Poll: {execution_of(result.progress.steps).value} in {result.elapsed_s:.2f} s",
        f"Verdict: {status.label} ({status.color.value}, Appendix E.2 row {status.row})",
    ]
    if status.row == 3:
        return lines, EXIT_UNKNOWN
    codes = {StatusColor.RED: 2, StatusColor.AMBER: 1}
    return lines, codes.get(status.color, 0)
