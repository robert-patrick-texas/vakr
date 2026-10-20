"""The real collector loop, end to end: simulator, ICMP to 127.0.0.1, SQLite, restart (M3 exit).

One scenario drives ``CollectorService`` as ``vakr-collector.service``
runs it: real ICMP ping sockets to loopback, the real PySNMP adapter
against the in-process IOS XE simulator, real SQLite metadata and
telemetry in ``tmp_path``, the durable spool, and ingestion. Only the
inventory is a fake (``LabInventory``) and the poll interval is the
test-only 2 s override (``ServiceTimings.poll_interval_override_s``). Every
assertion reads through the telemetry ports, as the views will:

* both devices are discovered (DISC-T01, DISC-001) and reachable over ICMP
  and SNMP (ICMP-T01, SNMP-T03);
* an interface's first sample has raw counters and no rates
  (``BASELINE_FIRST_SAMPLE``), and its second has rates (RATE-T02, RATE-T01);
* a device the simulator stops answering reads as an SNMP timeout with ICMP
  still reachable, never as Down and never as collector trouble (E.2 row 7,
  invariant 2, OBS-002);
* after a graceful restart the collector keeps its identity (SVC-T04,
  MPOL-006) and the first sample of each interface already has rates,
  resumed from the checkpoint (RATE-004, 6.2), and the shutdown wrote
  every record (SVC-T03).

The run takes about 20 s.

Dependencies:
    tests.simulators.snmp.simlab: The in-process agent and adapter factory.
    tests.integration.collector.collector_lab: Root, inventory, waits.

Example:
    $ uv run pytest tests/integration/collector
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.collector.collector_lab import (
    LabInventory,
    icmp_unavailable,
    lab_device,
    make_root,
    wait_until,
)
from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import Lab, lab
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.app.collector import inventory as inventory_seam
from vakr.app.collector.build import build_parts
from vakr.app.collector.service import CollectorService
from vakr.app.collector.timings import ServiceTimings
from vakr.domain.enums import (
    BaselineState,
    IcmpState,
    ObservationType,
    PollExecution,
    SnmpState,
    StatusColor,
)
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import InterfaceObservation
from vakr.domain.paging import PageRequest
from vakr.domain.polls import DevicePoll
from vakr.domain.quality import Quality
from vakr.domain.series import TimeWindow
from vakr.logic.status import StatusEvidence, device_status
from vakr.paths import InstallPaths

ICMP_SKIP = icmp_unavailable()
pytestmark = pytest.mark.skipif(ICMP_SKIP is not None, reason=ICMP_SKIP or "")

TIMINGS = ServiceTimings(
    checkpoint_interval_s=0.5, health_interval_s=1.0, change_poll_s=0.25,
    discovery_spread_s=0.0, discovery_retry_min_s=1.0, poll_interval_override_s=2,
)  # fmt: skip


class Run:
    """One collector process: the service and its task."""

    def __init__(self, service: CollectorService, task: asyncio.Task[int]) -> None:
        """Hold the running service.

        Args:
            service: The service.
            task: Its ``run`` task.

        Returns:
            None

        Raises:
            None
        """
        self.service, self.task = service, task

    async def stop(self) -> int:
        """Stop gracefully and return the exit status.

        Args:
            None

        Returns:
            int: ``run``'s result.

        Raises:
            None
        """
        self.service.request_stop()
        return await asyncio.wait_for(self.task, 30)


async def start(paths: InstallPaths, sim: Lab) -> Run:
    """Run the preflight and start the collector, as ``vakr collector run`` does.

    Args:
        paths: The relocated install.
        sim: The simulator lab, whose factory knows the generated communities.

    Returns:
        Run: The running collector.

    Raises:
        PreflightError: If a startup check fails.
    """
    parts = await build_parts(paths, snmp_factory=sim.factory)
    service = CollectorService(parts, TIMINGS)
    return Run(service, asyncio.create_task(service.run()))


def window() -> TimeWindow:
    """Return the last hour.

    Args:
        None

    Returns:
        TimeWindow: ``[now - 1 h, now + 1 min)``.

    Raises:
        None
    """
    now = datetime.now(UTC)
    return TimeWindow(now - timedelta(hours=1), now + timedelta(minutes=1))


def samples(
    reads: SqliteTelemetry, cid: CollectorId, iid: InterfaceId
) -> list[InterfaceObservation]:
    """Read one interface's stored samples, oldest first.

    Args:
        reads: The telemetry ports.
        cid: This collector.
        iid: The interface.

    Returns:
        list[InterfaceObservation]: The samples.

    Raises:
        None
    """
    page = reads.query.read_raw(
        ObservationType.INTERFACE, iid, window(), PageRequest(limit=200), collector_id=cid
    )
    return [o for o in page.items if isinstance(o, InterfaceObservation)]


def polls(reads: SqliteTelemetry, cid: CollectorId, did: DeviceId) -> list[DevicePoll]:
    """Read one device's poll records, oldest first.

    Args:
        reads: The telemetry ports.
        cid: This collector.
        did: The device.

    Returns:
        list[DevicePoll]: The records.

    Raises:
        None
    """
    page = reads.query.read_raw(
        ObservationType.DEVICE_POLL, did, window(), PageRequest(limit=200), collector_id=cid
    )
    return [o for o in page.items if isinstance(o, DevicePoll)]


async def first_life(paths: InstallPaths, sim: Lab, inventory: LabInventory) -> CollectorId:
    """Collect until rates exist, silence one device, and stop gracefully.

    Args:
        paths: The install.
        sim: The simulator.
        inventory: The lab inventory.

    Returns:
        CollectorId: The collector's identity.

    Raises:
        AssertionError: If any expectation fails.
    """
    run = await start(paths, sim)
    store = SqliteTelemetryStore(paths.state_dir / TELEMETRY_FILE)
    reads = SqliteTelemetry(store)
    cid = run.service.parts.collector.id
    r1, r2 = inventory.items

    async def rated() -> bool:
        """Report whether an interface of the first router has two samples.

        Args:
            None

        Returns:
            bool: True once one has.

        Raises:
            None
        """
        plan = inventory.plans.get(r1.id)
        if plan is None:
            return False
        ids = [e.interface_id for e in plan.interfaces if e.interface_id is not None]
        found = [await asyncio.to_thread(samples, reads, cid, i) for i in ids]
        return any(len(s) >= 2 for s in found)

    assert await wait_until(rated, 15), "no interface reached a second sample"
    assert len(inventory.recorded) == 2  # DISC-001: each device discovered once
    now = datetime.now(UTC)
    for device in (r1, r2):
        state = await asyncio.to_thread(reads.current.device_state, device.id, cid, now=now)
        assert state is not None
        assert state.icmp is not None
        assert state.snmp is not None
        assert state.icmp.state is IcmpState.REACHABLE
        assert state.snmp.state is SnmpState.RESPONDING
    _check_rates(reads, cid, inventory)
    sim.devices[r2.name].faults.drop_all = True

    async def silent() -> bool:
        """Report whether the silenced router reads as an SNMP timeout.

        Args:
            None

        Returns:
            bool: True once it does.

        Raises:
            None
        """
        state = await asyncio.to_thread(
            reads.current.device_state, r2.id, cid, now=datetime.now(UTC)
        )
        return (
            state is not None and state.snmp is not None and state.snmp.state is SnmpState.TIMEOUT
        )

    assert await wait_until(silent, 10), "the silenced device never read as an SNMP timeout"
    state = await asyncio.to_thread(reads.current.device_state, r2.id, cid, now=datetime.now(UTC))
    assert state is not None
    assert state.icmp is not None
    assert state.snmp is not None
    assert state.icmp.state is IcmpState.REACHABLE  # ICMP stays honest
    assert not state.snmp.quality & Quality.COLLECTOR_DEGRADED  # the device, not vakr
    verdict = device_status(StatusEvidence(state.icmp.state, state.snmp.state, state.observed_at),
                            datetime.now(UTC))  # fmt: skip
    assert verdict.color is not StatusColor.RED
    assert verdict.row == 7
    assert await run.stop() == 0  # SVC-T03
    store.close()
    return cid


def _check_rates(reads: SqliteTelemetry, cid: CollectorId, inventory: LabInventory) -> None:
    """RATE-T02 then RATE-T01: the first sample has no rates, the second has them.

    Args:
        reads: The telemetry ports.
        cid: This collector.
        inventory: For the interface UUIDs.

    Returns:
        None

    Raises:
        AssertionError: If an interface breaks the rule.
    """
    r1 = inventory.items[0]
    checked = 0
    for entry in inventory.plans[r1.id].interfaces:
        if entry.interface_id is None:
            continue
        found = samples(reads, cid, entry.interface_id)
        if len(found) < 2:
            continue
        first, second = found[0], found[1]
        assert first.baseline_state is BaselineState.NO_BASELINE
        assert first.quality & Quality.BASELINE_FIRST_SAMPLE
        assert first.in_octets is not None
        assert first.in_bps is None
        assert second.baseline_state is BaselineState.VALID
        assert second.in_bps is not None
        assert second.sample_elapsed_us is not None
        checked += 1
    assert checked >= 1


async def second_life(
    paths: InstallPaths, sim: Lab, inventory: LabInventory, cid: CollectorId
) -> None:
    """Restart and check that rates resume from the checkpoint at once.

    Args:
        paths: The install.
        sim: The simulator.
        inventory: The lab inventory.
        cid: The identity of the first run.

    Returns:
        None

    Raises:
        AssertionError: If any expectation fails.
    """
    r1 = inventory.items[0]
    store = SqliteTelemetryStore(paths.state_dir / TELEMETRY_FILE)
    reads = SqliteTelemetry(store)
    ids = [e.interface_id for e in inventory.plans[r1.id].interfaces if e.interface_id is not None]
    before = {i: len(samples(reads, cid, i)) for i in ids}
    run = await start(paths, sim)
    assert run.service.parts.collector.id == cid  # SVC-T04, MPOL-006: same identity

    async def resumed() -> bool:
        """Report whether any interface got a sample after the restart.

        Args:
            None

        Returns:
            bool: True once one did.

        Raises:
            None
        """
        found = [await asyncio.to_thread(samples, reads, cid, i) for i in ids]
        return any(len(s) > before[i] for i, s in zip(ids, found, strict=True))

    assert await wait_until(resumed, 10), "no sample after the restart"
    resumed_ok = 0
    for iid in ids:
        found = samples(reads, cid, iid)
        if len(found) > before[iid] and before[iid] >= 1:
            assert found[before[iid]].baseline_state is BaselineState.VALID  # RATE-004
            resumed_ok += 1
    assert resumed_ok >= 1
    assert await run.stop() == 0
    records = polls(reads, cid, r1.id)
    assert records
    assert all(p.execution is not PollExecution.FAILED for p in records)
    store.close()


def test_collector_runs_end_to_end_and_resumes_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3 exit: discovery, reachability, rates, honest SNMP timeout, restart with rates.

    Cites DISC-T01, RATE-T01, RATE-T02, SVC-T03, SVC-T04, RATE-004, E.2 row 7.

    Args:
        tmp_path: Test directory.
        monkeypatch: Wires the lab inventory into the merge seam.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run both lives of the collector against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        async with lab([router(1), router(2)]) as sim:
            paths = make_root(tmp_path, sim.port)
            inventory = LabInventory([lab_device("rtr-sim-001", 1), lab_device("rtr-sim-002", 2)])
            monkeypatch.setattr(inventory_seam, "INVENTORY_FACTORY", lambda _ctx: inventory)
            cid = await first_life(paths, sim, inventory)
            await second_life(paths, sim, inventory, cid)

    asyncio.run(scenario())
