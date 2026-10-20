"""Tests for counter baselines and rates (RATE-T01..T07, RATE-T09, section 9.7, E.4).

Every 9.7 invalidation reason has a row, each asserting both the flag and
that no rate leaks out (RATE-004): a refused baseline is a gap, never a
spike. RATE-T08 (denominators) lives in ``test_logic_capacity.py`` and the
generated reset sequences in ``test_logic_rates_property.py``.

Dependencies:
    pytest: Tables.
    vakr.logic.rates, vakr.logic.baseline, vakr.logic.counters: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_rates.py
"""

from collections.abc import Callable
from dataclasses import replace

import pytest

from vakr.domain.enums import BaselineState, CounterFamily, UptimeContinuity
from vakr.domain.quality import BASELINE_INVALIDATING, Quality
from vakr.logic.baseline import RateContext, max_gap_s
from vakr.logic.capacity import CapacityInputs, CapacitySource
from vakr.logic.counters import COUNTERS, CounterSample
from vakr.logic.rates import RateResult, compute_rates

HC, C32 = CounterFamily.HC64, CounterFamily.LEGACY32
CONT = UptimeContinuity.CONTINUOUS
GIG = CapacityInputs(if_high_speed_mbps=1000)
FAST_E = CapacityInputs(if_speed_bps=100_000_000)
BASE = 10**12
Q = Quality


def sample(
    family: CounterFamily = HC, start: int = BASE, errors: int = 1000, **overrides: object
) -> CounterSample:
    """Build a sample: octets and packets at ``start``, errors at ``errors``.

    Args:
        family: Counter family.
        start: Value for the octet and packet counters.
        errors: Value for the error counters, which are always 32-bit.
        **overrides: Field values to replace.

    Returns:
        CounterSample: The sample.

    Raises:
        None
    """
    return replace(CounterSample(family, start, start, start, start, errors, errors), **overrides)


def ctx(elapsed: float | None = 60.0, **overrides: object) -> RateContext:
    """Build a pair context: 60 s apart at a 60 s interval, continuous uptime, 1 Gb/s.

    Args:
        elapsed: Seconds between the samples.
        **overrides: Field values to replace.

    Returns:
        RateContext: The context.

    Raises:
        None
    """
    return replace(RateContext(elapsed, 60.0, CONT, GIG), **overrides)


def assert_no_rates(r: RateResult) -> None:
    """Assert that nothing derived leaks from a refused baseline (RATE-004).

    Args:
        r: The result.

    Returns:
        None

    Raises:
        AssertionError: If any delta, rate, or utilization is present.
    """
    assert all(r.metric(n).delta is None and r.metric(n).rate is None for n in COUNTERS)
    assert r.in_utilization_pct is None
    assert r.out_utilization_pct is None


def test_rate_t01_normal_64_bit_rate_uses_actual_elapsed_time() -> None:
    """RATE-T01: exact bps, pps, and error rate over the actual 62.5 s (RATE-001, -006, -007).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    curr = sample(
        in_octets=BASE + 781_250_000,
        out_octets=BASE + 390_625_000,
        in_packets=BASE + 125_000,
        out_packets=BASE + 62_500,
        in_errors=1025,
    )
    r = compute_rates(sample(), curr, ctx(62.5))
    assert (r.baseline, r.quality) == (BaselineState.VALID, Q.NONE)
    assert (r.in_octets.delta, r.in_octets.rate) == (781_250_000, 100_000_000.0)
    assert r.out_octets.rate == 50_000_000.0
    assert (r.in_packets.rate, r.out_packets.rate) == (2000.0, 1000.0)
    assert (r.in_errors.delta, r.in_errors.rate) == (25, 0.4)
    assert (r.out_errors.delta, r.out_errors.rate) == (0, 0.0)
    assert (r.in_utilization_pct, r.out_utilization_pct) == (10.0, 5.0)
    assert r.capacity is not None
    assert r.capacity.source is CapacitySource.IF_HIGH_SPEED


@pytest.mark.parametrize(
    ("prior_missing", "flag"),
    [
        pytest.param(False, Q.BASELINE_FIRST_SAMPLE, id="RATE-T02 first sample"),
        pytest.param(True, Q.BASELINE_MISSING_PRIOR, id="RATE-T02 missing prior sample"),
    ],
)
def test_rate_t02_no_prior_sample(prior_missing: bool, flag: Quality) -> None:
    """RATE-T02: raw counters kept, every rate unavailable, then the baseline holds.

    Args:
        prior_missing: Whether an earlier baseline existed.
        flag: Expected reason.

    Returns:
        None

    Raises:
        None
    """
    first = sample()
    r = compute_rates(None, first, ctx(None, prior_missing=prior_missing))
    assert (r.baseline, r.quality) == (BaselineState.NO_BASELINE, flag)
    assert_no_rates(r)
    assert r.in_octets.quality == flag
    following = compute_rates(first, sample(in_octets=BASE + 750_000), ctx())
    assert (following.baseline, following.in_octets.rate) == (BaselineState.VALID, 100_000.0)


SPIKE = BASE + 10**15  # what a naive subtraction would turn into a petabit "rate"


@pytest.mark.parametrize(
    ("prev", "curr", "context", "flags"),
    [
        pytest.param(
            sample(),
            sample(start=1_000),
            ctx(continuity=UptimeContinuity.RESET_DETECTED),
            Q.BASELINE_UPTIME_RESET,
            id="RATE-T03 reboot: counters fell, reported as uptime reset",
        ),
        pytest.param(
            sample(),
            sample(start=SPIKE),
            ctx(continuity=UptimeContinuity.RESET_DETECTED),
            Q.BASELINE_UPTIME_RESET,
            id="RATE-T03 reboot with counters above the old value",
        ),
        pytest.param(
            sample(),
            sample(in_octets=BASE + 1),
            ctx(continuity=UptimeContinuity.UNKNOWN),
            Q.BASELINE_UPTIME_RESET,
            id="RATE-T03 inconsistent continuity (UPTIME_AHEAD)",
        ),
        pytest.param(
            sample(discontinuity_ticks=500),
            sample(start=SPIKE, discontinuity_ticks=9_000),
            ctx(),
            Q.BASELINE_DISCONTINUITY,
            id="RATE-T03 ifCounterDiscontinuityTime changed",
        ),
        pytest.param(
            sample(),
            sample(discontinuity_ticks=9_000),
            ctx(),
            Q.BASELINE_DISCONTINUITY,
            id="RATE-T03 discontinuity support appeared",
        ),
        pytest.param(
            sample(),
            sample(in_octets=BASE // 2),
            ctx(),
            Q.BASELINE_DECREASE,
            id="RATE-T05 unexplained HC decrease: no wrap, no spike",
        ),
        pytest.param(
            sample(C32, start=3_000_000_000),
            sample(C32, start=1_000_000_000),
            ctx(capacity=FAST_E),
            Q.BASELINE_DECREASE,
            id="RATE-T05 32-bit decrease too large to be a wrap",
        ),
        pytest.param(
            sample(),
            sample(in_octets=BASE + 1),
            ctx(301.0),
            Q.BASELINE_GAP,
            id="RATE-T06 HC gap over 5 x interval",
        ),
        pytest.param(
            sample(C32, start=1000),
            sample(C32, start=2000),
            ctx(),
            Q.BASELINE_GAP,
            id="RATE-T06 32-bit gap over half the wrap time at 1 Gb/s",
        ),
        pytest.param(
            sample(),
            sample(),
            ctx(0.0),
            Q.BASELINE_ELAPSED_INVALID,
            id="RATE-003 zero elapsed time",
        ),
        pytest.param(
            sample(),
            sample(),
            ctx(-5.0),
            Q.BASELINE_ELAPSED_INVALID,
            id="RATE-003 negative elapsed time",
        ),
        pytest.param(
            sample(C32, start=1000),
            sample(start=2000),
            ctx(),
            Q.BASELINE_WIDTH_CHANGE,
            id="RATE-T07 32-bit to HC switch",
        ),
        pytest.param(
            sample(identity_resolved=False),
            sample(),
            ctx(),
            Q.BASELINE_IDENTITY_UNRESOLVED,
            id="RATE-003 prior identity unresolved",
        ),
        pytest.param(
            sample(),
            sample(identity_resolved=False),
            ctx(),
            Q.BASELINE_IDENTITY_UNRESOLVED,
            id="RATE-003 current identity unresolved",
        ),
        pytest.param(
            sample(C32, identity_resolved=False, start=5),
            sample(start=SPIKE),
            ctx(400.0),
            Q.BASELINE_IDENTITY_UNRESOLVED | Q.BASELINE_WIDTH_CHANGE | Q.BASELINE_GAP,
            id="RATE-003 every applicable reason is recorded",
        ),
    ],
)
def test_invalid_baselines_never_spike(
    prev: CounterSample, curr: CounterSample, context: RateContext, flags: Quality
) -> None:
    """RATE-T03, -T05, -T06, -T07: each 9.7 reason invalidates and is flagged (RATE-004).

    Args:
        prev: Prior sample.
        curr: Current sample.
        context: Pair evidence.
        flags: Expected invalidating flags.

    Returns:
        None

    Raises:
        None
    """
    r = compute_rates(prev, curr, context)
    assert r.baseline is BaselineState.INVALIDATED
    assert r.quality & BASELINE_INVALIDATING == flags
    assert Q.BASELINE_DECREASE not in r.quality or flags == Q.BASELINE_DECREASE
    assert_no_rates(r)


def test_rate_t07_family_switch_invalidates_exactly_one_interval() -> None:
    """RATE-T07: after a 32-bit to HC switch, the next HC pair is valid again.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    legacy, hc1, hc2 = sample(C32, start=1000), sample(start=BASE), sample(start=BASE + 7500)
    assert compute_rates(legacy, hc1, ctx()).baseline is BaselineState.INVALIDATED
    again = compute_rates(hc1, hc2, ctx())
    assert (again.baseline, again.in_octets.rate) == (BaselineState.VALID, 1000.0)


WRAP = 2**32


@pytest.mark.parametrize(
    ("prev", "curr", "context", "name", "delta"),
    [
        pytest.param(
            sample(C32, start=WRAP - 1_000_000),
            sample(C32, start=6_500_000),
            ctx(capacity=FAST_E),
            "in_octets",
            7_500_000,
            id="RATE-T04 32-bit octet wrap at 1 Mb/s on a 100 Mb/s port",
        ),
        pytest.param(
            sample(C32, start=WRAP - 10),
            sample(C32, start=5),
            ctx(capacity=FAST_E),
            "in_packets",
            15,
            id="RATE-T04 32-bit packet wrap",
        ),
        pytest.param(
            sample(errors=WRAP - 5),
            sample(errors=10),
            ctx(),
            "in_errors",
            15,
            id="RATE-T04 error counters are 32-bit even on HC samples",
        ),
        pytest.param(
            sample(C32, 1000, in_packets=3 * WRAP - 10),
            sample(C32, 1000, in_packets=WRAP - 5),
            ctx(capacity=FAST_E),
            "in_packets",
            5,
            id="RATE-T04 aggregate of three packet components, two wrapped",
        ),
    ],
)
def test_rate_t04_plausible_wrap_is_accepted(
    prev: CounterSample, curr: CounterSample, context: RateContext, name: str, delta: int
) -> None:
    """RATE-T04: a wrap with consistent evidence gives the true delta (RATE-005).

    Args:
        prev: Prior sample.
        curr: Current sample.
        context: Pair evidence: continuous, unchanged discontinuity, known speed.
        name: Counter that wrapped.
        delta: True delta.

    Returns:
        None

    Raises:
        None
    """
    r = compute_rates(prev, curr, context)
    assert r.baseline is BaselineState.VALID
    assert r.metric(name).delta == delta
    assert Q.COUNTER_WRAP_32 in r.metric(name).quality
    assert Q.COUNTER_WRAP_32 in r.quality
    if name == "in_octets":
        assert r.in_octets.rate == 1_000_000.0


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(ctx(capacity=CapacityInputs()), id="RATE-T04 no line rate: wrap unproven"),
        pytest.param(
            ctx(capacity=CapacityInputs(contracted_bps=10**8, contracted_is_denominator=True)),
            id="RATE-T04 a contracted rate is not a line rate",
        ),
        pytest.param(
            ctx(capacity=CapacityInputs(if_speed_bps=10**5)),
            id="RATE-T04 wrapped delta impossible at 100 kb/s",
        ),
    ],
)
def test_rate_t04_wrap_without_consistent_evidence_is_refused(context: RateContext) -> None:
    """RATE-T04: without line-rate evidence or plausibility, a decrease stays a decrease.

    Args:
        context: Pair evidence lacking support for the wrap.

    Returns:
        None

    Raises:
        None
    """
    prev, curr = sample(C32, start=WRAP - 1_000_000), sample(C32, start=6_500_000)
    r = compute_rates(prev, curr, context)
    assert (r.baseline, r.quality & BASELINE_INVALIDATING) == (
        BaselineState.INVALIDATED,
        Q.BASELINE_DECREASE,
    )
    assert_no_rates(r)


@pytest.mark.parametrize(
    ("in_bps", "flagged"),
    [
        pytest.param(120_000_000, True, id="RATE-T09 120% of capacity kept and flagged"),
        pytest.param(110_000_000, False, id="RATE-T09 exactly 1.10 x is not flagged"),
        pytest.param(105_000_000, False, id="RATE-T09 105% kept, not clipped"),
    ],
)
def test_rate_t09_over_capacity_is_kept_and_flagged(in_bps: int, flagged: bool) -> None:
    """RATE-T09: over-capacity rates are flagged above 1.10 x, never clipped (RATE-009).

    Args:
        in_bps: Inbound rate to produce on a 100 Mb/s interface.
        flagged: Whether ``RATE_EXCEEDS_CAPACITY`` is expected.

    Returns:
        None

    Raises:
        None
    """
    curr = sample(in_octets=BASE + in_bps * 60 // 8)
    r = compute_rates(sample(), curr, ctx(capacity=FAST_E))
    assert r.in_octets.rate == float(in_bps)
    assert r.in_utilization_pct == pytest.approx(in_bps / 1_000_000)
    assert (Q.RATE_EXCEEDS_CAPACITY in r.in_octets.quality) is flagged
    assert (Q.RATE_EXCEEDS_CAPACITY in r.quality) is flagged
    assert Q.RATE_EXCEEDS_CAPACITY not in r.out_octets.quality


def test_rate_010_unknown_capacity_gives_absolute_rates_only() -> None:
    """RATE-010: no denominator means absolute rates and unavailable utilization.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    r = compute_rates(sample(), sample(in_octets=BASE + 7500), ctx(capacity=CapacityInputs()))
    assert (r.baseline, r.quality) == (BaselineState.VALID, Q.CAPACITY_UNKNOWN)
    assert (r.in_octets.rate, r.in_utilization_pct, r.capacity) == (1000.0, None, None)


def test_missing_counters_affect_only_their_metric() -> None:
    """RATE-003: a missing current or prior counter blanks that metric alone (invariant 4).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    r = compute_rates(sample(out_errors=None), sample(in_errors=None), ctx())
    assert (r.baseline, r.quality) == (BaselineState.VALID, Q.MISSING_FIELDS)
    assert (r.in_errors.rate, r.in_errors.quality) == (None, Q.MISSING_FIELDS)
    assert (r.out_errors.rate, r.out_errors.quality) == (None, Q.BASELINE_MISSING_PRIOR)
    assert r.in_octets.rate == 0.0
    empty = replace(sample(), **dict.fromkeys(COUNTERS))
    nothing = compute_rates(sample(), empty, ctx())
    assert (nothing.baseline, nothing.quality) == (BaselineState.INVALIDATED, Q.MISSING_FIELDS)
    unusable = compute_rates(empty, sample(), ctx())
    assert (unusable.baseline, unusable.quality) == (
        BaselineState.INVALIDATED,
        Q.BASELINE_MISSING_PRIOR,
    )
    bare = compute_rates(None, sample(), ctx(None, capacity=CapacityInputs()))
    assert bare.quality == Q.BASELINE_FIRST_SAMPLE | Q.CAPACITY_UNKNOWN
    assert bare.in_octets.quality == Q.BASELINE_FIRST_SAMPLE  # capacity is sample-wide only
    first = compute_rates(None, sample(in_octets=None), ctx(None))
    assert first.quality == Q.BASELINE_FIRST_SAMPLE | Q.MISSING_FIELDS
    assert first.in_octets.quality == Q.BASELINE_FIRST_SAMPLE | Q.MISSING_FIELDS
    assert first.out_octets.quality == Q.BASELINE_FIRST_SAMPLE


@pytest.mark.parametrize(
    ("family", "line_rate", "expected"),
    [
        pytest.param(HC, 10**9, 300.0, id="E.4 HC: 5 x interval regardless of speed"),
        pytest.param(C32, None, 300.0, id="E.4 32-bit, unknown speed: 5 x interval"),
        pytest.param(C32, 10**7, 300.0, id="E.4 32-bit at 10 Mb/s: interval rule is smaller"),
        pytest.param(C32, 10**8, 2**34 / 10**8, id="E.4 32-bit at 100 Mb/s: half wrap 171.8 s"),
    ],
)
def test_max_gap(family: CounterFamily, line_rate: int | None, expected: float) -> None:
    """E.4: maximum baseline gap for each counter family.

    Args:
        family: Counter family.
        line_rate: Line rate in b/s.
        expected: Maximum gap in seconds at a 60 s interval.

    Returns:
        None

    Raises:
        None
    """
    assert max_gap_s(family, 60.0, line_rate) == expected


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: CounterSample(HC, in_octets=-1), id="negative counter"),
        pytest.param(lambda: CounterSample(C32, in_octets=WRAP), id="32-bit octets at 2^32"),
        pytest.param(lambda: CounterSample(HC, in_errors=WRAP), id="errors are 32-bit on HC"),
        pytest.param(lambda: CounterSample(HC, in_octets=2**64), id="HC octets at 2^64"),
        pytest.param(lambda: ctx(interval_s=0.0), id="zero interval"),
        pytest.param(lambda: ctx(plausibility_factor=0.0), id="zero plausibility factor"),
        pytest.param(lambda: ctx(float("nan")), id="NaN elapsed"),
        pytest.param(
            lambda: compute_rates(sample(), sample(), ctx(None)), id="prior without elapsed"
        ),
    ],
)
def test_invalid_inputs_are_rejected(build: Callable[[], object]) -> None:
    """Values no agent or caller should produce raise instead of being guessed at.

    Args:
        build: Zero-argument callable that must raise.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"range|positive|finite|required"):
        build()


def test_aggregate_packet_counters_may_exceed_32_bits() -> None:
    """SNMP-008: summed packet components are not limited to one counter's range.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert CounterSample(C32, in_packets=3 * WRAP - 1).in_packets == 3 * WRAP - 1
