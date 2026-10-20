"""Tests for the utilization denominator and line rate (RATE-T08, RATE-008, RATE-010).

The denominator order is override, contracted rate (only when configured as
the denominator), ``ifHighSpeed`` normalised from Mb/s, then ``ifSpeed``.
Each row also checks the label the UI shows. Line rate, used for 32-bit wrap
checks, is tested separately because it deliberately ignores contracts.

Dependencies:
    pytest: Tables.
    vakr.logic.capacity, vakr.logic.rates: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_capacity.py
"""

from dataclasses import replace

import pytest

from vakr.domain.enums import CounterFamily, UptimeContinuity
from vakr.logic.baseline import RateContext
from vakr.logic.capacity import (
    IF_SPEED_MAX,
    Capacity,
    CapacityInputs,
    CapacitySource,
    effective_capacity,
    line_rate_bps,
)
from vakr.logic.counters import CounterSample
from vakr.logic.rates import compute_rates

ALL = CapacityInputs(
    override_bps=50_000_000,
    contracted_bps=20_000_000,
    contracted_is_denominator=True,
    if_high_speed_mbps=1000,
    if_speed_bps=1_000_000_000,
)


def inputs(**changes: object) -> CapacityInputs:
    """Build capacity inputs from ``ALL`` with some fields replaced.

    Args:
        **changes: Field values to replace; None removes a source.

    Returns:
        CapacityInputs: The inputs.

    Raises:
        None
    """
    return replace(ALL, **changes)


@pytest.mark.parametrize(
    ("given", "bps", "source", "label"),
    [
        pytest.param(
            inputs(),
            50_000_000,
            CapacitySource.OVERRIDE,
            "capacity override",
            id="RATE-T08 override wins over everything",
        ),
        pytest.param(
            inputs(override_bps=None),
            20_000_000,
            CapacitySource.CONTRACTED,
            "contracted rate",
            id="RATE-T08 contracted rate as denominator",
        ),
        pytest.param(
            inputs(override_bps=None, contracted_is_denominator=False),
            10**9,
            CapacitySource.IF_HIGH_SPEED,
            "ifHighSpeed",
            id="RATE-T08 contract not configured as denominator is skipped",
        ),
        pytest.param(
            inputs(override_bps=None, contracted_bps=None),
            10**9,
            CapacitySource.IF_HIGH_SPEED,
            "ifHighSpeed",
            id="RATE-T08 ifHighSpeed normalised from Mb/s",
        ),
        pytest.param(
            CapacityInputs(if_high_speed_mbps=0, if_speed_bps=10_000_000),
            10_000_000,
            CapacitySource.IF_SPEED,
            "ifSpeed",
            id="RATE-T08 ifHighSpeed of 0 means unknown; ifSpeed used",
        ),
        pytest.param(
            CapacityInputs(if_high_speed_mbps=100_000, if_speed_bps=IF_SPEED_MAX),
            10**11,
            CapacitySource.IF_HIGH_SPEED,
            "ifHighSpeed",
            id="RATE-T08 100G: saturated ifSpeed, ifHighSpeed used",
        ),
        pytest.param(
            CapacityInputs(if_speed_bps=1_544_000),
            1_544_000,
            CapacitySource.IF_SPEED,
            "ifSpeed",
            id="RATE-T08 ifSpeed alone",
        ),
    ],
)
def test_rate_t08_denominator_precedence_and_label(
    given: CapacityInputs, bps: int, source: CapacitySource, label: str
) -> None:
    """RATE-T08: the first usable source in RATE-008 order, with its label.

    Args:
        given: What is known about the interface.
        bps: Expected denominator.
        source: Expected source.
        label: Expected UI label.

    Returns:
        None

    Raises:
        None
    """
    capacity = effective_capacity(given)
    assert capacity == Capacity(bps, source)
    assert capacity.label == label


@pytest.mark.parametrize(
    "given",
    [
        pytest.param(CapacityInputs(), id="RATE-T08 nothing known"),
        pytest.param(
            CapacityInputs(if_speed_bps=IF_SPEED_MAX), id="RATE-T08 saturated ifSpeed only"
        ),
        pytest.param(CapacityInputs(if_speed_bps=0, if_high_speed_mbps=0), id="RATE-T08 zeros"),
        pytest.param(
            CapacityInputs(contracted_bps=10**8), id="RATE-T08 contract not a denominator"
        ),
    ],
)
def test_rate_t08_unknown_capacity(given: CapacityInputs) -> None:
    """RATE-010: with no usable source, utilization is unavailable, not guessed.

    Args:
        given: What is known about the interface.

    Returns:
        None

    Raises:
        None
    """
    assert effective_capacity(given) is None
    prev, curr = (
        CounterSample(CounterFamily.HC64, in_octets=0),
        CounterSample(CounterFamily.HC64, in_octets=7500),
    )
    r = compute_rates(prev, curr, RateContext(60.0, 60.0, UptimeContinuity.CONTINUOUS, given))
    assert (r.in_octets.rate, r.in_utilization_pct, r.capacity) == (1000.0, None, None)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(CapacityInputs(), None, id="E.4 unknown"),
        pytest.param(
            CapacityInputs(contracted_bps=10**8, contracted_is_denominator=True),
            None,
            id="E.4 a contract does not bound what the hardware counts",
        ),
        pytest.param(
            CapacityInputs(override_bps=5 * 10**7, if_high_speed_mbps=1000),
            10**9,
            id="E.4 sub-rate override on a gigabit port: the port speed",
        ),
        pytest.param(
            CapacityInputs(override_bps=10**10, if_speed_bps=10**9),
            10**10,
            id="E.4 override above a wrong ifSpeed",
        ),
        pytest.param(
            CapacityInputs(if_speed_bps=IF_SPEED_MAX),
            IF_SPEED_MAX,
            id="E.4 saturated ifSpeed is a lower bound",
        ),
    ],
)
def test_line_rate(given: CapacityInputs, expected: int | None) -> None:
    """Line rate is the largest physical or explicit rate, for wrap safety (E.4).

    Args:
        given: What is known about the interface.
        expected: Expected line rate.

    Returns:
        None

    Raises:
        None
    """
    assert line_rate_bps(given) == expected


def test_negative_capacity_is_rejected() -> None:
    """No agent or configuration can mean a negative speed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="negative"):
        CapacityInputs(if_speed_bps=-1)


def test_slow_links_prefer_exact_if_speed_over_rounded_if_high_speed() -> None:
    """A T1 reports ifHighSpeed 2 (rounded) but ifSpeed 1544000 (exact); use the exact one.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    from vakr.logic.capacity import CapacityInputs, CapacitySource, effective_capacity

    t1 = effective_capacity(CapacityInputs(if_high_speed_mbps=2, if_speed_bps=1_544_000))
    assert t1 is not None
    assert (t1.bps, t1.source) == (1_544_000, CapacitySource.IF_SPEED)
    fast = effective_capacity(CapacityInputs(if_high_speed_mbps=1000, if_speed_bps=1_000_000_000))
    assert fast is not None
    assert fast.source is CapacitySource.IF_HIGH_SPEED
    no_if_speed = effective_capacity(CapacityInputs(if_high_speed_mbps=2, if_speed_bps=None))
    assert no_if_speed is not None
    assert (no_if_speed.bps, no_if_speed.source) == (2_000_000, CapacitySource.IF_HIGH_SPEED)
