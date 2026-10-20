"""Tests for turning probe results into ICMP observations (ICMP-T01..T04, T06).

The summary math itself is tested in ``tests/unit/logic/test_logic_icmp.py``;
these tests prove the collector feeds it correctly: only probes that say
something about the target are summarised, E.1 flags reach the
observation, collector trouble becomes ``COLLECTOR_DEGRADED``/``UNKNOWN``
instead of loss (invariant 2), and family and source are recorded.

Dependencies:
    asyncio, pytest: Fake prober and tables.
    tests.unit.collectors.icmp.icmp_results: Compact result builders.
    vakr.collectors.icmp: The code under test.

Example:
    $ uv run pytest tests/unit/collectors/icmp/test_icmp_cycle.py
"""

import asyncio

import pytest

from tests.unit.collectors.icmp.icmp_results import START, ids, make_result
from vakr.collectors.icmp.cycle import IcmpCollector, PollContext, build_cycle, collector_state
from vakr.collectors.icmp.settings import IcmpSettings
from vakr.domain.enums import AddressFamily, CollectorState, IcmpState, ReachabilityStrategy
from vakr.domain.ids import ObservationId, new_id
from vakr.domain.quality import Quality
from vakr.logic.icmp import IcmpPolicy, Impairment, SnmpPlan
from vakr.ports.icmp import CollectorFault, IcmpCounters, ProbeRequest, ProbeResult

GATED, FALLBACK = ReachabilityStrategy.ICMP_GATED, ReachabilityStrategy.SNMP_FALLBACK
ONLY = ReachabilityStrategy.ICMP_ONLY


def context(
    strategy: ReachabilityStrategy = GATED, collector: CollectorState = CollectorState.HEALTHY
) -> PollContext:
    """Return a poll context with fresh IDs.

    Args:
        strategy: Reachability strategy.
        collector: Collector health as the scheduler sees it.

    Returns:
        PollContext: The context.

    Raises:
        None
    """
    return PollContext(*ids(), strategy=strategy, collector=collector)


def test_icmp_t01_five_replies_give_the_exact_observation() -> None:
    """ICMP-T01, ICMP-001..010: raw RTTs and every summary value are exact.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ctx = context()
    obs_id = new_id(ObservationId)
    cycle = build_cycle(make_result([1000, 1200, 1100, 1300, 1050]), ctx, observation_id=obs_id)
    obs = cycle.observation
    assert obs is not None
    assert (obs.observation_id, obs.poll_id, obs.device_id) == (obs_id, ctx.poll_id, ctx.device_id)
    assert obs.rtts_us == (1000, 1200, 1100, 1300, 1050)
    assert (obs.min_rtt_us, obs.avg_rtt_us, obs.median_rtt_us, obs.max_rtt_us) == (
        1000,
        1130,
        1100,
        1300,
    )
    assert obs.jitter_us == 108
    assert (obs.state, obs.quality, obs.timestamp) == (IcmpState.REACHABLE, Quality.NONE, START)
    assert (obs.address_family, obs.target_address, obs.source_address) == (
        AddressFamily.IPV4,
        "192.0.2.1",
        None,
    )
    assert cycle.snmp is SnmpPlan.COLLECT


@pytest.mark.parametrize(
    ("specs", "state", "quality", "plan", "loss"),
    [
        pytest.param(
            [900, 950, -1, 1000, 980],
            IcmpState.REACHABLE,
            Quality.PARTIAL_LOSS,
            SnmpPlan.COLLECT,
            20.0,
            id="4/5 PARTIAL_LOSS",
        ),
        pytest.param(
            [-1, 1500, "u", 2500, -1],
            IcmpState.IMPAIRED,
            Quality.NONE,
            SnmpPlan.COLLECT,
            60.0,
            id="ICMP-T02 2/5",
        ),
        pytest.param(
            [-1, -1, 1800, -1, -1],
            IcmpState.IMPAIRED,
            Quality.SINGLE_REPLY,
            SnmpPlan.SKIP,
            80.0,
            id="ICMP-T03 1/5 SINGLE_REPLY",
        ),
        pytest.param(
            [-1, "u", -1, -1, -1],
            IcmpState.UNREACHABLE,
            Quality.NONE,
            SnmpPlan.SKIP,
            100.0,
            id="ICMP-T04 0/5",
        ),
    ],
)
def test_e1_states_flags_and_gating_reach_the_observation(
    specs: list[int | str], state: IcmpState, quality: Quality, plan: SnmpPlan, loss: float
) -> None:
    """ICMP-T02..T04, E.1: states, flags, SNMP gating; an unreachable is a lost probe.

    Args:
        specs: Probe outcomes in compact notation.
        state: Expected state.
        quality: Expected flags.
        plan: Expected SNMP plan under ``icmp_gated``.
        loss: Expected loss percent.

    Returns:
        None

    Raises:
        None
    """
    cycle = build_cycle(make_result(specs), context())
    assert cycle.observation is not None
    assert (cycle.observation.state, cycle.observation.quality) == (state, quality)
    assert (cycle.snmp, cycle.summary.loss_percent) == (plan, loss)
    if cycle.summary.received < 2:
        assert cycle.observation.jitter_us is None


def test_icmp_t04_zero_replies_store_no_summary() -> None:
    """ICMP-T04, ICMP-004/005: five lost probes, 100% loss, no latency or jitter.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    obs = build_cycle(make_result([-1] * 5), context()).observation
    assert obs is not None
    assert obs.rtts_us == (None,) * 5
    summaries = (obs.min_rtt_us, obs.avg_rtt_us, obs.median_rtt_us, obs.max_rtt_us, obs.jitter_us)
    assert summaries == (None,) * 5


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(make_result([-1] * 5, fault=CollectorFault.RECEIVE_OVERFLOW), id="overflow"),
        pytest.param(make_result([-1, -1, "a", "a", "a"]), id="socket failed mid-cycle"),
    ],
)
def test_collector_trouble_makes_silence_unknown_not_unreachable(result: ProbeResult) -> None:
    """Invariant 2, OBS-003: with collector evidence, no replies is UNKNOWN.

    Args:
        result: A cycle with a collector fault.

    Returns:
        None

    Raises:
        None
    """
    cycle = build_cycle(result, context())
    assert cycle.summary.state is IcmpState.UNKNOWN
    assert Quality.COLLECTOR_DEGRADED in cycle.summary.quality


def test_probes_the_collector_could_not_send_are_not_loss() -> None:
    """OBS-003: two unsent probes shrink the group instead of adding 40% loss.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    cycle = build_cycle(make_result([1000, -1, 1100, "n", "n"]), context())
    obs = cycle.observation
    assert obs is not None
    assert obs.rtts_us == (1000, None, 1100)
    assert (cycle.summary.sent, cycle.summary.loss_percent) == (3, 33.3)
    assert obs.state is IcmpState.REACHABLE
    assert obs.quality == Quality.PARTIAL_LOSS | Quality.COLLECTOR_DEGRADED


@pytest.mark.parametrize(
    ("strategy", "plan"), [(GATED, SnmpPlan.SKIP), (FALLBACK, SnmpPlan.STAGE1_FALLBACK)]
)
def test_nothing_sent_means_not_attempted_and_no_observation(
    strategy: ReachabilityStrategy, plan: SnmpPlan
) -> None:
    """A cycle with no probe sent is not attempted: no ICMP row, UNKNOWN state.

    Args:
        strategy: Reachability strategy.
        plan: Expected SNMP plan.

    Returns:
        None

    Raises:
        None
    """
    cycle = build_cycle(make_result(["n"] * 5), context(strategy))
    assert cycle.observation is None
    assert cycle.summary.state is IcmpState.UNKNOWN
    assert cycle.summary.quality == Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED
    assert cycle.snmp is plan


def test_scheduler_reported_degradation_also_blocks_unreachable() -> None:
    """E.1: 0/5 from an unhealthy collector is UNKNOWN.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    cycle = build_cycle(make_result([-1] * 5), context(collector=CollectorState.OVERLOADED))
    assert cycle.summary.state is IcmpState.UNKNOWN


@pytest.mark.parametrize(
    ("fault", "reported", "expected"),
    [
        (None, CollectorState.HEALTHY, CollectorState.HEALTHY),
        (CollectorFault.OVERLOADED, CollectorState.HEALTHY, CollectorState.OVERLOADED),
        (CollectorFault.RECEIVE_OVERFLOW, CollectorState.HEALTHY, CollectorState.OVERLOADED),
        (CollectorFault.PERMISSION_DENIED, CollectorState.HEALTHY, CollectorState.DEGRADED),
        (CollectorFault.SOCKET_ERROR, CollectorState.UNAVAILABLE, CollectorState.UNAVAILABLE),
    ],
)
def test_collector_state_combines_scheduler_view_and_cycle_evidence(
    fault: CollectorFault | None, reported: CollectorState, expected: CollectorState
) -> None:
    """OBS-002: capacity faults read as overload, others as degradation.

    Args:
        fault: Cycle fault.
        reported: Scheduler's view.
        expected: Combined state.

    Returns:
        None

    Raises:
        None
    """
    assert collector_state(make_result([1000], fault=fault), reported) is expected


def test_icmp_t06_ipv6_records_family_and_source() -> None:
    """ICMP-T06, ICMP-011: IPv6 uses the same math and records family and source.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result = make_result([1000, 1200, 1100, 1300, 1050], "2001:db8::1", "2001:db8::10")
    obs = build_cycle(result, context()).observation
    assert obs is not None
    assert (obs.address_family, obs.target_address, obs.source_address) == (
        AddressFamily.IPV6,
        "2001:db8::1",
        "2001:db8::10",
    )
    assert (obs.avg_rtt_us, obs.jitter_us) == (1130, 108)


def test_latency_threshold_and_strategies() -> None:
    """E.1 ``impaired_rtt_ms`` and ICMP-013/014 plans come from ``vakr.logic``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    slow = build_cycle(make_result([150_000] * 5), context(), IcmpPolicy(impaired_rtt_ms=100))
    assert slow.summary.impairments == (Impairment.LATENCY,)
    assert build_cycle(make_result([900] * 5), context(ONLY)).snmp is SnmpPlan.SKIP
    single = make_result([900, -1, -1, -1, -1])
    assert build_cycle(single, context(FALLBACK)).snmp is SnmpPlan.STAGE1_FALLBACK


class FakeProber:
    """Returns a canned result and records requests."""

    def __init__(self, result: ProbeResult) -> None:
        """Store the result.

        Args:
            result: What ``probe`` returns.

        Returns:
            None

        Raises:
            None
        """
        self.result, self.requests = result, []

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Record the request and return the canned result.

        Args:
            request: The cycle.

        Returns:
            ProbeResult: The canned result.

        Raises:
            None
        """
        self.requests.append(request)
        return self.result

    def counters(self) -> IcmpCounters:
        """Return zero counters.

        Args:
            None

        Returns:
            IcmpCounters: Zeros.

        Raises:
            None
        """
        return IcmpCounters()


def test_collector_builds_the_request_from_settings_and_enforces_the_gate() -> None:
    """E.17: ``icmp_gated`` with fewer than 2 probes is refused before anything is sent.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    prober = FakeProber(make_result([700, 800, 900]))
    collector = IcmpCollector(prober, IcmpSettings(probes=3, spacing_s=0.1, timeout_s=0.5))
    cycle = asyncio.run(collector.collect("192.0.2.1", context(), "192.0.2.10"))
    assert cycle.summary.received == 3
    assert prober.requests == [ProbeRequest("192.0.2.1", 3, 0.1, 0.5, "192.0.2.10")]
    collector.settings = IcmpSettings(probes=1)
    with pytest.raises(ValueError, match="would never run SNMP"):
        asyncio.run(collector.collect("192.0.2.1", context()))
    assert asyncio.run(collector.collect("192.0.2.1", context(ONLY))).summary.sent == 3
