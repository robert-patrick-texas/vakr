"""A device added through the API is discovered and polled by the real collector (27.4, 6.2).

The API is the production build (``vakr.app.api.build``) over the same
SQLite metadata database the collector reads, reached as an admin on the
Unix socket; the collector is ``CollectorService`` with the real
``InventoryService`` (no fake inventory), real ICMP to loopback, and the
real PySNMP adapter against the in-process IOS XE simulator. With an
empty inventory at start:

* ``POST /api/v1/devices`` creates an API-managed device (META-008);
* the collector notices the change counter, discovers the device
  (DISC-001), and polls it: device poll records and interface samples
  appear (SNMP-T03), and its interfaces are listed through the API;
* ``DELETE`` retires it (META-009) and polling stops, while its history
  stays readable.

The credential name check is given the simulator's credential name,
because the strict credential provider refuses a file the test user owns;
the community itself stays inside the simulator lab.

Dependencies:
    tests.simulators.snmp.simlab; tests.integration.collector.collector_lab, test_collector_e2e.

Example:
    $ uv run pytest tests/integration/collector/test_manual_device_e2e.py
"""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tests.integration.collector.collector_lab import icmp_unavailable, make_root, wait_until
from tests.integration.collector.test_collector_e2e import polls, samples, start
from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import lab
from tests.unit.api.api_testkit import peer_client
from tests.unit.auth.auth_testkit import cheap_hasher
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.api.app import create_app
from vakr.app.api.build import build_parts
from vakr.auth import Passwords
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.users import Role
from vakr.inventory.validate import Checks

ICMP_SKIP = icmp_unavailable()
pytestmark = pytest.mark.skipif(ICMP_SKIP is not None, reason=ICMP_SKIP or "")
NAME = "rtr-sim-001"
"""The simulated router, and the name of its credential in the lab's resolver."""


def test_27_4_a_device_added_through_the_api_is_discovered_and_polled(tmp_path: Path) -> None:
    """27.4, 6.2, DISC-001, META-009: add, discover, poll; retire, and polling stops.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the API and the collector against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            AssertionError: If any expectation fails.
        """
        async with lab([router(1)]) as sim:
            paths = make_root(tmp_path, sim.port)
            api = build_parts(paths, passwords=Passwords(cheap_hasher()))
            api.context.inventory_checks = lambda: Checks(frozenset({NAME}))
            client = peer_client(create_app(api.context), Role.ADMIN)
            run = await start(paths, sim)
            store = SqliteTelemetryStore(paths.state_dir / TELEMETRY_FILE)
            try:
                await _add_poll_retire(client, run.service.parts.collector.id,
                                       SqliteTelemetry(store))  # fmt: skip
            finally:
                assert await run.stop() == 0
                store.close()
                api.close()

    asyncio.run(scenario())


async def _add_poll_retire(client: TestClient, cid: CollectorId, reads: SqliteTelemetry) -> None:
    """Add the device, wait for discovery and samples, then retire it.

    Args:
        client: The admin's socket client.
        cid: The collector's ID.
        reads: The telemetry ports.

    Returns:
        None

    Raises:
        AssertionError: If any expectation fails.
    """
    body = {"name": NAME, "address": "127.0.0.1", "credential": NAME, "tags": ["lab"]}
    made = await asyncio.to_thread(client.post, "/api/v1/devices", json=body)
    assert made.status_code == 201, made.text
    assert made.json()["managed_by"] == "api"
    device = DeviceId(UUID(made.json()["id"]))
    url = f"/api/v1/devices/{device}/interfaces"

    async def sampled() -> bool:
        """Report whether a discovered interface has a sample yet.

        Args:
            None

        Returns:
            bool: True once one has.

        Raises:
            None
        """
        answer = await asyncio.to_thread(client.get, url, params={"limit": 50})
        ids = [InterfaceId(UUID(r["ref"]["interface_id"])) for r in answer.json()["rows"]]
        return any([await asyncio.to_thread(samples, reads, cid, i) for i in ids])

    assert await wait_until(sampled, 30), "the added device was never discovered and sampled"
    assert await asyncio.to_thread(polls, reads, cid, device)
    gone = await asyncio.to_thread(client.delete, f"/api/v1/devices/{device}")
    assert gone.json()["admin_state"] == "retired"
    await asyncio.sleep(3)  # let a poll already started finish
    settled = len(await asyncio.to_thread(polls, reads, cid, device))
    await asyncio.sleep(5)  # 2.5 poll intervals at the test's 2 s override
    assert len(await asyncio.to_thread(polls, reads, cid, device)) == settled
    kept = await asyncio.to_thread(client.get, url, params={"include_retired": True})
    rows = kept.json()["rows"]
    assert rows, "a retired device keeps its interfaces and history (META-009)"
