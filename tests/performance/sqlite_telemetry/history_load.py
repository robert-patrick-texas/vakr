"""Workload builders for the M4 history budget checks (MVP-ACC-05, 12.9).

The telemetry side reuses ``tele_workload``: the 100-device, 1,600-interface,
two-collector MVP topology written through the real SQLite adapter. The
metadata side is the memory reference store holding the same UUIDs, so the
query services can name every interface. Interfaces are 10 Gb/s Ethernet
ports, so the workload's rates stay below capacity. Rollups for long
graphs are written directly as 15-minute buckets for one interface, which is
all a single 400-day graph reads (ADR-021 appendix B).

Dependencies:
    tests.performance.sqlite_telemetry.tele_workload; tests.contract.contract_samples.
    vakr.adapters: The SQLite telemetry store and the memory metadata store.

Example:
    >>> len(build_topology(21).interfaces)
    1600
"""

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from tests.contract import contract_samples as samples
from tests.performance.sqlite_telemetry.tele_workload import START, Generator, Topology
from tests.performance.sqlite_telemetry.tele_workload import build_topology as build_topology
from vakr.adapters.memory import memory_uow_factory
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
    telemetry_migrations,
)
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric
from vakr.domain.state import RollupBucket
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend

BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def metadata_for(topo: Topology) -> MetadataUnitOfWorkFactory:
    """Build a memory metadata store with the topology's devices, interfaces, and collectors.

    Args:
        topo: The workload topology.

    Returns:
        MetadataUnitOfWorkFactory: Opens units of work on the filled store.

    Raises:
        None
    """
    uow_factory = memory_uow_factory()
    with uow_factory() as uow:
        for n, col in enumerate(topo.collectors):
            uow.collectors.add(samples.collector(("east", "west")[n], id=col))
        for d, dev in enumerate(topo.devices):
            uow.devices.add(
                samples.device(f"dev{d:03d}", id=DeviceId(dev), poll_address=f"192.0.2.{d + 1}")
            )
        for iface, dev, index, _base, _size in topo.interfaces:
            uow.interfaces.add(
                samples.interface(
                    DeviceId(dev),
                    f"Te0/0/{index}",
                    id=iface,
                    if_index=index,
                    if_type=6,
                    if_high_speed=10_000,
                )
            )
        uow.commit()
    return uow_factory


def open_store(directory: Path) -> tuple[SqliteTelemetryStore, Callable[[], TelemetryBackend]]:
    """Create a migrated telemetry file and return the store and a view factory.

    Args:
        directory: Where to create ``telemetry.db``.

    Returns:
        tuple: The store (close it when done) and the backend factory.

    Raises:
        None
    """
    path = directory / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    store = SqliteTelemetryStore(path)
    return store, sqlite_telemetry_factory(store)


def load_minutes(backend: SqliteTelemetry, topo: Topology, minutes: int) -> int:
    """Write ``minutes`` of the full workload, moving staged rows every 15 minutes.

    Args:
        backend: The adapter.
        topo: The topology.
        minutes: Simulated minutes from ``START``.

    Returns:
        int: Rows written.

    Raises:
        None
    """
    gen, rows = Generator(topo, 21), 0
    for m in range(minutes):
        rows += backend.writer.write_batch(new_id(BatchId), gen.minute(m)).inserted
        if (m + 1) % 15 == 0 or m + 1 == minutes:
            backend.maintain(START + timedelta(minutes=m + 4))
    return rows


def rollup_days(backend: TelemetryBackend, iface: InterfaceId, col: CollectorId, days: int) -> int:
    """Write ``days`` of full 15-minute in/out rate buckets for one interface, ending at ``START``.

    Args:
        backend: The adapter.
        iface: The interface.
        col: The collector.
        days: Days of buckets.

    Returns:
        int: Buckets written.

    Raises:
        None
    """
    first = START - timedelta(days=days)
    written = 0
    for day in range(days):
        batch = []
        for k in range(96):
            at = first + timedelta(days=day) + k * BUCKET
            v = 1e8 + (day * 96 + k) % 1000
            batch += [
                RollupBucket(
                    iface,
                    col,
                    metric,
                    at,
                    ROLLUP_BUCKET_SECONDS,
                    15,
                    15,
                    15,
                    minimum=v,
                    mean=v + 1,
                    p95=v + 2,
                    maximum=v + 3,
                )
                for metric in (Metric.IN_BPS, Metric.OUT_BPS)
            ]
        written += backend.rollups.write(batch)
    return written


def save_topology(topo: Topology, path: Path) -> None:
    """Write a topology's UUIDs and rates as JSON, so a kept database can be reused.

    Args:
        topo: The topology.
        path: The JSON file.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written.
    """
    data = {
        "collectors": [str(c) for c in topo.collectors],
        "devices": [str(d) for d in topo.devices],
        "interfaces": [[str(i), str(d), n, b, z] for i, d, n, b, z in topo.interfaces],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def load_topology(path: Path) -> Topology:
    """Read a topology written by ``save_topology``.

    Args:
        path: The JSON file.

    Returns:
        Topology: The topology.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If it is not a saved topology.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return Topology(
        tuple(CollectorId(UUID(c)) for c in data["collectors"]),
        tuple(DeviceId(UUID(d)) for d in data["devices"]),
        tuple(
            (InterfaceId(UUID(i)), DeviceId(UUID(d)), n, b, z)
            for i, d, n, b, z in data["interfaces"]
        ),
    )
