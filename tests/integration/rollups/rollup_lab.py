"""A small simulated fleet for the rollup job tests: polls, ICMP cycles, interface samples.

``Fleet`` writes what the collector would write for a few devices and
interfaces at a fixed poll interval: one poll record per slot, one ICMP
cycle and one sample per interface a second or two later. Rates are whole
numbers, so SQLite's float32 rollup storage (ADR-021 amendment 3) keeps
them exactly and tests can compare backends and runs with ``==``.

Dependencies:
    tests.contract.contract_samples; vakr.adapters.sqlite.telemetry.

Example:
    >>> Fleet(devices=1, interfaces=2).subjects()[1].__len__()
    2
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from tests.contract import contract_samples as s
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.observations import Observation
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.ports.telemetry import TelemetryBackend
from vakr.ports.telemetry.rollup_source import ICMP_METRICS, INTERFACE_METRICS

SECOND = timedelta(seconds=1)


@dataclass
class Fleet:
    """Devices, their interfaces, and one collector.

    Attributes:
        devices: How many devices.
        interfaces: Interfaces per device.
        collector: The collector UUID.
        device_ids: Device UUIDs.
        interface_ids: Interface UUIDs per device.
    """

    devices: int = 2
    interfaces: int = 2
    collector: CollectorId = field(default_factory=lambda: new_id(CollectorId))
    device_ids: list[DeviceId] = field(default_factory=list)
    interface_ids: dict[DeviceId, list[InterfaceId]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Create the UUIDs.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.device_ids = [new_id(DeviceId) for _ in range(self.devices)]
        self.interface_ids = {d: [new_id(InterfaceId) for _ in range(self.interfaces)]
                              for d in self.device_ids}  # fmt: skip

    def subjects(self) -> tuple[list[DeviceId], list[InterfaceId]]:
        """Return every device and every interface.

        Args:
            None

        Returns:
            tuple[list[DeviceId], list[InterfaceId]]: Devices and interfaces.

        Raises:
            None
        """
        return self.device_ids, [i for d in self.device_ids for i in self.interface_ids[d]]

    def observations(
        self, start: datetime, slots: int, interval_s: int = 60
    ) -> Iterator[Observation]:
        """Yield what the collector writes for ``slots`` polls from ``start``.

        Args:
            start: First slot (UTC).
            slots: Number of poll slots.
            interval_s: Poll interval.

        Returns:
            Iterator[Observation]: Polls, ICMP cycles, and interface samples.

        Raises:
            None
        """
        for k in range(slots):
            at = start + k * interval_s * SECOND
            for n, dev in enumerate(self.device_ids):
                yield s.poll(dev, self.collector, at, effective_interval_s=interval_s,
                             configured_interval_s=interval_s)  # fmt: skip
                yield s.icmp(dev, self.collector, at + SECOND)
                for j, iface in enumerate(self.interface_ids[dev]):
                    yield s.sample(dev, iface, self.collector, at + 2 * SECOND,
                                   in_bps=float(1000 * k + 10 * n + j), out_bps=float(k % 7),
                                   in_pps=float(k), out_pps=2.0, in_error_rate=0.0,
                                   out_error_rate=0.0)  # fmt: skip

    def write(self, backend: TelemetryBackend, rows: Iterable[Observation]) -> int:
        """Write observations in batches.

        Args:
            backend: The backend.
            rows: Observations.

        Returns:
            int: Observations written.

        Raises:
            None
        """
        batch: list[Observation] = []
        total = 0
        for row in rows:
            batch.append(row)
            if len(batch) == 20_000:
                total += backend.writer.write_batch(new_id(BatchId), batch).inserted
                batch = []
        if batch:
            total += backend.writer.write_batch(new_id(BatchId), batch).inserted
        return total

    def buckets(
        self, backend: TelemetryBackend, window: TimeWindow
    ) -> dict[tuple[UUID, Metric], tuple[RollupBucket, ...]]:
        """Read every stored bucket of the fleet in a window (at most 1,200 per series).

        Args:
            backend: The backend.
            window: Window on bucket start.

        Returns:
            dict: ``(subject, metric)`` to its buckets, oldest first.

        Raises:
            None
        """
        devices, interfaces = self.subjects()
        pairs: list[tuple[UUID, Metric]] = [(d, m) for d in devices for m in ICMP_METRICS]
        pairs.extend((i, m) for i in interfaces for m in INTERFACE_METRICS)
        return {(subject, m): backend.rollups.read(subject, self.collector, m, window, limit=1200)
                for subject, m in pairs}  # fmt: skip
