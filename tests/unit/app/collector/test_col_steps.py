"""The collector's ICMP and SNMP steps against a scripted prober and the SNMP simulator.

Covers E.25 #5 (a collector-side ICMP failure lets SNMP run), ICMP-007 (the
skip is recorded), SNMP-006 and ADR-005 (Stage 2 only for up/up interfaces
with a persistent identity), E.23 (a session that cannot open is
``UNKNOWN``, a silent agent is ``TIMEOUT``), and ICMP-013 (the fallback is
one low-cost request).

Dependencies:
    tests.simulators.snmp.simlab: The in-process agent and real adapter.
    tests.unit.app.collector.col_fakes: Devices and the scripted prober.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_steps.py
"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, Lab, lab
from tests.unit.app.collector.col_fakes import (
    ScriptedProber,
    iface,
    make_device,
    make_target,
    poll_ctx,
)
from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.icmp_step import (
    CollectorIcmpStep,
    IcmpNotSentError,
    IcmpResolutionError,
    resolve_address,
)
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.snmp_step import CollectorSnmpStep
from vakr.app.collector.tally import FaultTally
from vakr.collectors.icmp.cycle import IcmpCollector
from vakr.collectors.icmp.settings import IcmpSettings
from vakr.collectors.snmp import PollPlan, plan_from_discovery
from vakr.collectors.snmp.config import SnmpConfig
from vakr.discovery import discover
from vakr.domain.enums import AddressFamily, BaselineState, SnmpState
from vakr.domain.observations import IcmpObservation, InterfaceObservation, SnmpDeviceObservation
from vakr.domain.quality import Quality
from vakr.domain.status_observations import InterfaceStatusObservation
from vakr.ports.clock import SystemClock
from vakr.scheduler.machine import Phase
from vakr.scheduler.runner import PollProgress, run_poll
from vakr.scheduler.steps import PollContext, PollSteps, SnmpResult, StepStatus

UP_UP = {1, 2, 5, 6, 8, 9, 10, 11, 12, 13}  # router layout in tests/simulators/snmp/ports.py
NAME = "rtr-sim-001"


class RecordingSnmp:
    """An ``SnmpStep`` that records Stage 1 calls and answers OK."""

    def __init__(self) -> None:
        """Start with no calls.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.calls: list[bool] = []

    async def stage1(self, ctx: PollContext, fallback: bool) -> SnmpResult:
        """Record the call.

        Args:
            ctx: The poll.
            fallback: Whether it is the fallback attempt.

        Returns:
            SnmpResult: OK with no up/up interfaces.

        Raises:
            None
        """
        del ctx
        self.calls.append(fallback)
        return SnmpResult(StepStatus.OK)

    async def stage2(self, ctx: PollContext, stage1: SnmpResult) -> SnmpResult:
        """Never reached: Stage 1 reports nothing up/up.

        Args:
            ctx: The poll.
            stage1: Stage 1's result.

        Returns:
            SnmpResult: OK.

        Raises:
            None
        """
        del ctx, stage1
        return SnmpResult(StepStatus.OK)


def icmp_step(*script: list[int | str]) -> tuple[CollectorIcmpStep, FaultTally]:
    """Build an ICMP step over a scripted prober with two probes per cycle.

    Args:
        script: Probe specs per cycle.

    Returns:
        tuple[CollectorIcmpStep, FaultTally]: The step and its tally.

    Raises:
        None
    """
    tally = FaultTally()
    collector = IcmpCollector(ScriptedProber(*script), IcmpSettings(probes=2))
    return CollectorIcmpStep(collector, tally), tally


def now_us() -> int:
    """Return monotonic microseconds for the runner.

    Args:
        None

    Returns:
        int: The time.

    Raises:
        None
    """
    return round(SystemClock().monotonic() * 1e6)


def test_nothing_sent_is_collector_trouble_and_snmp_still_runs() -> None:
    """E.25 #5, invariant 2: no reply count reaches the gate, so icmp_gated runs Stage 1.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    step, tally = icmp_step(["n", "n"])
    snmp = RecordingSnmp()
    progress = PollProgress()
    target = make_target(make_device(1))
    asyncio.run(run_poll(poll_ctx(target), PollSteps(step, snmp), progress, now_us))
    assert snmp.calls == [False]
    icmp = progress.steps[0]
    assert icmp.phase is Phase.ICMP
    assert icmp.status is StepStatus.ERROR
    assert icmp.error == IcmpNotSentError.__name__
    assert progress.quality & Quality.COLLECTOR_DEGRADED
    assert not [o for o in progress.observations if isinstance(o, IcmpObservation)]
    assert tally.lifetime["icmp_not_sent"] == 1


def test_one_reply_under_icmp_gated_records_snmp_skipped() -> None:
    """ICMP-007, 9.2: 1/2 replies keeps the ICMP result and records SNMP as SKIPPED.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    step, _ = icmp_step([900, -1])
    result = asyncio.run(step.icmp(poll_ctx(make_target(make_device(1)))))
    assert (result.replies, result.sent) == (1, 2)
    kinds = [type(o) for o in result.observations]
    assert kinds == [IcmpObservation, SnmpDeviceObservation]
    skipped = result.observations[1]
    assert isinstance(skipped, SnmpDeviceObservation)
    assert skipped.state is SnmpState.SKIPPED
    two, _ = icmp_step([900, 1100])
    answered = asyncio.run(two.icmp(poll_ctx(make_target(make_device(1)))))
    assert [type(o) for o in answered.observations] == [IcmpObservation]


def test_names_resolve_with_a_bound() -> None:
    """9.11: a DNS name is resolved with a bounded lookup; a slow one is collector trouble.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    named = make_device(2, poll_address="localhost", address_family=AddressFamily.IPV4)
    assert asyncio.run(resolve_address(named)) == "127.0.0.1"
    assert asyncio.run(resolve_address(make_device(3))) == "192.0.2.3"

    async def slow() -> None:
        """Resolve against a lookup that never finishes in time.

        Args:
            None

        Returns:
            None

        Raises:
            IcmpResolutionError: Always.
        """
        loop = asyncio.get_running_loop()

        async def stall(*_args: object, **_kwargs: object) -> list[object]:
            """Take longer than the bound.

            Args:
                _args: Ignored.
                _kwargs: Ignored.

            Returns:
                list[object]: Never returns in time.

            Raises:
                None
            """
            await asyncio.sleep(5)
            return []

        loop.getaddrinfo = stall  # ty: ignore[invalid-assignment]
        await resolve_address(named, timeout_s=0.01)

    with pytest.raises(IcmpResolutionError):
        asyncio.run(slow())


async def _plan(sim: Lab, unresolved: frozenset[int] = frozenset()) -> PollPlan:
    """Discover the simulated router and give its interfaces UUIDs.

    Args:
        sim: The lab.
        unresolved: ifIndexes left without a UUID.

    Returns:
        PollPlan: The plan.

    Raises:
        None
    """
    async with sim.factory.open(sim.target(NAME), FAST) as session:
        found = await discover(session, SystemClock())
    entries = tuple(
        e if e.if_index in unresolved else replace(e, interface_id=iface(e.if_index))
        for e in plan_from_discovery(found).interfaces
    )
    return PollPlan(entries)


def snmp_step(sim: Lab, plans: PlanBook, tally: FaultTally) -> CollectorSnmpStep:
    """Build the SNMP step against the lab.

    Args:
        sim: The lab.
        plans: Working plans.
        tally: The fault tally.

    Returns:
        CollectorSnmpStep: The step.

    Raises:
        None
    """
    config = SnmpConfig(sim.port, FAST, timedelta(days=1), timedelta(days=7))
    return CollectorSnmpStep(sim.factory, config, plans, BaselineStore(), SystemClock(), tally, "b")


def test_stage1_then_stage2_sample_up_up_interfaces_with_identities() -> None:
    """SNMP-006, ADR-005, BASELINE_IDENTITY_UNRESOLVED: only resolved up/up interfaces are sampled.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Poll the simulated router twice.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        async with lab([router(1)]) as sim:
            device = make_device(1, poll_address="127.0.0.1", credential_ref=NAME)
            plans, tally = PlanBook(), FaultTally()
            plans.adopt(device.id, await _plan(sim, unresolved=frozenset({2})), device.created_at)
            step, ctx = snmp_step(sim, plans, tally), poll_ctx(make_target(device))
            first = await step.stage1(ctx, fallback=False)
            assert first.status is StepStatus.OK
            assert first.up_up == len(UP_UP) - 1
            assert first.quality & Quality.BASELINE_IDENTITY_UNRESOLVED
            assert tally.unresolved_interfaces[device.id] == 1
            *statuses, obs = first.observations  # E.35: status records come first
            assert isinstance(obs, SnmpDeviceObservation)
            assert statuses
            assert all(isinstance(r, InterfaceStatusObservation) and r.baseline for r in statuses)
            assert obs.state is SnmpState.RESPONDING
            second = await step.stage2(ctx, first)
            rows = [o for o in second.observations if isinstance(o, InterfaceObservation)]
            assert {o.if_index for o in rows} == UP_UP - {2}
            assert all(o.baseline_state is BaselineState.NO_BASELINE for o in rows)
            with pytest.raises(TypeError):
                await step.stage2(ctx, SnmpResult(StepStatus.OK, up_up=1))

    asyncio.run(scenario())


def test_collector_and_target_failures_are_told_apart() -> None:
    """E.23, invariant 2: no credential is UNKNOWN+COLLECTOR_DEGRADED; silence is TIMEOUT.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Poll without a credential, then a silent agent, then the fallback.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        async with lab([router(1)]) as sim:
            tally, plans = FaultTally(), PlanBook()
            step = snmp_step(sim, plans, tally)
            bare = make_device(1, poll_address="127.0.0.1", credential_ref=None)
            unknown = await step.stage1(poll_ctx(make_target(bare)), fallback=False)
            obs = unknown.observations[0]
            assert isinstance(obs, SnmpDeviceObservation)
            assert obs.state is SnmpState.UNKNOWN
            assert obs.quality & Quality.COLLECTOR_DEGRADED
            assert tally.lifetime["snmp_session"] == 1
            device = make_device(1, poll_address="127.0.0.1", credential_ref=NAME)
            sim.devices[NAME].seen.clear()
            fallback = await step.stage1(poll_ctx(make_target(device)), fallback=True)
            assert fallback.up_up == 0
            assert [len(oids) for _, oids in sim.devices[NAME].seen] == [1]  # sysUpTime.0 only
            sim.devices[NAME].faults.drop_all = True
            silent = await step.stage1(poll_ctx(make_target(device)), fallback=False)
            obs = silent.observations[0]
            assert isinstance(obs, SnmpDeviceObservation)
            assert silent.status is StepStatus.NO_RESPONSE
            assert obs.state is SnmpState.TIMEOUT
            assert not obs.quality & Quality.COLLECTOR_DEGRADED

    asyncio.run(scenario())


def test_a_device_without_a_plan_still_gets_icmp_and_uptime() -> None:
    """DISC-001, E.27: before (or without) a poll plan, ICMP runs and Stage 1 reads sysUpTime.0.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> PollProgress:
        """Poll the simulated router with no plan at all.

        Args:
            None

        Returns:
            PollProgress: What the poll produced.

        Raises:
            None
        """
        async with lab([router(1)]) as sim:
            device = make_device(1, poll_address="127.0.0.1", credential_ref=NAME)
            icmp, tally = icmp_step([900, 1000])
            steps = PollSteps(icmp, snmp_step(sim, PlanBook(), tally))
            progress = PollProgress()
            await run_poll(poll_ctx(make_target(device)), steps, progress, now_us)
            return progress

    progress = asyncio.run(scenario())
    kinds = [type(o) for o in progress.observations]
    assert kinds == [IcmpObservation, SnmpDeviceObservation]
    snmp = progress.observations[1]
    assert isinstance(snmp, SnmpDeviceObservation)
    assert snmp.state is SnmpState.RESPONDING
    assert snmp.sys_uptime_ticks is not None
    assert [s.phase for s in progress.steps][-1] is Phase.STAGE2  # recorded as skipped
    assert progress.steps[-1].status is StepStatus.SKIPPED
