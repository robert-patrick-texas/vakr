"""Tests for observation freshness (E.3, TIME-005, TIME-006).

An observation is stale when its age exceeds ``max(2.5 x interval,
interval + 30 s)``: 150 s at 60 s, 40 s at 10 s, 750 s at 300 s. One late or
missed poll is tolerated; two are not.

Dependencies:
    pytest: Tables.
    vakr.logic.freshness: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_freshness.py
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from vakr.domain.quality import Quality
from vakr.logic.freshness import assess_freshness, stale_threshold_s

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("interval", "age", "threshold", "stale"),
    [
        pytest.param(60, 150, 150.0, False, id="E.3 60 s interval: exactly 150 s is fresh"),
        pytest.param(60, 150.001, 150.0, True, id="E.3 60 s interval: over 150 s is stale"),
        pytest.param(60, 119, 150.0, False, id="E.3 one missed poll is tolerated"),
        pytest.param(10, 41, 40.0, True, id="E.3 10 s interval: interval + 30 s dominates"),
        pytest.param(300, 700, 750.0, False, id="E.3 300 s interval: 2.5 x dominates"),
        pytest.param(60, -5, 150.0, False, id="TIME-005 future timestamp is reported, not stale"),
    ],
)
def test_e3_threshold(interval: float, age: float, threshold: float, stale: bool) -> None:
    """E.3 threshold and the strict "greater than" comparison.

    Args:
        interval: Effective interval in seconds.
        age: Observation age in seconds.
        threshold: Expected threshold.
        stale: Expected verdict.

    Returns:
        None

    Raises:
        None
    """
    f = assess_freshness(NOW - timedelta(seconds=age), NOW, interval)
    assert (f.threshold_s, f.stale) == (threshold, stale)
    assert f.age_s == pytest.approx(age)
    assert f.quality == (Quality.STALE if stale else Quality.NONE)


def test_never_observed_is_stale() -> None:
    """TIME-006: with no observation there is nothing current to show.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = assess_freshness(None, NOW, 60)
    assert (f.age_s, f.stale, f.quality) == (None, True, Quality.STALE)


def test_configurable_multiplier_and_margin() -> None:
    """TIME-006: the multiplier and minimum margin are parameters.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert stale_threshold_s(60, multiplier=3.0) == 180.0
    assert stale_threshold_s(60, min_margin_s=120.0) == 180.0


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: stale_threshold_s(0), id="zero interval"),
        pytest.param(lambda: assess_freshness(NOW, NOW.replace(tzinfo=None), 60), id="naive now"),
        pytest.param(lambda: assess_freshness(NOW.replace(tzinfo=None), NOW, 60), id="naive time"),
    ],
)
def test_invalid_inputs(call: Callable[[], object]) -> None:
    """Naive datetimes and non-positive intervals are refused (TIME-001).

    Args:
        call: Zero-argument callable that must raise.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"positive|timezone-aware"):
        call()
