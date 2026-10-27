"""M6 review findings on Stage 1 lifecycle and status records (E.6, ADR-024, invariant 1).

1. A peer never writes interface lifecycle into its replicated metadata
   (ADR-024), yet still records its own status observations; ``PeerInventory``
   exposes an allow-list, so a new write method cannot slip through.
2. An annotation or tag edit between Stage 1 and the flush does not lose the
   lifecycle change: evidence is no longer compared with the record's general
   ``updated_at`` (E.6).
3. The interface table says "not up/up" only when a Stage 1 status record says
   so; STALE, RETIRED, and rows without a sample keep their own wording.

Dependencies:
    pytest; tests.contract.contract_samples; tests.unit.query.acceptance_testkit.

Example:
    $ uv run pytest tests/unit/app/collector/test_lifecycle_review.py
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.contract import contract_samples as s
from tests.unit.query.acceptance_testkit import OK, T0, Fleet, SlotPlan
from vakr.app.collector.replication_loop import PeerInventory
from vakr.app.collector.status_tracker import StatusTracker, lifecycle_loop
from vakr.collectors.snmp.plan import InterfacePlan, PollPlan
from vakr.collectors.snmp.stage1 import InterfaceStatus, Stage1Result
from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, PollId, new_id
from vakr.inventory.stage1_lifecycle import StatusEvidence, apply_stage1_status
from vakr.ports.clock import ManualClock
from vakr.ports.snmp.results import SnmpStats
from vakr.query import StatusQueries
from vakr.query.interface_rows import interface_row

STALE = timedelta(days=10)


def test_adr024_a_peer_records_status_but_never_writes_lifecycle() -> None:
    """ADR-024, E.6: a peer's Stage 1 still yields status records; the inventory gets nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    iface = new_id(InterfaceId)
    inner = SimpleNamespace(
        devices=lambda: (), change_counter=lambda: 1, poll_plan=lambda _d: None,
        replace_policy=lambda *_a: None, record_discovery_report=lambda *_a: pytest.fail("wrote"),
        record_stage1_status=lambda _items: pytest.fail("a peer wrote lifecycle"),
        record_something_new=lambda: pytest.fail("a new write method slipped through"),
    )  # fmt: skip
    peer = PeerInventory(inner)
    for hidden in ("record_stage1_status", "record_discovery_report", "record_something_new"):
        assert not hasattr(peer, hidden)
    assert (peer.devices(), peer.change_counter(), peer.poll_plan(new_id(DeviceId))) == (
        (),
        1,
        None,
    )
    tracker = StatusTracker()
    up = IfAdminStatus.UP
    stage1 = Stage1Result(
        T0,
        0.0,
        100,
        (InterfaceStatus(1, up, IfOperStatus.DOWN),),
        SnmpStats(requests=1, responses=1, timeouts=0, retries=0),
    )
    ids = (new_id(PollId), new_id(CollectorId), new_id(DeviceId))
    (record,) = tracker.observe(ids, PollPlan((InterfacePlan(1, iface),)), stage1)
    assert record.interface_id == iface  # the telemetry record is kept

    async def scenario() -> None:
        """Run the flush loop once against the peer.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        stop = asyncio.Event()
        task = asyncio.create_task(lifecycle_loop(stop, tracker, peer, interval_s=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(scenario())
    assert tracker.pending == 0  # dropped, never queued without bound


def test_e6_an_annotation_edit_before_the_flush_keeps_the_lifecycle_change() -> None:
    """E.6: Stage 1 found the link down; an admin tags it before the flush; it still goes inactive.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    device = f.device("core1")
    iface = f.interface(device, "Gi1")
    down = StatusEvidence(iface.id, T0 + timedelta(minutes=1), IfAdminStatus.UP, IfOperStatus.DOWN)
    with f.uow() as uow:
        stored = uow.interfaces.get(iface.id)
        assert stored is not None
        uow.interfaces.update(replace(stored, comment="uplink to dc2",
                                      updated_at=T0 + timedelta(minutes=1, seconds=2)),
                              writer=stored.managed_by)  # fmt: skip
        uow.commit()
    assert apply_stage1_status(f.uow, [down], STALE) == 1
    with f.uow() as uow:
        stored = uow.interfaces.get(iface.id)
    assert stored is not None
    assert stored.lifecycle is InterfaceLifecycle.INACTIVE


def test_e6_stage1_evidence_never_revives_an_interface_discovery_lost() -> None:
    """E.6: a late Stage 1 batch does not undo discovery's NOT_DISCOVERED decision.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    device = f.device("core1")
    iface = s.interface(device.id, "Gi1", if_index=None, lifecycle=InterfaceLifecycle.INACTIVE)
    with f.uow() as uow:
        uow.interfaces.add(iface)
        uow.commit()
    up = StatusEvidence(iface.id, T0 + timedelta(minutes=1), IfAdminStatus.UP, IfOperStatus.UP)
    assert apply_stage1_status(f.uow, [up], STALE) == 0


def _detail(f: Fleet, now) -> dict[str, tuple[str, str | None]]:
    """Return each interface row's state text and reason from the device screen.

    Args:
        f: The fleet.
        now: View time.

    Returns:
        dict[str, tuple[str, str | None]]: Interface name to ``(text, reason)``.

    Raises:
        None
    """
    queries = StatusQueries(f.uow, f.telemetry, clock=ManualClock(now))
    detail = queries.device_detail("core1")
    assert detail is not None
    return {row.name: (row.state.text, row.state.reason) for row in detail.interfaces}


def test_invariant1_not_up_up_only_from_a_stage1_status_record() -> None:
    """Invariant 1: "not up/up" needs a status record; STALE, RETIRED, no sample keep their words.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    device = f.device("core1")
    later = T0 + timedelta(minutes=30)
    down = f.interface(device, "Gi1", lifecycle=InterfaceLifecycle.INACTIVE, updated_at=later)
    f.interface(device, "Gi2", lifecycle=InterfaceLifecycle.INACTIVE, updated_at=later)
    f.interface(device, "Gi3", lifecycle=InterfaceLifecycle.STALE, updated_at=later)
    f.interface(device, "Gi4", lifecycle=InterfaceLifecycle.RETIRED, updated_at=later)
    f.run(device, T0, 40, lambda k: OK if k < 20 else SlotPlan(sampled=frozenset({"Gi2"})))
    f.write([s.status(device.id, down.id, f.collector.id, T0 + timedelta(minutes=20, seconds=8),
                      baseline=False)])  # fmt: skip
    rows = _detail(f, T0 + timedelta(minutes=40, seconds=30))
    assert rows["Gi1"][0] == "not up/up (up/down)"
    assert not rows["Gi2"][0].startswith("not up/up")  # sampled now: its sample speaks
    assert not rows["Gi3"][0].startswith("not up/up")
    assert "Gi4" not in rows  # retired interfaces leave the device table
    retired = interface_row(s.interface(device.id, "Gi4", lifecycle=InterfaceLifecycle.RETIRED),
                            None, "east")  # fmt: skip
    assert (retired.state.text, retired.state.reason) == ("—", "no sample from east")
    bare = Fleet()
    lone = bare.device("core1")
    bare.interface(lone, "Gi9", lifecycle=InterfaceLifecycle.INACTIVE)
    bare.run(lone, T0, 5, lambda _k: SlotPlan(sampled=frozenset()))
    (text, reason) = _detail(bare, T0 + timedelta(minutes=5, seconds=30))["Gi9"]
    assert text == "—"
    assert reason is not None
    assert reason.startswith("no sample from")
