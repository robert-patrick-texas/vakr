"""Tests for ICMP probe-group summaries, E.1 states, and SNMP gating (ICMP-T01..T04).

Covers ICMP-001..010 (summary math and storage sentinel), ICMP-006, -007,
-013 and -014 (SNMP plan per strategy), Appendix E.1 (state table and
thresholds), and E.7 (rounding). Expected values are computed by hand in
each row so a regression shows exactly which number moved.

Dependencies:
    pytest, hypothesis: Tables and generated probe groups.
    vakr.logic.icmp: The functions under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_icmp.py
"""

from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vakr.domain.enums import CollectorState, IcmpState, ReachabilityStrategy
from vakr.domain.quality import Quality
from vakr.logic.icmp import (
    IcmpPolicy,
    Impairment,
    ProbeGroup,
    SnmpPlan,
    snmp_plan,
    summarise_probes,
)

PROPERTY = settings(derandomize=True, database=None, max_examples=400, deadline=None)
LOSS, LATENCY = Impairment.LOSS, Impairment.LATENCY
NO_REPLY = -1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            (1000, 1200, 1100, 1300, 1050),
            (5, 0, 0.0, 1000, 1130, 1100, 1300, 108),
            id="ICMP-T01 five replies: exact min/avg/median/max/jitter/loss",
        ),
        pytest.param(
            (NO_REPLY, 1500, NO_REPLY, 2500, NO_REPLY),
            (2, 3, 60.0, 1500, 2000, 2000, 2500, 500),
            id="ICMP-T02 2/5: -1 excluded from every statistic",
        ),
        pytest.param(
            (NO_REPLY, NO_REPLY, 1800, NO_REPLY, NO_REPLY),
            (1, 4, 80.0, 1800, 1800, 1800, 1800, None),
            id="ICMP-T03 1/5: jitter unavailable with one reply",
        ),
        pytest.param(
            (NO_REPLY,) * 5,
            (0, 5, 100.0, None, None, None, None, None),
            id="ICMP-T04 0/5: 100% loss, no latency or jitter summary",
        ),
    ],
)
def test_summary_math(raw: tuple[int, ...], expected: tuple[object, ...]) -> None:
    """Summary values follow 9.3 and E.7; lost probes never enter the math (ICMP-005).

    Args:
        raw: Stored per-probe values, ``-1`` for no reply (ICMP-004).
        expected: received, lost, loss %, min, avg, median, max, jitter.

    Returns:
        None

    Raises:
        None
    """
    group = ProbeGroup.from_raw(raw)
    s = summarise_probes(group)
    assert group.rtts_us == tuple(None if v == NO_REPLY else v for v in raw)  # ICMP-003
    assert s.sent == 5
    got = (s.received, s.lost, s.loss_percent, s.min_rtt_us, s.avg_rtt_us)
    assert (*got, s.median_rtt_us, s.max_rtt_us, s.jitter_us) == expected


@pytest.mark.parametrize(
    ("replies", "state", "quality", "impairments"),
    [
        pytest.param(5, IcmpState.REACHABLE, Quality.NONE, (), id="E.1 5/5 reachable"),
        pytest.param(4, IcmpState.REACHABLE, Quality.PARTIAL_LOSS, (), id="E.1 4/5 partial loss"),
        pytest.param(3, IcmpState.IMPAIRED, Quality.NONE, (LOSS,), id="E.1 3/5 impaired at 40%"),
        pytest.param(2, IcmpState.IMPAIRED, Quality.NONE, (LOSS,), id="ICMP-T02 2/5 impaired"),
        pytest.param(
            1, IcmpState.IMPAIRED, Quality.SINGLE_REPLY, (LOSS,), id="ICMP-T03 1/5 single reply"
        ),
        pytest.param(0, IcmpState.UNREACHABLE, Quality.NONE, (), id="ICMP-T04 0/5 unreachable"),
    ],
)
def test_e1_state_table(
    replies: int, state: IcmpState, quality: Quality, impairments: tuple[Impairment, ...]
) -> None:
    """Each reply count maps to the E.1 default state and flag.

    Args:
        replies: Replies out of five.
        state: Expected state.
        quality: Expected flags.
        impairments: Expected impairment reasons.

    Returns:
        None

    Raises:
        None
    """
    rtts = tuple([2000] * replies + [None] * (5 - replies))
    s = summarise_probes(ProbeGroup(rtts, sent=5))
    assert (s.state, s.quality, s.impairments) == (state, quality, impairments)


@pytest.mark.parametrize(
    ("group", "collector", "state", "quality"),
    [
        pytest.param(
            ProbeGroup((None,) * 5, 5),
            CollectorState.OVERLOADED,
            IcmpState.UNKNOWN,
            Quality.COLLECTOR_DEGRADED,
            id="ICMP-T04 0/5 from an overloaded collector is unknown, not down",
        ),
        pytest.param(
            ProbeGroup((), 0),
            CollectorState.HEALTHY,
            IcmpState.UNKNOWN,
            Quality.NOT_ATTEMPTED,
            id="ICMP-T04 poll not attempted is unknown",
        ),
        pytest.param(
            ProbeGroup((), 0),
            CollectorState.UNAVAILABLE,
            IcmpState.UNKNOWN,
            Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED,
            id="E.1 not attempted on an unavailable collector",
        ),
        pytest.param(
            ProbeGroup((900, None, 1000, 1100, 1000), 5),
            CollectorState.DEGRADED,
            IcmpState.REACHABLE,
            Quality.PARTIAL_LOSS | Quality.COLLECTOR_DEGRADED,
            id="E.1 replies still prove reachability; the loss is marked suspect",
        ),
    ],
)
def test_collector_context(
    group: ProbeGroup, collector: CollectorState, state: IcmpState, quality: Quality
) -> None:
    """Collector trouble never yields UNREACHABLE (invariant 2, OBS-003).

    Args:
        group: Probes.
        collector: Collector health.
        state: Expected state.
        quality: Expected flags.

    Returns:
        None

    Raises:
        None
    """
    s = summarise_probes(group, collector=collector)
    assert (s.state, s.quality) == (state, quality)
    if group.sent == 0:
        assert s.loss_percent is None
        assert s.median_rtt_us is None


@pytest.mark.parametrize(
    ("rtts", "policy", "state", "impairments", "quality"),
    [
        pytest.param(
            (1600, 1600, 1600, 1600, 1600),
            IcmpPolicy(impaired_rtt_ms=1.5),
            IcmpState.IMPAIRED,
            (LATENCY,),
            Quality.NONE,
            id="E.1 median above impaired_rtt_ms",
        ),
        pytest.param(
            (1500, 1500, 1500, 1500, 1500),
            IcmpPolicy(impaired_rtt_ms=1.5),
            IcmpState.REACHABLE,
            (),
            Quality.NONE,
            id="E.1 median equal to impaired_rtt_ms is not above it",
        ),
        pytest.param(
            (1005, 1005, 1005, 1005, 1005),
            IcmpPolicy(impaired_rtt_ms=1.005),
            IcmpState.REACHABLE,
            (),
            Quality.NONE,
            id="E.1 1.005 ms threshold compared exactly",
        ),
        pytest.param(
            (1600, 1600, 1600, 1600, None),
            IcmpPolicy(impaired_rtt_ms=1.5),
            IcmpState.IMPAIRED,
            (LATENCY,),
            Quality.PARTIAL_LOSS,
            id="E.1 latency with 4/5 keeps the partial-loss flag",
        ),
        pytest.param(
            (1600, 1600, None, None, None),
            IcmpPolicy(impaired_rtt_ms=1.5),
            IcmpState.IMPAIRED,
            (LOSS, LATENCY),
            Quality.NONE,
            id="E.1 loss and latency together",
        ),
        pytest.param(
            (1000, 1000, 1000, None, None),
            IcmpPolicy(impaired_loss_percent=50),
            IcmpState.REACHABLE,
            (),
            Quality.PARTIAL_LOSS,
            id="E.1 raised loss threshold keeps 3/5 reachable",
        ),
        pytest.param(
            (1000,),
            IcmpPolicy(),
            IcmpState.IMPAIRED,
            (),
            Quality.SINGLE_REPLY,
            id="E.1 one probe sent and answered is still a single reply",
        ),
    ],
)
def test_thresholds(
    rtts: tuple[int | None, ...],
    policy: IcmpPolicy,
    state: IcmpState,
    impairments: tuple[Impairment, ...],
    quality: Quality,
) -> None:
    """``impaired_loss_percent`` and ``impaired_rtt_ms`` drive IMPAIRED (E.1).

    Args:
        rtts: Probe results.
        policy: Thresholds.
        state: Expected state.
        impairments: Expected reasons.
        quality: Expected flags.

    Returns:
        None

    Raises:
        None
    """
    s = summarise_probes(ProbeGroup(rtts, sent=len(rtts)), policy)
    assert (s.state, s.impairments, s.quality) == (state, impairments, quality)


GATED, FALLBACK = ReachabilityStrategy.ICMP_GATED, ReachabilityStrategy.SNMP_FALLBACK
ICMP_ONLY, SNMP_ONLY = ReachabilityStrategy.ICMP_ONLY, ReachabilityStrategy.SNMP_ONLY
COLLECT, STAGE1, SKIP = SnmpPlan.COLLECT, SnmpPlan.STAGE1_FALLBACK, SnmpPlan.SKIP


@pytest.mark.parametrize(
    ("strategy", "plans"),
    [
        pytest.param(GATED, (SKIP, SKIP, COLLECT, COLLECT), id="ICMP-006 ICMP-007 icmp_gated"),
        pytest.param(FALLBACK, (STAGE1, STAGE1, COLLECT, COLLECT), id="ICMP-013 snmp_fallback"),
        pytest.param(ICMP_ONLY, (SKIP, SKIP, SKIP, SKIP), id="ICMP-014 icmp_only"),
        pytest.param(SNMP_ONLY, (COLLECT, COLLECT, COLLECT, COLLECT), id="ICMP-014 snmp_only"),
    ],
)
def test_snmp_plan_per_strategy(
    strategy: ReachabilityStrategy, plans: tuple[SnmpPlan, ...]
) -> None:
    """SNMP runs, falls back, or is skipped per strategy for 0, 1, 2 and 5 replies.

    Args:
        strategy: Reachability strategy.
        plans: Expected plan for 0, 1, 2 and 5 replies.

    Returns:
        None

    Raises:
        None
    """
    assert tuple(snmp_plan(n, strategy) for n in (0, 1, 2, 5)) == plans


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: ProbeGroup((1, 2), sent=3), id="sent disagrees with entries"),
        pytest.param(lambda: ProbeGroup((1, -1), sent=2), id="raw sentinel passed as an RTT"),
        pytest.param(lambda: ProbeGroup.from_raw((1, -2)), id="negative non-sentinel"),
        pytest.param(lambda: IcmpPolicy(impaired_loss_percent=0), id="zero loss threshold"),
        pytest.param(lambda: IcmpPolicy(impaired_loss_percent=101), id="loss threshold over 100"),
        pytest.param(lambda: IcmpPolicy(impaired_rtt_ms=0), id="zero RTT threshold"),
    ],
)
def test_invalid_inputs_are_rejected(build: Callable[[], object]) -> None:
    """Inputs that would corrupt a summary raise instead of being guessed at.

    Args:
        build: Zero-argument callable that must raise.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"sent=|non-negative|must be"):
        build()


GROUPS = st.lists(st.one_of(st.none(), st.integers(0, 5_000_000)), min_size=1, max_size=10)


@PROPERTY
@given(GROUPS)
def test_summary_invariants(rtts: list[int | None]) -> None:
    """Order statistics bracket each other; jitter is bounded by half the range.

    Args:
        rtts: Generated probe results.

    Returns:
        None

    Raises:
        None
    """
    s = summarise_probes(ProbeGroup(tuple(rtts), sent=len(rtts)))
    assert s.received + s.lost == s.sent
    assert (s.jitter_us is None) == (s.received < 2)
    if s.received == 0:
        assert s.state is IcmpState.UNREACHABLE
        return
    assert s.min_rtt_us is not None
    assert s.max_rtt_us is not None
    assert s.min_rtt_us <= (s.median_rtt_us or 0) <= s.max_rtt_us
    assert s.min_rtt_us <= (s.avg_rtt_us or 0) <= s.max_rtt_us
    assert (s.jitter_us or 0) <= (s.max_rtt_us - s.min_rtt_us) / 2 + 1
    assert s.state is not IcmpState.UNREACHABLE


@PROPERTY
@given(GROUPS, st.integers(0, 10))
def test_lost_probes_never_change_latency(rtts: list[int | None], extra_lost: int) -> None:
    """Adding lost probes changes loss only, never latency or jitter (ICMP-005).

    Args:
        rtts: Generated probe results.
        extra_lost: Lost probes to append.

    Returns:
        None

    Raises:
        None
    """
    base = summarise_probes(ProbeGroup(tuple(rtts), sent=len(rtts)))
    more = rtts + [None] * extra_lost
    lossier = summarise_probes(ProbeGroup(tuple(more), sent=len(more)))
    latency = ("min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us")
    assert [getattr(base, f) for f in latency] == [getattr(lossier, f) for f in latency]
    assert (lossier.loss_percent or 0) >= (base.loss_percent or 0)
