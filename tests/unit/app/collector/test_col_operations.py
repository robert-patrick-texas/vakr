"""Manual rediscovery inside the collector: pickup, claim, run, result (DISC-T03, API-T06).

A request stored by ``OperationService`` is picked up by
``OperationWatcher``, claimed for this collector, and run by
``ManualDiscoveryRunner`` against the in-process SNMP agent through the
real ``InventoryService`` on a memory store; the operation ends
``succeeded`` with the counts the write produced, and the device's next
scheduled rediscovery is a full interval away (DISC-002). Restart
recovery, devices this collector does not discover, a manual request that
waits for a running discovery, and a device that leaves inventory are
covered without SNMP.

Dependencies:
    tests.simulators.snmp.simlab: The in-process agent.
    vakr.app.collector, vakr.operations, vakr.inventory: The parts under test.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_operations.py
"""

import asyncio
from contextlib import AbstractAsyncContextManager
from datetime import timedelta

import pytest

from tests.contract import contract_samples as s
from tests.contract.contract_metadata_operations import operation
from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, lab
from tests.unit.app.collector.col_fakes import make_device, make_target
from vakr.adapters.memory import memory_uow_factory
from vakr.app.collector import discovery as discovery_module
from vakr.app.collector.discovery_attempt import GONE, OK, Attempt
from vakr.app.collector.discovery_manual import ManualDiscoveryRunner
from vakr.app.collector.inventory import EmptyInventory
from vakr.app.collector.operations import OperationWatcher
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.timings import ServiceTimings
from vakr.collectors.snmp.config import SnmpConfig
from vakr.domain.enums import ReachabilityStrategy
from vakr.domain.ids import OperationId
from vakr.domain.operations import Operation, OperationState
from vakr.inventory.service import InventoryService
from vakr.operations import OperationService, limits
from vakr.policy.rules import Policy
from vakr.ports.clock import SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.snmp import SnmpSession, SnmpSettings, SnmpTarget

DAY = 86_400
QUIET = ServiceTimings(discovery_concurrency=1, discovery_spread_s=3600)
"""First discoveries are an hour away, so only a manual request starts one."""


async def until(factory: MetadataUnitOfWorkFactory, op_id: OperationId, state: OperationState
                ) -> Operation:  # fmt: skip
    """Wait up to 10 s for an operation to reach a state.

    Args:
        factory: Opens units of work.
        op_id: The operation.
        state: The state.

    Returns:
        Operation: The operation in that state.

    Raises:
        AssertionError: If it does not get there in time.
    """
    for _ in range(200):
        with factory() as uow:
            op = uow.operations.get(op_id)
        if op is not None and op.state is state:
            return op
        await asyncio.sleep(0.05)
    raise AssertionError(f"operation never became {state.value}: {op}")


def test_disc_t03_a_queued_request_is_claimed_run_and_recorded() -> None:
    """DISC-T03, DISC-009, DISC-002: picked up within a pass, recorded, interval restarted.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Rediscover one simulated router twice through the request service.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spec = router(1)
        async with lab([spec]) as sim:
            uow, clock = memory_uow_factory(), SystemClock()
            east = s.collector()
            device = make_device(1, poll_address="127.0.0.1", credential_ref=spec.name)
            with uow() as tx:
                tx.collectors.add(east)
                tx.devices.add(device)
                tx.commit()
            inventory = InventoryService(uow, east.id, Policy(), clock)
            runner = ManualDiscoveryRunner(inventory, sim.factory, PlanBook(), clock, QUIET, "lab")
            runner.config = SnmpConfig(sim.port, FAST, timedelta(days=1), timedelta(days=7))
            runner.replan([make_target(device)])
            stop = asyncio.Event()
            watcher = OperationWatcher(runner, uow, east.id, clock)
            tasks = [asyncio.create_task(runner.run()),
                     asyncio.create_task(watcher.watch(stop, 0.05))]  # fmt: skip
            requests = OperationService(uow, clock)
            first = requests.request_rediscovery(device.id, actor="alice").operation
            done = await until(uow, first.id, OperationState.SUCCEEDED)
            again = requests.request_rediscovery(device.id, actor="alice").operation
            repeat = await until(uow, again.id, OperationState.SUCCEEDED)
            stop.set()
            await runner.stop()
            await asyncio.gather(*tasks)
            assert done.collector_id == east.id
            assert done.result is not None
            assert repeat.result is not None
            assert done.result.interfaces == repeat.result.interfaces > 0
            assert done.result.new == done.result.interfaces  # the first walk names them all
            assert (repeat.result.new, repeat.result.missing) == (0, 0)  # the same identities
            left = runner.due_in(device.id)
            assert left is not None
            assert DAY - 60 < left <= DAY
            assert watcher.started == 2

    asyncio.run(scenario())


def test_restart_recovery_and_devices_this_collector_does_not_discover() -> None:
    """E.32: running-at-stop becomes failed; an undiscovered device fails once planned.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Start a watcher over a left-over and an icmp_only request.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        uow, clock = memory_uow_factory(), SystemClock()
        east = s.collector()
        left, quiet = make_device(1), make_device(2, reachability=ReachabilityStrategy.ICMP_ONLY)
        stuck = operation(left.id, requested_at=clock.now_utc()).claimed(east.id, clock.now_utc())
        pending = operation(quiet.id, requested_at=clock.now_utc())
        with uow() as tx:
            tx.collectors.add(east)
            for record in (left, quiet):
                tx.devices.add(record)
            tx.operations.add(stuck)
            tx.operations.add(pending)
            tx.commit()
        runner = ManualDiscoveryRunner(EmptyInventory(), _NoSnmp(), PlanBook(), clock, QUIET, "lab")
        watcher = OperationWatcher(runner, uow, east.id, clock)
        stop = asyncio.Event()
        task = asyncio.create_task(watcher.watch(stop, 0.05))
        failed = await until(uow, stuck.id, OperationState.FAILED)
        assert failed.error == limits.RESTART_REASON
        await asyncio.sleep(0.2)  # not planned yet: the icmp_only request waits
        with uow() as tx:
            waiting = tx.operations.get(pending.id)
        assert waiting is not None
        assert waiting.state is OperationState.QUEUED
        runner.replan([make_target(quiet, ReachabilityStrategy.ICMP_ONLY)])
        gave_up = await until(uow, pending.id, OperationState.FAILED)
        assert gave_up.error == limits.NOT_HERE_REASON
        stop.set()
        await task

    asyncio.run(scenario())


def test_a_manual_request_waits_for_the_running_discovery_then_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DISC-003: the hook gets the run that started after the request, not the one before.

    Args:
        monkeypatch: Replaces the SNMP walk with a gated fake.

    Returns:
        None

    Raises:
        None
    """
    gates: list[asyncio.Event] = []

    async def walk(*_args: object) -> Attempt:
        """Wait for the test to release this walk.

        Args:
            *_args: As ``attempt``.

        Returns:
            Attempt: ``ok`` without a report.

        Raises:
            None
        """
        gates.append(gate := asyncio.Event())
        await gate.wait()
        return Attempt(OK, "", None, None, None)

    async def scenario() -> None:
        """Release one scheduled walk, with a manual request made during it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        monkeypatch.setattr(discovery_module, "attempt", walk)
        device, clock = make_device(1), SystemClock()
        eager = ServiceTimings(discovery_concurrency=1, discovery_spread_s=0)
        runner = ManualDiscoveryRunner(EmptyInventory(), _NoSnmp(), PlanBook(), clock, eager, "lab")
        runner.replan([make_target(device)])
        task = asyncio.create_task(runner.run())
        while not gates:
            await asyncio.sleep(0.01)
        seen: list[Attempt] = []

        async def hook(done: Attempt) -> None:
            """Record the outcome.

            Args:
                done: The outcome.

            Returns:
                None

            Raises:
                None
            """
            seen.append(done)

        assert runner.free_slots() == 0
        assert runner.run_now(device.id, hook)
        assert not runner.run_now(device.id, hook)  # one waiting per device
        gates[0].set()
        while len(gates) < 2:
            await asyncio.sleep(0.01)
        assert seen == []  # the scheduled walk was not the manual one
        gates[1].set()
        while not seen:
            await asyncio.sleep(0.01)
        assert seen[0].status == OK
        assert runner.run_now(device.id, hook)
        runner.replan([])  # the device left inventory before its turn
        while len(seen) < 2:
            await asyncio.sleep(0.01)
        assert seen[1].status == GONE
        await runner.stop()
        await task

    asyncio.run(scenario())


class _NoSnmp:
    """A session factory that refuses: no test here walks an agent through it."""

    def open(
        self,
        target: SnmpTarget,
        settings: SnmpSettings,  # noqa: ARG002 - contract
    ) -> AbstractAsyncContextManager[SnmpSession]:
        """Refuse.

        Args:
            target: The agent.
            settings: The budget.

        Returns:
            AbstractAsyncContextManager[SnmpSession]: Never.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(f"no SNMP session expected for {target.address}")
