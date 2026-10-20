"""Operator-language rendering of one ICMP cycle for ``vakr icmp probe`` (18.3).

The output separates what the device did (replied, stayed silent, or a
router reported it unreachable) from what the collector could not do
(send or watch a probe), because "the device is down" and "vakr could not
look" need different people to act (OBS-002). Missing RTTs are shown as
missing, never as 0 ms.

Dependencies:
    vakr.collectors.icmp.cycle: The summarised cycle.
    vakr.ports.icmp: Outcomes and collector faults.

Safety:
    Prints addresses and timings only; nothing here is secret.

Example:
    >>> fmt_ms(1234), fmt_ms(None)
    ('1.234 ms', 'n/a')
"""

from vakr.collectors.icmp.cycle import IcmpCycle
from vakr.domain.enums import IcmpState
from vakr.logic.icmp import SnmpPlan
from vakr.ports.icmp import CollectorFault, ProbeOutcome, ProbeSample

EXIT_UNKNOWN = 3
_EXIT = {
    IcmpState.REACHABLE: 0,
    IcmpState.IMPAIRED: 1,
    IcmpState.UNREACHABLE: 2,
    IcmpState.UNKNOWN: EXIT_UNKNOWN,
}
FAULTS = {
    CollectorFault.PERMISSION_DENIED: "this host refuses unprivileged ICMP to this user's"
    " groups (net.ipv4.ping_group_range); run `vakr doctor` for the fix",
    CollectorFault.FAMILY_UNSUPPORTED: "this host has no ICMP for this address family"
    " (is IPv6 disabled?)",
    CollectorFault.SOCKET_ERROR: "the ICMP socket failed",
    CollectorFault.SOURCE_UNAVAILABLE: "the source address is not configured on this host",
    CollectorFault.SOCKET_LIMIT: "too many different source addresses are in use",
    CollectorFault.OVERLOADED: "the collector was too busy to send it on time",
    CollectorFault.LOCAL_NO_ROUTE: "this host has no route to the target",
    CollectorFault.LOCAL_POLICY: "a local firewall rule or broadcast restriction refused it",
    CollectorFault.SEND_ERROR: "sending failed on this host",
    CollectorFault.RECEIVE_OVERFLOW: "the collector's receive buffer overflowed, so replies"
    " may have been dropped on this host",
    CollectorFault.CANCELLED: "the cycle was cancelled",
}
"""What each collector fault means, for operators."""


def fmt_ms(rtt_us: int | None) -> str:
    """Render microseconds as milliseconds, or ``n/a`` when missing.

    Args:
        rtt_us: Integer microseconds, or ``None``.

    Returns:
        str: Such as ``"1.234 ms"``.

    Raises:
        None
    """
    return "n/a" if rtt_us is None else f"{rtt_us / 1000:.3f} ms"


def _sample_line(sample: ProbeSample, timeout_ms: float) -> str:
    """Describe one probe.

    Args:
        sample: The probe's outcome.
        timeout_ms: Per-probe timeout, for the lost-probe wording.

    Returns:
        str: One indented line.

    Raises:
        None
    """
    head = f"  probe {sample.index + 1}: "
    match sample.outcome:
        case ProbeOutcome.REPLY:
            return head + f"reply in {fmt_ms(sample.rtt_us)}"
        case ProbeOutcome.TIMEOUT:
            return head + f"no reply within {timeout_ms:g} ms (lost)"
        case ProbeOutcome.UNREACHABLE:
            return head + f"lost: {sample.detail}"
        case _:
            why = FAULTS.get(sample.fault, "collector problem") if sample.fault else "unknown"
            verb = "not sent" if sample.outcome is ProbeOutcome.NOT_SENT else "not observed"
            detail = f" ({sample.detail})" if sample.detail else ""
            return head + f"{verb}: {why}{detail}; not counted as loss"


def render(cycle: IcmpCycle, name: str | None = None) -> list[str]:
    """Render a cycle as the lines ``vakr icmp probe`` prints.

    Args:
        cycle: The summarised cycle.
        name: Host name the operator typed, if it was not an address.

    Returns:
        list[str]: Output lines.

    Raises:
        None
    """
    request, summary = cycle.result.request, cycle.summary
    family = "IPv6" if request.family.value == "ipv6" else "IPv4"
    shown = f"{name} ({request.target}, {family})" if name else f"{request.target} ({family})"
    source = request.source or "the default source"
    lines = [
        f"probing {shown} from {source}: {request.count} probes, "
        f"{request.spacing_s * 1000:g} ms apart, {request.timeout_s * 1000:g} ms timeout"
    ]
    lines += [_sample_line(s, request.timeout_s * 1000) for s in cycle.result.samples]
    why = ", ".join(i.value for i in summary.impairments)
    state = summary.state.value + (f" ({why})" if why else "")
    if summary.loss_percent is None:
        lines += ["", f"state: {state}: no probe could be sent"]
    else:
        counts = f"{summary.received} of {summary.sent} replies"
        lines += ["", f"state: {state}: {counts}, {summary.loss_percent:.1f}% loss"]
    stats = (summary.min_rtt_us, summary.avg_rtt_us, summary.median_rtt_us, summary.max_rtt_us)
    if summary.received:
        rtts = "/".join(f"{v / 1000:.3f}" for v in stats if v is not None)
        lines.append(f"  rtt min/avg/median/max: {rtts} ms, jitter {fmt_ms(summary.jitter_us)}")
    if cycle.result.fault is not None:
        lines.append(f"  collector problem: {FAULTS[cycle.result.fault]}.")
        lines.append("  This result is about this collector, not the device (OBS-002).")
    gate = "would run" if cycle.snmp is SnmpPlan.COLLECT else "would be skipped (under 2 replies)"
    lines.append(f"  SNMP under icmp_gated: {gate}")
    if quality := cycle.summary.quality:
        lines.append(f"  quality flags: {str(quality.name).replace('|', ', ')}")
    return lines


def exit_code(cycle: IcmpCycle) -> int:
    """Map the derived state to a Nagios-style exit status.

    Args:
        cycle: The summarised cycle.

    Returns:
        int: 0 reachable, 1 impaired, 2 unreachable, 3 unknown.

    Raises:
        None
    """
    return _EXIT[cycle.summary.state]
