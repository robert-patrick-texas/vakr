"""6.2 on the real stores: the API reads SQLite telemetry while the collector writes it (WAL).

``build_parts`` opens a relocated install exactly as ``vakr api serve``
does. A writer thread, standing in for the collector with its own store
object, appends interface samples while the API answers graphs and lists
from the same files: every answer is a 200 and the data the writer
committed shows up.

Dependencies:
    vakr.app.api.build; the SQLite adapters; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/api/test_api_sqlite.py
"""

import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

from tests.contract import contract_samples as samples
from tests.unit.api.api_testkit import peer_client
from tests.unit.api.test_api_server import install
from tests.unit.auth.auth_testkit import cheap_hasher
from tests.unit.query.query_testkit import NOW
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, sqlite_uow_factory
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.api.app import create_app
from vakr.app.api.build import build_parts
from vakr.auth import Passwords
from vakr.domain.ids import BatchId, new_id
from vakr.domain.users import Role
from vakr.ports.clock import ManualClock

ETHERNET = 6
MINUTES = 60


def seed(state: Path) -> tuple[Any, Any, Any]:
    """Add a collector, a device, and an interface to ``metadata.db``.

    Args:
        state: The state directory.

    Returns:
        tuple: The collector, device, and interface records.

    Raises:
        None
    """
    store = SqliteMetadataStore(state / METADATA_FILE)
    try:
        with sqlite_uow_factory(store)() as uow:
            collector = uow.collectors.add(samples.collector("east"))
            device = uow.devices.add(samples.device("core1"))
            iface = uow.interfaces.add(samples.interface(device.id, "Gi0/0/1", if_type=ETHERNET,
                                                         if_high_speed=1000))  # fmt: skip
            uow.commit()
    finally:
        store.close()
    return collector, device, iface


def test_6_2_api_reads_while_the_collector_writes(tmp_path: Path) -> None:
    """6.2, STORE-011: WAL reads beside the only writer never fail and see committed data.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = install(tmp_path, 8080)
    collector, device, iface = seed(paths.state_dir)
    parts = build_parts(paths, clock=ManualClock(NOW), passwords=Passwords(cheap_hasher()))
    writer_store = SqliteTelemetryStore(paths.state_dir / TELEMETRY_FILE)

    def collect() -> None:
        """Write one sample per minute of the last hour, one batch each, as ingestion does.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        backend = SqliteTelemetry(writer_store)
        for minute in range(MINUTES, 0, -1):
            at = NOW - timedelta(minutes=minute)
            row = samples.sample(device.id, iface.id, collector.id, at, in_bps=1000.0 + minute)
            backend.writer.write_batch(new_id(BatchId), [row])

    client = peer_client(create_app(parts.context), Role.OPERATOR)
    url = f"/api/v1/interfaces/{iface.id}/history/traffic"
    thread = threading.Thread(target=collect)
    statuses: list[int] = []
    try:
        thread.start()
        while thread.is_alive():
            statuses.append(client.get(url, params={"range": "1h"}).status_code)
            statuses.append(client.get("/api/v1/devices").status_code)
        thread.join()
        final = client.get(url, params={"range": "1h"}).json()
    finally:
        writer_store.close()
        parts.close()
    assert set(statuses) <= {200}
    inbound = [s for s in final["series"] if s["metric"] == "in_bps"]
    values = [p["value"] for p in inbound[0]["points"] if p["value"] is not None]
    assert len(values) == MINUTES
