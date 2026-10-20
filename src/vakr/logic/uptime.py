"""Classify ``sysUpTime`` continuity across the gap between two successful polls (E.5).

``sysUpTime.0`` is TimeTicks: hundredths of a second since the device's SNMP
management agent last initialised, wrapping at 2^32 (about 497 days). After
a gap, the prior value plus the elapsed wall time predicts the new value. A
match within tolerance means the agent ran continuously; a lower value means
it restarted (UPTIME-002); a higher one points to a clock problem.

The evidence only concerns the management agent (UPTIME-003). A restarted
agent may or may not mean the chassis rebooted, so a reset is reported as a
management-agent restart with a confidence, never as a chassis reboot
(UPTIME-005).

Wrap handling (UPTIME-004): continuity is judged around the 2^32 circle, so
a value just past the wrap is not mistaken for a restart. A new value no
longer than the gap (plus tolerance) fits a restart during the gap and is
reported as one even when the expected value wrapped and sits below it.
Otherwise lower or higher is judged as in E.5, except that a counter which
has not yet wrapped when the expected value has is behind, not hours ahead.

Dependencies:
    dataclasses, enum, fractions, math: Immutable results and exact tick math.
    vakr.domain.enums, vakr.domain.quality: ``UptimeContinuity`` and flags.

Safety:
    Pure. Decisions use exact rationals; the integer fields are for display.

Example:
    >>> check_uptime(prev_ticks=360_000, curr_ticks=366_050, elapsed_s=60.0).continuity.name
    'CONTINUOUS'
    >>> r = check_uptime(prev_ticks=360_000, curr_ticks=3_000, elapsed_s=60.0)
    >>> r.continuity.name, r.confidence.name
    ('RESET_DETECTED', 'HIGH')
"""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from math import isfinite

from vakr.domain.enums import UptimeContinuity
from vakr.domain.quality import Quality

TIMETICKS_MODULUS = 2**32
TICKS_PER_SECOND = 100
MIN_TOLERANCE_S = 10  # E.5: tolerance is max(10 s, 1% of elapsed)
TOLERANCE_FRACTION = Fraction(1, 100)
_HALF = TIMETICKS_MODULUS // 2


class RestartConfidence(StrEnum):
    """How well a lower ``sysUpTime`` fits a management-agent restart (UPTIME-005)."""

    HIGH = "high"  # the new uptime is no longer than the gap: a restart during it
    LOW = "low"  # uptime went backwards but is too long for a restart in the gap


@dataclass(frozen=True, slots=True)
class UptimeCheck:
    """Continuity verdict with the evidence behind it.

    Attributes:
        continuity: E.5 result.
        quality: ``UPTIME_AHEAD`` or ``CLOCK_UNSYNCED`` when they apply.
        expected_ticks: ``(prev + 100 x elapsed) mod 2^32``, rounded.
        deviation_ticks: Signed ``curr - expected``, rounded; negative is behind.
        tolerance_ticks: The tolerance applied, rounded.
        wrapped: True when the counter crossed 2^32 (for CONTINUOUS) or the
            expected value did (otherwise).
        confidence: Set only for ``RESET_DETECTED``.
        evidence: One operator-readable sentence.
    """

    continuity: UptimeContinuity
    quality: Quality
    expected_ticks: int
    deviation_ticks: int
    tolerance_ticks: int
    wrapped: bool
    confidence: RestartConfidence | None
    evidence: str


def _seconds(ticks: Fraction | int) -> str:
    """Format a tick count as seconds for evidence text.

    Args:
        ticks: TimeTicks (hundredths of a second).

    Returns:
        str: Seconds with two decimals, such as ``"60.00 s"``.

    Raises:
        None
    """
    return f"{float(ticks) / TICKS_PER_SECOND:,.2f} s"


def _validate(prev_ticks: int, curr_ticks: int, elapsed_s: float) -> None:
    """Reject values that cannot come from TimeTicks or a clock.

    Args:
        prev_ticks: Prior ``sysUpTime.0``.
        curr_ticks: Current ``sysUpTime.0``.
        elapsed_s: Wall-clock seconds between the two observations.

    Returns:
        None

    Raises:
        ValueError: If a tick value is outside [0, 2^32) or ``elapsed_s`` is
            not finite.
    """
    for ticks in (prev_ticks, curr_ticks):
        if not 0 <= ticks < TIMETICKS_MODULUS:
            raise ValueError(f"TimeTicks must be in [0, 2^32), got {ticks}")
    if not isfinite(elapsed_s):
        raise ValueError("elapsed_s must be finite")


def _verdict(
    curr: int, deviation: Fraction, advance: Fraction, tolerance: Fraction, elapsed_s: float
) -> tuple[UptimeContinuity, Quality, RestartConfidence | None, str]:
    """Classify a value that missed the continuity window.

    Args:
        curr: Current ``sysUpTime.0``.
        deviation: Signed distance from the expected value, behind if negative.
        advance: Ticks the wall clock advanced.
        tolerance: Allowed deviation in ticks.
        elapsed_s: Wall-clock seconds between the observations.

    Returns:
        tuple: Continuity, quality, confidence, and evidence text.

    Raises:
        None
    """
    gap = f"the {elapsed_s:,.0f} s gap"
    # Fit is checked first: near the wrap, a fresh agent can sit above the wrapped expectation.
    if curr <= advance + tolerance:
        text = f"sysUpTime is {_seconds(curr)}: the management agent restarted during {gap}"
        confidence = RestartConfidence.HIGH
    elif deviation < 0:
        text = f"sysUpTime is {_seconds(-deviation)} below expected but too long for a restart"
        text += f" during {gap}: inconsistent"
        confidence = RestartConfidence.LOW
    else:
        text = f"sysUpTime is {_seconds(deviation)} ahead of expected: check the clocks"
        return UptimeContinuity.UNKNOWN, Quality.UPTIME_AHEAD, None, text
    # UPTIME-005: sysUpTime only proves the agent restarted, not the chassis.
    text += "; this does not prove a chassis reboot"
    return UptimeContinuity.RESET_DETECTED, Quality.NONE, confidence, text


def check_uptime(prev_ticks: int, curr_ticks: int, elapsed_s: float) -> UptimeCheck:
    """Compare two ``sysUpTime.0`` values across a gap (E.5, UPTIME-002, UPTIME-004).

    Args:
        prev_ticks: ``sysUpTime.0`` from the earlier successful observation.
        curr_ticks: ``sysUpTime.0`` from the later one.
        elapsed_s: Wall-clock seconds between the two observations.

    Returns:
        UptimeCheck: ``CONTINUOUS``, ``RESET_DETECTED`` with confidence, or
        ``UNKNOWN`` with ``UPTIME_AHEAD``. A negative ``elapsed_s`` (the wall
        clock went backwards) gives ``UNKNOWN`` with ``CLOCK_UNSYNCED``.

    Raises:
        ValueError: If a tick value is outside [0, 2^32) or ``elapsed_s`` is
            not finite.
    """
    _validate(prev_ticks, curr_ticks, elapsed_s)
    advance = TICKS_PER_SECOND * Fraction(elapsed_s)
    tolerance = Fraction(max(TICKS_PER_SECOND * MIN_TOLERANCE_S, TOLERANCE_FRACTION * advance))
    raw = prev_ticks + advance
    expected, wrapped = raw % TIMETICKS_MODULUS, raw >= TIMETICKS_MODULUS
    circular = (curr_ticks - expected + _HALF) % TIMETICKS_MODULUS - _HALF
    confidence = None
    if elapsed_s < 0:
        continuity, quality, deviation = UptimeContinuity.UNKNOWN, Quality.CLOCK_UNSYNCED, circular
        text = f"the wall clock went back {-elapsed_s:,.2f} s between polls; cannot judge uptime"
    elif abs(circular) <= tolerance:
        continuity, quality, deviation = UptimeContinuity.CONTINUOUS, Quality.NONE, circular
        # A value just past 2^32 can match an expected value just below it.
        wrapped = raw + circular >= TIMETICKS_MODULUS
        across = " across the TimeTicks wrap" if wrapped else ""
        text = f"sysUpTime advanced as expected{across}, within {_seconds(tolerance)}"
    else:
        deviation = curr_ticks - expected
        if wrapped and deviation > _HALF:
            # Expected has wrapped but the counter has not: it is behind, not ahead.
            deviation -= TIMETICKS_MODULUS
        continuity, quality, confidence, text = _verdict(
            curr_ticks, deviation, advance, tolerance, elapsed_s
        )
    return UptimeCheck(
        continuity=continuity,
        quality=quality,
        expected_ticks=round(expected),
        deviation_ticks=round(deviation),
        tolerance_ticks=round(tolerance),
        wrapped=wrapped,
        confidence=confidence,
        evidence=text,
    )
