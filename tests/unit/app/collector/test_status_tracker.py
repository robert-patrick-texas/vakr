"""Stage 1 status records and lifecycle following each poll (E.6, E.35, LIFE-001, LIFE-006).

The tracker writes a baseline per interface at its first Stage 1, then a
record only when the status pair changes; it keys by persistent UUID, never
``ifIndex``. The lifecycle it queues reaches the inventory in batches from a
worker thread, and an interface that goes down and comes back is inactive
and then active again in the inventory and the status views without waiting
for rediscovery.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.app.collector, vakr.inventory, vakr.query.

Example:
    $ uv run pytest tests/unit/app/collector/test_status_tracker.py
"""

import asyncio
from datetime import timedelta

from tests.contract import contract_samples as s
from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
from vakr.app.collector.status_tracker import StatusTracker, lifecycle_loop
from vakr.collectors.snmp.plan import InterfacePlan, PollPlan
from vakr.collectors.snmp.stage1 import InterfaceStatus, Stage1Result
from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, PollId, new_id
from vakr.domain.quality import Quality
from vakr.inventory.stage1_lifecycle import StatusEvidence, apply_stage1_status
from vakr.ports.snmp.results import SnmpStats
from vakr.query import StatusQueries

UP, DOWN = IfOperStatus.UP, IfOperStatus.DOWN
A, B = new_id(InterfaceId), new_id(InterfaceId)
PLAN = PollPlan((InterfacePlan(1, A), InterfacePlan(2, B), InterfacePlan(3, None)))
IDS = (new_id(PollId), new_id(CollectorId), new_id(DeviceId))


def stage1(minute: int, a: IfOperStatus, b: IfOperStatus, *, answered: bool = True):
    """Build a Stage 1 result for interfaces 1, 2, and the unidentified 3.

    Args:
        minute: Minutes after T0.
        a: Interface 1's oper status.
        b: Interface 2's oper status.
        answered: Whether the agent answered.

    Returns:
        Stage1Result: The result.

    Raises:
        None
    """
    up = IfAdminStatus.UP
    statuses = (InterfaceStatus(1, up, a), InterfaceStatus(2, up, b), InterfaceStatus(3, up, UP))
    stats = SnmpStats(requests=1, responses=1 if answered else 0, timeouts=0, retries=0)
    return Stage1Result(s.T0 + minute * s.MINUTE, 0.0, 100, statuses, stats)


def test_e35_baseline_then_only_changes_keyed_by_identity() -> None:
    """E.35: first Stage 1 records both identified interfaces; then only the change.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tracker = StatusTracker()
    first = tracker.observe(IDS, PLAN, stage1(0, UP, UP))
    assert [(r.interface_id, r.baseline) for r in first] == [(A, True), (B, True)]
    assert tracker.observe(IDS, PLAN, stage1(1, UP, UP)) == ()
    (change,) = tracker.observe(IDS, PLAN, stage1(2, DOWN, UP), Quality.MAINTENANCE)
    assert (change.interface_id, change.oper_status, change.baseline) == (A, DOWN, False)
    assert change.quality == Quality.MAINTENANCE
    assert tracker.observe(IDS, PLAN, stage1(3, UP, UP, answered=False)) == ()
    moved = PollPlan((InterfacePlan(2, A), InterfacePlan(1, B)))  # ifIndex swapped
    # ifIndex 1 now answers DOWN and 2 UP: B went down and A came up, whatever their indexes.
    renumbered = tracker.observe(IDS, moved, stage1(4, DOWN, UP))
    assert [(r.interface_id, r.if_index, r.oper_status) for r in renumbered] == [
        (A, 2, UP), (B, 1, DOWN)]  # fmt: skip


def test_e6_lifecycle_follows_stage1_without_rediscovery() -> None:
    """E.6, LIFE-001: down at Stage 1 is inactive at once; up again is active; views agree.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    uow = memory_uow_factory()
    device = s.device("core1")
    iface = s.interface(device.id, "Gi1/0/1")
    with uow() as u:
        u.devices.add(device)
        u.interfaces.add(iface)
        u.commit()
    stale = timedelta(days=10)
    down = StatusEvidence(iface.id, s.T0 + s.MINUTE, IfAdminStatus.UP, DOWN)
    assert apply_stage1_status(uow, [down], stale) == 1
    queries = StatusQueries(uow, memory_telemetry_factory())
    (row,) = queries.device_detail(str(device.id)).interfaces
    assert row.lifecycle is InterfaceLifecycle.INACTIVE
    assert row.state.text == "not up/up (inactive)"
    assert apply_stage1_status(uow, [down], stale) == 0  # nothing changes twice
    old = StatusEvidence(iface.id, s.T0, IfAdminStatus.UP, UP)
    assert apply_stage1_status(uow, [old], stale) == 0  # older than the record: ignored
    up = StatusEvidence(iface.id, s.T0 + 2 * s.MINUTE, IfAdminStatus.UP, UP)
    assert apply_stage1_status(uow, [up], stale) == 1
    with uow() as u:
        assert u.interfaces.get(iface.id).lifecycle is InterfaceLifecycle.ACTIVE


def test_e6_the_flush_loop_hands_batches_to_the_inventory_and_retries() -> None:
    """E.6, E.35 bounds: batches go to the inventory in a worker thread; a failure is retried.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tracker = StatusTracker()
    tracker.observe(IDS, PLAN, stage1(0, UP, DOWN))
    calls: list[int] = []

    class Inventory:
        """A fake inventory that fails once."""

        def record_stage1_status(self, items) -> int:
            """Take a batch, failing the first time.

            Args:
                items: The batch.

            Returns:
                int: Items taken.

            Raises:
                RuntimeError: On the first call.
            """
            calls.append(len(items))
            if len(calls) == 1:
                raise RuntimeError("metadata busy")
            return len(items)

    async def scenario() -> None:
        """Run the loop until both batches were offered.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        stop = asyncio.Event()
        task = asyncio.create_task(lifecycle_loop(stop, tracker, Inventory(), interval_s=0.01))
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(scenario())
    assert calls[:2] == [2, 2]
    assert tracker.pending == 0
