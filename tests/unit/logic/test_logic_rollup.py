"""Rollup arithmetic: exact peaks and p95, loss from counts, schedule slots (ROLL-T01, ROLL-T02).

Dependencies:
    hypothesis, pytest; vakr.logic.rollup, vakr.logic.stats.

Example:
    $ uv run pytest tests/unit/logic/test_logic_rollup.py
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vakr.logic.rollup import (
    Summary,
    loss_summary,
    reply_summary,
    schedule_slots,
    summarize,
    whole_summary,
)
from vakr.logic.stats import p95


def test_roll_t01_rates_keep_exact_max_and_nearest_rank_p95() -> None:
    """ROLL-T01, RET-004, E.17 #13: 30 samples give the 29th value as p95 and the true max.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    values = [float(v) for v in (*range(1, 29), 1_000.0, 50.0)]  # one spike, unordered tail
    found = summarize(values)
    assert found == Summary(30, 1.0, sum(values) / 30, 50.0, 1_000.0)
    assert found is not None
    assert found.p95 == p95(values)


def test_missing_data_is_never_a_zero_summary() -> None:
    """Invariant 1: nothing valid gives ``None``, never zeros.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert summarize([]) is None
    assert whole_summary([]) is None
    assert reply_summary([], 0) is None
    assert reply_summary([900], 0) is None
    assert loss_summary([]) is None
    zero = summarize([0.0, 0.0])
    assert zero == Summary(2, 0.0, 0.0, 0.0, 0.0)


def test_roll_t02_loss_comes_from_summed_counts_not_averaged_percentages() -> None:
    """ROLL-T02, RET-008: 5/5 lost and 0/2 lost is 71.4% (5 of 7), not the 50% average.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    found = loss_summary([(5, 0), (2, 2)])
    assert found == Summary(2, 0.0, 71.4, 100.0, 100.0)
    third = loss_summary([(3, 2), (3, 2), (3, 3)])
    assert third is not None
    assert (third.minimum, third.mean, third.maximum) == (0.0, 22.2, 33.3)  # E.7: one decimal


def test_loss_refuses_impossible_counts() -> None:
    """RET-008: a cycle that sent nothing or got more replies than probes is not loss evidence.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="send probes"):
        loss_summary([(0, 0)])
    with pytest.raises(ValueError, match="send probes"):
        loss_summary([(2, 3)])


def test_replies_summarise_every_probe_with_a_half_even_median() -> None:
    """RET-006, E.7: min, median, p95, and max come from individual replies.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert reply_summary([900, 1100, 1000, 1001], 2) == Summary(2, 900.0, 1000.0, 1100.0, 1100.0)
    assert whole_summary([100, 101]) == Summary(2, 100.0, 100.0, 101.0, 101.0)


@pytest.mark.parametrize(
    ("window", "anchor", "period", "slots"),
    [
        ((0, 900), 0, 60, 15),
        ((0, 900), 30, 60, 15),
        ((0, 900), 7, 30, 30),
        ((0, 900), 0, 120, 8),
        ((0, 900), 100, 120, 7),
        ((0, 900), 950, 120, 7),
        ((0, 900), 1800, 3600, 0),
        ((900, 1800), 1800, 3600, 0),
        ((1800, 2700), 1800, 3600, 1),
        ((0, 900), -3540, 3600, 1),
    ],
)
def test_schedule_slots_count_intended_polls(
    window: tuple[int, int], anchor: int, period: int, slots: int
) -> None:
    """E.10: a bucket expects every slot of the device's schedule that falls inside it.

    Args:
        window: Bucket in seconds.
        anchor: One scheduled slot, seconds.
        period: Interval, seconds.
        slots: Expected count.

    Returns:
        None

    Raises:
        None
    """
    us = 1_000_000
    assert schedule_slots(window[0] * us, window[1] * us, anchor * us, period * us) == slots
    with pytest.raises(ValueError, match="positive"):
        schedule_slots(0, 1, 0, 0)


@given(st.lists(st.floats(min_value=0, max_value=1e13, allow_nan=False), min_size=1, max_size=60))
def test_rate_summaries_always_satisfy_the_bucket_rules(values: list[float]) -> None:
    """RET-004: minimum <= mean, p95 <= maximum for any samples, so every bucket validates.

    Args:
        values: Generated rates.

    Returns:
        None

    Raises:
        None
    """
    found = summarize(values)
    assert found is not None
    assert found.minimum <= found.mean <= found.maximum
    assert found.minimum <= found.p95 <= found.maximum
    assert found.valid == len(values)


@given(
    st.lists(
        st.integers(min_value=1, max_value=5).flatmap(
            lambda sent: st.tuples(st.just(sent), st.integers(min_value=0, max_value=sent))
        ),
        min_size=1,
        max_size=40,
    )
)
def test_loss_summaries_always_satisfy_the_bucket_rules(counts: list[tuple[int, int]]) -> None:
    """RET-008: summed-count loss lies between the per-cycle extremes after E.7 rounding.

    Args:
        counts: Generated ``(sent, received)`` cycles.

    Returns:
        None

    Raises:
        None
    """
    found = loss_summary(counts)
    assert found is not None
    assert found.minimum <= found.mean <= found.maximum
    assert found.minimum <= found.p95 <= found.maximum
