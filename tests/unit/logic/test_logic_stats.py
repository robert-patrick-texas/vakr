"""Tests for exact half-to-even statistics and the shared nearest-rank p95 (E.7, RET-004).

The rounding rule is part of the stored data contract: a Go collector and a
rollup job must produce the same integers, so ties and near-ties are pinned
here and the square root is checked against a 60-digit decimal reference.

Dependencies:
    pytest, hypothesis: Tables and generated samples.
    vakr.logic.stats: The functions under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_stats.py
"""

from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from math import ceil

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vakr.logic import stats

PROPERTY = settings(derandomize=True, database=None, max_examples=400, deadline=None)
RTTS = st.lists(st.integers(min_value=0, max_value=10_000_000), min_size=1, max_size=12)


def reference_pstdev(values: list[int]) -> int:
    """Compute the population standard deviation with 60-digit decimals.

    Args:
        values: Integer samples.

    Returns:
        int: The deviation rounded half to even.

    Raises:
        None
    """
    with localcontext() as ctx:
        ctx.prec = 60
        n = Decimal(len(values))
        mean = sum(Decimal(v) for v in values) / n
        variance = sum((Decimal(v) - mean) ** 2 for v in values) / n
        return int(variance.sqrt().quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@pytest.mark.parametrize(
    ("values", "mean", "median", "pstdev"),
    [
        pytest.param([1000], 1000, 1000, 0, id="E.7 single value"),
        pytest.param([1001, 1002], 1002, 1002, 0, id="E.7 mean 1001.5 rounds to even 1002"),
        pytest.param([1002, 1003], 1002, 1002, 0, id="E.7 mean 1002.5 rounds to even 1002"),
        pytest.param([1, 3], 2, 2, 1, id="E.7 deviation exactly 1"),
        pytest.param([0, 1], 0, 0, 0, id="E.7 deviation tie 0.5 rounds to even 0"),
        pytest.param([0, 3], 2, 2, 2, id="E.7 deviation tie 1.5 rounds to even 2"),
        pytest.param([4, 1, 3, 2], 2, 2, 1, id="E.7 unsorted input"),
    ],
)
def test_half_even_summaries(values: list[int], mean: int, median: int, pstdev: int) -> None:
    """Means, medians and deviations round half to even, exactly (E.7).

    Args:
        values: Samples.
        mean: Expected rounded mean.
        median: Expected rounded median.
        pstdev: Expected rounded population standard deviation.

    Returns:
        None

    Raises:
        None
    """
    assert stats.mean_half_even(values) == mean
    assert stats.median_half_even(values) == median
    assert stats.pstdev_half_even(values) == pstdev


@pytest.mark.parametrize(
    ("part", "whole", "expected"),
    [
        pytest.param(1, 5, 20.0, id="E.7 one of five"),
        pytest.param(1, 8, 12.5, id="E.7 exact one decimal"),
        pytest.param(1, 16, 6.2, id="E.7 6.25 rounds to even 6.2"),
        pytest.param(3, 16, 18.8, id="E.7 18.75 rounds to even 18.8"),
        pytest.param(3, 2000, 0.2, id="E.7 0.15 is a tie in exact arithmetic"),
        pytest.param(1, 3, 33.3, id="E.7 repeating decimal"),
    ],
)
def test_percent_one_decimal(part: int, whole: int, expected: float) -> None:
    """Loss percent is one decimal place, half to even, with no float drift (E.7).

    Args:
        part: Numerator.
        whole: Denominator.
        expected: Rounded percentage.

    Returns:
        None

    Raises:
        None
    """
    assert stats.percent_one_decimal(part, whole) == expected


@pytest.mark.parametrize(
    ("values", "percent", "expected"),
    [
        pytest.param([5.0], 95, 5.0, id="RET-004 single sample"),
        pytest.param([40, 10, 30, 20], 95, 40, id="RET-004 rank ceil(3.8)=4"),
        pytest.param(list(range(1, 21)), 95, 19, id="RET-004 rank 19 of 20"),
        pytest.param(list(range(1, 101)), 95, 95, id="RET-004 rank 95 of 100"),
        pytest.param(list(range(1, 16)), 95, 15, id="DASH-003 15 one-minute samples"),
        pytest.param([3, 1, 2], 50, 2, id="RET-004 median by nearest rank"),
        pytest.param([3, 1, 2], 100, 3, id="RET-004 p100 is the maximum"),
    ],
)
def test_nearest_rank(values: list[float], percent: float, expected: float) -> None:
    """The p95 is the nearest-rank sample, never interpolated (DASH-003, RET-004).

    Args:
        values: Samples.
        percent: Percentile.
        expected: The selected sample.

    Returns:
        None

    Raises:
        None
    """
    assert stats.nearest_rank(values, percent) == expected
    if percent == 95:
        assert stats.p95(values) == expected


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: stats.mean_half_even([]), id="empty mean"),
        pytest.param(lambda: stats.median_half_even([]), id="empty median"),
        pytest.param(lambda: stats.pstdev_half_even([]), id="empty deviation"),
        pytest.param(lambda: stats.p95([]), id="empty p95"),
        pytest.param(lambda: stats.nearest_rank([1], 0), id="percent zero"),
        pytest.param(lambda: stats.nearest_rank([1], 101), id="percent over 100"),
        pytest.param(lambda: stats.percent_one_decimal(1, 0), id="zero denominator"),
    ],
)
def test_missing_statistics_are_errors_not_zero(call: Callable[[], object]) -> None:
    """Empty inputs raise instead of returning 0 (invariant 1).

    Args:
        call: Zero-argument callable that must raise.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"need at least|percent|positive"):
        call()


@PROPERTY
@given(RTTS)
def test_pstdev_matches_decimal_reference(values: list[int]) -> None:
    """Integer-only rounding of sqrt(N)/n agrees with 60-digit decimals (E.7).

    Args:
        values: Generated samples up to 10 s in microseconds.

    Returns:
        None

    Raises:
        None
    """
    assert stats.pstdev_half_even(values) == reference_pstdev(values)


@PROPERTY
@given(RTTS, st.integers(min_value=1, max_value=100))
def test_nearest_rank_is_a_sample_with_enough_below(values: list[int], percent: int) -> None:
    """The result is a sample, and at least p% of samples are at or below it.

    Args:
        values: Generated samples.
        percent: Generated percentile.

    Returns:
        None

    Raises:
        None
    """
    result = stats.nearest_rank(values, percent)
    assert result in values
    assert sum(v <= result for v in values) >= ceil(percent * len(values) / 100)
    assert stats.mean_half_even(values) in range(min(values), max(values) + 1)
