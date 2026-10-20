"""Tests for ``sysUpTime`` continuity across gaps (UP-T01..T03, E.5).

Covers UPTIME-002 (continuity versus reset after a gap), UPTIME-004
(TimeTicks wrap and tolerance), and UPTIME-005 (a reset is a management
agent restart with a confidence, never a chassis reboot). Property tests
drive the counter across the 2^32 wrap from every starting point to show a
legitimate wrap is never reported as a restart.

Dependencies:
    pytest, hypothesis: Tables and generated tick values.
    vakr.logic.uptime: The function under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_uptime.py
"""

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from vakr.domain.enums import UptimeContinuity
from vakr.domain.quality import Quality
from vakr.logic.uptime import TIMETICKS_MODULUS, RestartConfidence, check_uptime

PROPERTY = settings(derandomize=True, database=None, max_examples=500, deadline=None)
WRAP = TIMETICKS_MODULUS
CONT, RESET, UNKNOWN = (
    UptimeContinuity.CONTINUOUS,
    UptimeContinuity.RESET_DETECTED,
    UptimeContinuity.UNKNOWN,
)
HIGH, LOW = RestartConfidence.HIGH, RestartConfidence.LOW
DAY = 8_640_000  # ticks


@pytest.mark.parametrize(
    ("prev", "curr", "elapsed", "continuity", "confidence", "quality", "wrapped"),
    [
        pytest.param(DAY, DAY + 6050, 60.0, CONT, None, Quality.NONE, False, id="UP-T01 one poll"),
        pytest.param(
            DAY,
            DAY + 360_000 + 3000,
            3600.0,
            CONT,
            None,
            Quality.NONE,
            False,
            id="UP-T01 one-hour gap, 30 s late, inside the 36 s (1%) tolerance",
        ),
        pytest.param(
            DAY,
            DAY + 7000,
            60.0,
            CONT,
            None,
            Quality.NONE,
            False,
            id="UP-T01 deviation exactly at the 10 s tolerance",
        ),
        pytest.param(
            DAY,
            DAY + 5000,
            60.0,
            CONT,
            None,
            Quality.NONE,
            False,
            id="UP-T01 10 s behind is still continuous",
        ),
        pytest.param(
            DAY,
            12_000,
            300.0,
            RESET,
            HIGH,
            Quality.NONE,
            False,
            id="UP-T02 agent up 120 s after a 300 s gap: restart during the gap",
        ),
        pytest.param(
            DAY,
            DAY // 2,
            60.0,
            RESET,
            LOW,
            Quality.NONE,
            False,
            id="UP-T02 lower but too long for a restart in the gap: inconsistent",
        ),
        pytest.param(
            DAY,
            DAY + 4999,
            60.0,
            RESET,
            LOW,
            Quality.NONE,
            False,
            id="UP-T02 one tick beyond tolerance behind",
        ),
        pytest.param(
            DAY,
            DAY + 7001,
            60.0,
            UNKNOWN,
            None,
            Quality.UPTIME_AHEAD,
            False,
            id="E.5 one tick beyond tolerance ahead: clock problem",
        ),
        pytest.param(
            WRAP - 3000,
            3000,
            60.0,
            CONT,
            None,
            Quality.NONE,
            True,
            id="UP-T03 wrap: expected and observed both past 2^32",
        ),
        pytest.param(
            WRAP - 5,
            10,
            0.0,
            CONT,
            None,
            Quality.NONE,
            True,
            id="UP-T03 observed just past the wrap, expected just before it",
        ),
        pytest.param(
            WRAP - 5500,
            WRAP - 400,
            60.0,
            CONT,
            None,
            Quality.NONE,
            False,
            id="UP-T03 observed just before the wrap, expected just after it",
        ),
        pytest.param(
            WRAP - 3000,
            5500,
            60.0,
            RESET,
            HIGH,
            Quality.NONE,
            True,
            id="UP-T02 restart just after the prior poll, above the wrapped expectation",
        ),
        pytest.param(
            WRAP - 100,
            5000,
            60.0,
            CONT,
            None,
            Quality.NONE,
            True,
            id="UP-T03 wrap with the agent 9 s behind",
        ),
        pytest.param(
            WRAP - 100,
            2000,
            60.0,
            RESET,
            HIGH,
            Quality.NONE,
            True,
            id="UP-T02 restart near the wrap is still a restart",
        ),
        pytest.param(
            WRAP - 100,
            WRAP - 50,
            60.0,
            RESET,
            LOW,
            Quality.NONE,
            True,
            id="UP-T03 expected wrapped but the counter barely moved: behind, not ahead",
        ),
        pytest.param(
            WRAP - 100,
            60_000,
            60.0,
            UNKNOWN,
            None,
            Quality.UPTIME_AHEAD,
            True,
            id="E.5 ahead after the wrap",
        ),
        pytest.param(
            100,
            100,
            -5.0,
            UNKNOWN,
            None,
            Quality.CLOCK_UNSYNCED,
            False,
            id="E.5 wall clock went backwards",
        ),
    ],
)
def test_continuity_table(
    prev: int,
    curr: int,
    elapsed: float,
    continuity: UptimeContinuity,
    confidence: RestartConfidence | None,
    quality: Quality,
    wrapped: bool,
) -> None:
    """E.5 classification with tolerance ``max(10 s, 1% of elapsed)`` and wrap handling.

    Args:
        prev: Prior ``sysUpTime.0``.
        curr: Current ``sysUpTime.0``.
        elapsed: Wall-clock seconds between them.
        continuity: Expected verdict.
        confidence: Expected restart confidence.
        quality: Expected flags.
        wrapped: Expected wrap indicator.

    Returns:
        None

    Raises:
        None
    """
    r = check_uptime(prev, curr, elapsed)
    assert (r.continuity, r.confidence, r.quality, r.wrapped) == (
        continuity,
        confidence,
        quality,
        wrapped,
    )


def test_reset_evidence_names_the_agent_not_the_chassis() -> None:
    """UP-T02: a reset records evidence and never claims a chassis reboot (UPTIME-005).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    r = check_uptime(DAY, 12_000, 300.0)
    assert "management agent restarted" in r.evidence
    assert "does not prove a chassis reboot" in r.evidence
    assert "120.00 s" in r.evidence
    assert (r.expected_ticks, r.deviation_ticks, r.tolerance_ticks) == (
        DAY + 30_000,
        -8_658_000,
        1000,
    )
    low = check_uptime(DAY, DAY // 2, 60.0)
    assert "inconsistent" in low.evidence
    assert "chassis reboot" in low.evidence


def test_evidence_for_other_verdicts() -> None:
    """Continuous, ahead, and clock-backwards results explain themselves.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert "across the TimeTicks wrap" in check_uptime(WRAP - 3000, 3000, 60.0).evidence
    assert "as expected, within 10.00 s" in check_uptime(DAY, DAY + 6000, 60.0).evidence
    assert "ahead of expected" in check_uptime(DAY, DAY + 99_000, 60.0).evidence
    assert "went back 5.00 s" in check_uptime(100, 100, -5.0).evidence


@pytest.mark.parametrize(
    ("prev", "curr", "elapsed"),
    [
        pytest.param(-1, 0, 1.0, id="negative ticks"),
        pytest.param(0, WRAP, 1.0, id="ticks at 2^32"),
        pytest.param(0, 0, float("inf"), id="infinite elapsed"),
        pytest.param(0, 0, float("nan"), id="NaN elapsed"),
    ],
)
def test_impossible_inputs_raise(prev: int, curr: int, elapsed: float) -> None:
    """Values no agent or clock can produce are rejected.

    Args:
        prev: Prior ticks.
        curr: Current ticks.
        elapsed: Elapsed seconds.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"TimeTicks|finite"):
        check_uptime(prev, curr, elapsed)


TICKS = st.integers(min_value=0, max_value=WRAP - 1)
ELAPSED = st.integers(min_value=0, max_value=10_000_000)  # up to ~116 days, whole seconds


@PROPERTY
@given(TICKS, ELAPSED, st.integers(min_value=-1000, max_value=1000))
def test_continuous_agent_is_never_a_restart(prev: int, elapsed: int, jitter: int) -> None:
    """UP-T03: an agent that kept running is CONTINUOUS from any start, across any wrap.

    Args:
        prev: Generated prior ticks.
        elapsed: Generated gap in seconds.
        jitter: Scheduling and response delay in ticks, within 10 s.

    Returns:
        None

    Raises:
        None
    """
    curr = (prev + 100 * elapsed + jitter) % WRAP
    r = check_uptime(prev, curr, float(elapsed))
    assert r.continuity is CONT
    assert r.deviation_ticks == jitter


@PROPERTY
@given(TICKS, st.integers(min_value=11, max_value=10_000_000), st.data())
def test_restart_during_gap_is_detected_with_high_confidence(
    prev: int, elapsed: int, data: st.DataObject
) -> None:
    """UP-T02: an agent restarted during the gap reports a high-confidence reset.

    Args:
        prev: Generated prior ticks.
        elapsed: Generated gap in seconds.
        data: Draws the restart time within the gap.

    Returns:
        None

    Raises:
        None
    """
    curr = data.draw(st.integers(min_value=0, max_value=100 * elapsed))
    expected = (prev + 100 * elapsed) % WRAP
    circular = (curr - expected + WRAP // 2) % WRAP - WRAP // 2
    tolerance = max(1000, elapsed)
    assume(abs(circular) > tolerance)  # a restart that lands on the expected value is invisible
    r = check_uptime(prev, curr, float(elapsed))
    assert (r.continuity, r.confidence) == (RESET, HIGH)
    assert "does not prove a chassis reboot" in r.evidence


@PROPERTY
@given(st.integers(min_value=0, max_value=WRAP // 4), ELAPSED, st.data())
def test_clock_ahead_is_unknown_not_restart(prev: int, elapsed: int, data: st.DataObject) -> None:
    """E.5: uptime beyond what the wall clock allows is UNKNOWN with UPTIME_AHEAD.

    Args:
        prev: Generated prior ticks, low enough that the expected value cannot wrap.
        elapsed: Generated gap in seconds.
        data: Draws how far ahead the agent is.

    Returns:
        None

    Raises:
        None
    """
    tolerance = max(1000, elapsed)
    expected = prev + 100 * elapsed
    floor = max(expected + tolerance, 100 * elapsed + tolerance) + 1
    # Going round the circle, values just below 2^32 are within tolerance of a small expected.
    ceiling = min(WRAP - 1, expected + WRAP - tolerance - 1)
    assume(floor <= ceiling)
    curr = data.draw(st.integers(min_value=floor, max_value=ceiling))
    r = check_uptime(prev, curr, float(elapsed))
    assert (r.continuity, r.quality, r.confidence) == (UNKNOWN, Quality.UPTIME_AHEAD, None)
