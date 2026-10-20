"""Operator words for states, quality flags, and missing values (12.8, 18.3).

Colour is never the only cue (UI-011): every status gets an icon whose
*shape* differs by meaning, so the list still reads in grayscale or for a
colour-blind operator, and a text label beside it. Quality flags and
baseline flags become short explanations, so a blank rate says *why* it is
blank ("first sample", "agent restarted") instead of looking like zero.

Dependencies:
    vakr.domain.enums, vakr.domain.quality: The vocabularies being named.
    vakr.logic.status: The status being shown.

Safety:
    Pure lookups.

Example:
    >>> from vakr.logic.status import DeviceStatus
    >>> from vakr.domain.enums import StatusBucket, StatusColor
    >>> v = status_view(DeviceStatus(StatusBucket.ATTENTION, StatusColor.RED, "Down", 4, 0))
    >>> v.icon, v.label
    ('✖', 'Down')
    >>> rate_gap_reason(Quality.BASELINE_UPTIME_RESET)
    'agent restarted; counters reset'
    >>> mark_stale(Reading("reachable", "reachable"), True).text
    'reachable (stale)'
"""

from dataclasses import replace

from vakr.domain.enums import IcmpState, IfAdminStatus, IfOperStatus, SnmpState
from vakr.domain.quality import Quality
from vakr.logic.status import DeviceStatus
from vakr.query.views import HealthView, Reading, StatusView

_ICONS_BY_ROW = {1: "○", 2: "◐", 3: "?", 4: "✖", 10: "✔"}
_DEGRADED_ICON = "▲"  # rows 5-9 and the split composite: amber, but evidence exists

ICMP_WORDS = {
    IcmpState.REACHABLE: "reachable",
    IcmpState.IMPAIRED: "impaired",
    IcmpState.UNREACHABLE: "unreachable",
    IcmpState.UNKNOWN: "unknown",
}
SNMP_WORDS = {
    SnmpState.RESPONDING: "responding",
    SnmpState.TIMEOUT: "timeout",
    SnmpState.ERROR: "error",
    SnmpState.SKIPPED: "skipped",
    SnmpState.PARTIAL: "partial",
    SnmpState.UNKNOWN: "unknown",
}

# Checked in this order: the first flag present explains a missing rate.
_RATE_GAP_REASONS: tuple[tuple[Quality, str], ...] = (
    (Quality.BASELINE_UPTIME_RESET, "agent restarted; counters reset"),
    (Quality.BASELINE_DISCONTINUITY, "interface counters were reset"),
    (Quality.BASELINE_WIDTH_CHANGE, "counters changed between 32-bit and 64-bit"),
    (Quality.BASELINE_DECREASE, "counter went backwards"),
    (Quality.BASELINE_GAP, "too long since the previous sample"),
    (Quality.BASELINE_IDENTITY_UNRESOLVED, "interface identity awaits review"),
    (Quality.BASELINE_ELAPSED_INVALID, "sample time did not advance"),
    (Quality.BASELINE_FIRST_SAMPLE, "first sample; a rate needs two"),
    (Quality.BASELINE_MISSING_PRIOR, "no previous sample"),
    (Quality.MISSING_FIELDS, "counter not reported"),
)

QUALITY_WORDS: dict[Quality, str] = {
    Quality.PARTIAL_LOSS: "1 of 5 probes lost (routine under Control Plane Policing)",
    Quality.SINGLE_REPLY: "only one probe answered; SNMP skipped",
    Quality.NOT_ATTEMPTED: "the collector could not attempt the poll",
    Quality.COLLECTOR_DEGRADED: "collector was degraded; results are suspect",
    Quality.STALE: "data is older than the freshness threshold",
    Quality.PARTIAL_RESPONSE: "SNMP reply was partial",
    Quality.MISSING_FIELDS: "some SNMP fields were missing",
    Quality.BASELINE_FIRST_SAMPLE: "first counter sample; no rate yet",
    Quality.BASELINE_MISSING_PRIOR: "previous counter sample missing",
    Quality.BASELINE_GAP: "counter gap too long for a rate",
    Quality.BASELINE_UPTIME_RESET: "agent restart reset the counters",
    Quality.BASELINE_DISCONTINUITY: "interface counter discontinuity",
    Quality.BASELINE_WIDTH_CHANGE: "counter width changed",
    Quality.BASELINE_DECREASE: "counter decreased unexpectedly",
    Quality.BASELINE_IDENTITY_UNRESOLVED: "interface identity unresolved",
    Quality.COUNTER_WRAP_32: "a 32-bit counter wrap was accepted",
    Quality.RATE_EXCEEDS_CAPACITY: "rate above 110% of capacity (kept, not clipped)",
    Quality.CAPACITY_UNKNOWN: "capacity unknown; utilization unavailable",
    Quality.UPTIME_AHEAD: "sysUpTime ran ahead of the clock",
    Quality.CLOCK_UNSYNCED: "collector clock not synchronised",
    Quality.MAINTENANCE: "collected during maintenance",
    Quality.REQUEST_SIZE_REDUCED: "SNMP requests were made smaller after tooBig",
    Quality.RETRIED: "SNMP needed retries",
    Quality.BASELINE_ELAPSED_INVALID: "sample time did not advance",
}
"""One operator phrase per quality flag (blueprint 4.3, 18.3)."""


def status_view(status: DeviceStatus) -> StatusView:
    """Pair a status with its icon.

    Args:
        status: Result of ``vakr.logic.status``.

    Returns:
        StatusView: Bucket, colour, icon, label, row, and severity.

    Raises:
        None
    """
    icon = _ICONS_BY_ROW.get(status.row or 0, _DEGRADED_ICON)
    return StatusView(status.bucket, status.color, icon, status.label, status.row, status.severity)


def rate_gap_reason(quality: Quality) -> str:
    """Explain why a derived rate is unavailable (RATE-004, 18.3).

    Args:
        quality: Flags of the interface sample.

    Returns:
        str: The most specific reason. Without a baseline flag the counter
        itself was missing from the reply, which blanks only its own metric
        (E.17 #11), so the reason is ``"counter not reported"``.

    Raises:
        None
    """
    for flag, reason in _RATE_GAP_REASONS:
        if flag in quality:
            return reason
    return "counter not reported"


def quality_words(quality: Quality) -> tuple[str, ...]:
    """List every flag in operator words, in bit order.

    Args:
        quality: Combined flags.

    Returns:
        tuple[str, ...]: One phrase per flag set.

    Raises:
        None
    """
    return tuple(text for flag, text in QUALITY_WORDS.items() if flag in quality)


def if_state_text(admin: IfAdminStatus | None, oper: IfOperStatus | None) -> str:
    """Format ``ifAdminStatus/ifOperStatus`` as ``up/down``; unknown parts are ``?``.

    Args:
        admin: Admin status, if read.
        oper: Oper status, if read.

    Returns:
        str: Such as ``"up/up"``, ``"down/down"``, or ``"up/?"``.

    Raises:
        None
    """
    a = admin.name.lower() if admin is not None else "?"
    o = oper.name.lower().replace("_", "-") if oper is not None else "?"
    return f"{a}/{o}"


def mark_stale(reading: Reading, stale: bool) -> Reading:
    """Mark a reading as coming from stale evidence (TIME-006).

    Args:
        reading: The reading.
        stale: Whether its evidence is stale (E.3).

    Returns:
        Reading: Unchanged when fresh or missing; otherwise ``"... (stale)"``.

    Raises:
        None
    """
    if not stale or reading.value is None:
        return reading
    return replace(reading, text=f"{reading.text} (stale)", stale=True)


def health_line(health: HealthView) -> str:
    """Summarise storage and collector health in one line.

    Args:
        health: The health view.

    Returns:
        str: Such as ``"metadata ok · telemetry ok · east healthy (10 s ago)"``.

    Raises:
        None
    """
    parts = [f"metadata {health.metadata.text}", f"telemetry {health.telemetry.text}"]
    for c in health.collectors:
        if c.state.value is None:
            parts.append(f"{c.position}: {c.state.reason}")
        else:
            spool = f", spool {c.spool.text}" if c.spool.value is not None else ""
            parts.append(f"{c.position} {c.state.text} ({c.age.text} ago{spool})")
    return " · ".join(parts)
