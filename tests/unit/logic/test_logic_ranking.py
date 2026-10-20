"""Tests for top-utilization and top-error rankings (DASH-T01..T03).

DASH-T01 drives the ranking from real ``compute_rates`` output so the p95
and the RATE-008 denominator are checked end to end. DASH-T02 shows that an
interface whose cumulative error counter is enormous but whose baseline is
invalid never ranks. DASH-T03 checks exclusions and coverage labels.

Dependencies:
    pytest: Tables.
    vakr.logic.ranking, vakr.logic.ranking_errors, vakr.logic.rates: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_ranking.py
"""

from collections.abc import Sequence

import pytest

from vakr.domain.enums import CounterFamily, UptimeContinuity
from vakr.logic.baseline import RateContext
from vakr.logic.capacity import CapacityInputs, CapacitySource
from vakr.logic.counters import CounterSample
from vakr.logic.ranking import (
    DEFAULT_WINDOW_S,
    NO_BASELINE,
    NO_CAPACITY,
    Direction,
    UtilizationSeries,
    expected_samples,
    rank_utilization,
)
from vakr.logic.ranking_errors import ErrorSample, ErrorSeries, rank_errors
from vakr.logic.rates import RateResult, compute_rates

HC = CounterFamily.HC64
INTERVAL = 30.0
FAST_E = CapacityInputs(if_speed_bps=100_000_000)


def window(
    in_mbps: list[float], out_mbps: list[float], capacity: CapacityInputs
) -> list[RateResult]:
    """Run ``compute_rates`` over a sequence of per-interval rates.

    Args:
        in_mbps: Inbound Mb/s per interval.
        out_mbps: Outbound Mb/s per interval.
        capacity: Interface capacity.

    Returns:
        list[RateResult]: One result per interval.

    Raises:
        None
    """
    in_octets = out_octets = 10**9
    prev = CounterSample(HC, in_octets, out_octets)
    results = []
    for i, o in zip(in_mbps, out_mbps, strict=True):
        in_octets += round(i * 10**6 * INTERVAL / 8)
        out_octets += round(o * 10**6 * INTERVAL / 8)
        curr = CounterSample(HC, in_octets, out_octets)
        ctx = RateContext(INTERVAL, INTERVAL, UptimeContinuity.CONTINUOUS, capacity)
        results.append(compute_rates(prev, curr, ctx))
        prev = curr
    return results


def test_dash_t01_p95_directional_utilization_and_denominator() -> None:
    """DASH-T01: 15-minute nearest-rank p95 of the higher direction, with its denominator.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    n = expected_samples(DEFAULT_WINDOW_S, INTERVAL)
    assert n == 30
    ramp = [float(k) for k in range(1, 31)]  # 1..30 Mb/s on 100 Mb/s: 1..30 %
    spiky = [10.0] * 29 + [99.0]  # one burst: p95 ignores it, max would not
    busy = UtilizationSeries.from_rates("ge-0/0/2", window(ramp, [5.0] * 30, FAST_E), n)
    burst = UtilizationSeries.from_rates("ge-0/0/10", window([1.0] * 30, spiky, FAST_E), n)
    top = rank_utilization([burst, busy]).ranked
    assert [e.key for e in top] == ["ge-0/0/2", "ge-0/0/10"]
    assert (top[0].p95_in_pct, top[0].p95_out_pct) == pytest.approx((29.0, 5.0))
    assert (top[0].score_pct, top[0].direction) == (pytest.approx(29.0), Direction.IN)
    assert (top[1].score_pct, top[1].direction) == (pytest.approx(10.0), Direction.OUT)
    assert top[0].denominator is CapacitySource.IF_SPEED
    assert top[0].coverage.complete
    contracted = CapacityInputs(contracted_bps=50_000_000, contracted_is_denominator=True)
    sub_rate = UtilizationSeries.from_rates("wan0", window(ramp, ramp, contracted), n)
    (entry,) = rank_utilization([sub_rate]).ranked
    assert (entry.score_pct, entry.denominator) == (pytest.approx(58.0), CapacitySource.CONTRACTED)


@pytest.mark.parametrize(
    ("in_pct", "out_pct", "score", "direction"),
    [
        pytest.param((50.0,), (50.0,), 50.0, Direction.IN, id="DASH-003 tie reports inbound"),
        pytest.param((None,), (20.0,), 20.0, Direction.OUT, id="DASH-003 outbound only"),
        pytest.param((20.0,), (None,), 20.0, Direction.IN, id="DASH-003 inbound only"),
    ],
)
def test_direction_choice(
    in_pct: tuple[float | None, ...],
    out_pct: tuple[float | None, ...],
    score: float,
    direction: Direction,
) -> None:
    """DASH-003: the score is the higher available direction.

    Args:
        in_pct: Inbound samples.
        out_pct: Outbound samples.
        score: Expected score.
        direction: Expected direction.

    Returns:
        None

    Raises:
        None
    """
    (e,) = rank_utilization([UtilizationSeries("x", in_pct, out_pct, 1)]).ranked
    assert (e.score_pct, e.direction) == (score, direction)


def errors_window(
    deltas: Sequence[tuple[int, int, int | None, int | None] | None],
) -> tuple[ErrorSample, ...]:
    """Build error samples; None marks an unavailable baseline.

    Args:
        deltas: (in errors, out errors, in packets, out packets) per sample.

    Returns:
        tuple[ErrorSample, ...]: The samples.

    Raises:
        None
    """
    return tuple(ErrorSample(None, None) if d is None else ErrorSample(*d) for d in deltas)


def test_dash_t02_valid_error_deltas_rank_and_cumulative_values_never_do() -> None:
    """DASH-T02: rank by valid deltas, then errors per million packets (DASH-004, RATE-007).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    reset = compute_rates(
        CounterSample(HC, 0, 0, 0, 0, 4_000_000_000, 4_000_000_000),
        CounterSample(HC, 0, 0, 0, 0, 5, 5),
        RateContext(60.0, 60.0, UptimeContinuity.RESET_DETECTED),
    )
    series = [
        ErrorSeries("quiet", errors_window([(0, 0, 10, 10)]), 1),
        ErrorSeries("many-packets", errors_window([(25, 25, 5_000_000, 5_000_000)]), 1),
        ErrorSeries("few-packets", errors_window([(30, 20, 500_000, 500_000)]), 1),
        ErrorSeries("no-packet-data", errors_window([(60, 40, None, None)]), 1),
        ErrorSeries("rebooted", (ErrorSample.from_rates(reset),), 1),
    ]
    ranking = rank_errors(series)
    assert [(e.key, e.errors, e.per_million) for e in ranking.ranked] == [
        ("no-packet-data", 100, None),
        ("few-packets", 50, 50.0),
        ("many-packets", 50, 5.0),
        ("quiet", 0, 0.0),
    ]
    assert [(x.key, x.reason) for x in ranking.excluded] == [("rebooted", NO_BASELINE)]
    assert (ranking.ranked[1].in_errors, ranking.ranked[1].out_errors) == (30, 20)


def test_dash_t02_ratio_needs_packet_data_for_every_counted_sample() -> None:
    """DASH-004: a ratio over partial packet data would be wrong, so it is withheld.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    mixed = ErrorSeries("mixed", errors_window([(5, 0, 100, 100), (5, 0, None, 100)]), 2)
    idle = ErrorSeries("idle", errors_window([(5, 0, 0, 0)]), 1)
    by_key = {e.key: e for e in rank_errors([mixed, idle]).ranked}
    assert (by_key["mixed"].errors, by_key["mixed"].per_million) == (10, None)
    assert by_key["idle"].per_million is None


def test_dash_t03_incomplete_coverage_is_labelled_and_unavailable_is_excluded() -> None:
    """DASH-T03: invalid baselines are excluded and partial coverage is visible (DASH-005).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    partial = UtilizationSeries("ge-0/0/1", (80.0,) * 12 + (None,) * 3, (1.0,) * 15, 15)
    full = UtilizationSeries("ge-0/0/3", (40.0,) * 15, (1.0,) * 15, 15, CapacitySource.IF_SPEED)
    blank = UtilizationSeries("ge-0/0/9", (None,) * 15, (None,) * 15, 15, CapacitySource.IF_SPEED)
    no_speed = UtilizationSeries("ge-0/0/4", (None,) * 15, (None,) * 15, 15)
    ranking = rank_utilization([full, blank, partial, no_speed], limit=1)
    (top,) = ranking.ranked
    assert (top.key, top.coverage.label, top.coverage.complete) == (
        "ge-0/0/1",
        "12 of 15 samples",
        False,
    )
    assert [(x.key, x.reason) for x in ranking.excluded] == [
        ("ge-0/0/4", NO_CAPACITY),
        ("ge-0/0/9", NO_BASELINE),
    ]
    gappy = ErrorSeries("ge-0/0/5", errors_window([(1, 0, 10, 10), None, None]), 3)
    (entry,) = rank_errors([gappy]).ranked
    assert (entry.coverage.label, entry.coverage.complete) == ("1 of 3 samples", False)


@pytest.mark.parametrize(
    ("window_s", "interval_s", "expected"),
    [
        pytest.param(900, 60, 15, id="DASH-003 15 minutes at 60 s"),
        pytest.param(900, 30, 30, id="DASH-003 15 minutes at 30 s"),
        pytest.param(900, 3600, 1, id="DASH-003 window shorter than the interval"),
    ],
)
def test_expected_samples(window_s: float, interval_s: float, expected: int) -> None:
    """Coverage is measured against whole intervals in the window.

    Args:
        window_s: Window in seconds.
        interval_s: Interval in seconds.
        expected: Expected sample count.

    Returns:
        None

    Raises:
        None
    """
    assert expected_samples(window_s, interval_s) == expected


def test_expected_samples_rejects_non_positive_values() -> None:
    """A zero window or interval has no meaningful coverage.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="positive"):
        expected_samples(0, 60)
