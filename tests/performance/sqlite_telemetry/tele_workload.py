"""Deterministic MVP workload as real domain observations, with the rollups M4 would write.

The topology matches blueprint 27.2 and the ADR-021 benchmark: 100
devices (80 routers with 10 interfaces, 20 switches with 40), polled every
60 s by two collectors, so one node stores 3,200 interface series and 200
each of ICMP, SNMP, and poll series, plus one health snapshot per
collector per minute: 3,802 rows a minute, 5.47 million a day. Rates
follow a diurnal curve with noise, counters are HC64 and wrap, about 1% of
samples carry errors, and 0.4% of probes get no reply.

``Rollups`` keeps the last buckets' native values and builds the
``RollupBucket``s the M4 job would write for every interface and ICMP
metric, with nearest-rank p95 (``vakr.logic.stats.p95``, E.17 #13).

Dependencies:
    random, math: Deterministic values.
    vakr.domain, vakr.logic.stats: Observations, buckets, and p95.

Example:
    >>> topo = build_topology(21)
    >>> len(topo.devices), len(topo.interfaces), len(topo.collectors)
    (100, 1600, 2)
"""

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from vakr.domain.enums import (
    AddressFamily,
    BaselineState,
    CollectorState,
    CounterFamily,
    IcmpState,
    IfAdminStatus,
    IfOperStatus,
    PollExecution,
    ReachabilityStrategy,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId, new_id
from vakr.domain.observations import (
    CollectorHealthObservation,
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    Observation,
    SnmpDeviceObservation,
)
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric
from vakr.domain.state import RollupBucket
from vakr.logic.stats import p95

START = datetime(2026, 9, 1, tzinfo=UTC)
"""First simulated minute, as in the ADR-021 benchmark."""

IFACE_METRICS = (
    Metric.IN_BPS,
    Metric.OUT_BPS,
    Metric.IN_PPS,
    Metric.OUT_PPS,
    Metric.IN_ERRORS,
    Metric.OUT_ERRORS,
)
ICMP_METRICS = (Metric.RTT_MEDIAN_US, Metric.LOSS_PCT, Metric.JITTER_US)


@dataclass(frozen=True, slots=True)
class Topology:
    """The simulated inventory.

    Attributes:
        collectors: The two positions.
        devices: Device IDs.
        interfaces: ``(interface, device, ifIndex, base bit/s, packet bytes)``.
    """

    collectors: tuple[CollectorId, ...]
    devices: tuple[DeviceId, ...]
    interfaces: tuple[tuple[InterfaceId, DeviceId, int, float, int], ...]


def build_topology(seed: int) -> Topology:
    """Build the 100-device, 1,600-interface, two-collector topology.

    Args:
        seed: Random seed for rates and sizes.

    Returns:
        Topology: The inventory.

    Raises:
        None
    """
    rng = random.Random(seed)  # noqa: S311  # reproducible workload, not secrets
    devices = tuple(new_id(DeviceId) for _ in range(100))
    interfaces = []
    for d, dev in enumerate(devices):
        for i in range(10 if d < 80 else 40):
            base = 10 ** rng.uniform(4.0, 9.0)
            interfaces.append((new_id(InterfaceId), dev, i + 1, base, rng.randint(200, 1400)))
    return Topology((new_id(CollectorId), new_id(CollectorId)), devices, tuple(interfaces))


class Generator:
    """Produces each simulated minute's observations for both collectors."""

    def __init__(self, topo: Topology, seed: int) -> None:
        """Prepare per-series counters.

        Args:
            topo: The inventory.
            seed: Random seed.

        Returns:
            None

        Raises:
            None
        """
        self._topo = topo
        self._rng = random.Random(seed + 1)  # noqa: S311  # reproducible workload
        self._octets: dict[tuple[UUID, UUID], list[int]] = {}

    def minute(self, m: int) -> list[Observation]:
        """Return minute ``m``'s observations, each device at its own second.

        Args:
            m: Minutes after ``START``.

        Returns:
            list[Observation]: About 3,802 observations in time order.

        Raises:
            None
        """
        out: list[Observation] = []
        diurnal = 0.55 + 0.45 * math.sin(2 * math.pi * ((m / 1440) - 0.3))
        for col in self._topo.collectors:
            for d, dev in enumerate(self._topo.devices):
                t = START + timedelta(minutes=m, seconds=(d * 0.6) % 60)
                out += self._device(col, dev, t)
            out.append(
                CollectorHealthObservation(
                    new_id(ObservationId),
                    col,
                    START + timedelta(minutes=m, seconds=59),
                    CollectorState.HEALTHY,
                    "7.2.0-bench",
                    queue_depth=3,
                    queue_capacity=1000,
                )
            )
        by_device: dict[UUID, list[tuple[InterfaceId, DeviceId, int, float, int]]] = defaultdict(
            list
        )
        for iface in self._topo.interfaces:
            by_device[iface[1]].append(iface)
        polls = {(o.collector_id, o.device_id): o for o in out if isinstance(o, DevicePoll)}
        for (col, dev), poll in polls.items():
            out += [self._sample(col, poll, i, diurnal, m) for i in by_device[dev]]
        out.sort(key=lambda o: o.timestamp)
        return out

    def _device(self, col: CollectorId, dev: DeviceId, t: datetime) -> list[Observation]:
        """Build one device's poll, ICMP cycle, and SNMP exchange.

        Args:
            col: The collector.
            dev: The device.
            t: Poll time.

        Returns:
            list[Observation]: Three observations.

        Raises:
            None
        """
        rng, poll_id = self._rng, new_id(PollId)
        base = 1000 + (hash((col, dev)) % 40_000)
        rtts = tuple(
            None if rng.random() < 0.004 else int(base * rng.uniform(0.9, 1.3)) for _ in range(5)
        )
        got = sorted(r for r in rtts if r is not None)
        lost = len(got) < 5
        icmp = IcmpObservation(
            new_id(ObservationId),
            poll_id,
            col,
            dev,
            t,
            AddressFamily.IPV4,
            "192.0.2.1",
            rtts,
            IcmpState.IMPAIRED if lost else IcmpState.REACHABLE,
            Quality.PARTIAL_LOSS if lost else Quality.NONE,
            min_rtt_us=got[0] if got else None,
            avg_rtt_us=got[len(got) // 2] if got else None,
            median_rtt_us=got[len(got) // 2] if got else None,
            max_rtt_us=got[-1] if got else None,
            jitter_us=(got[-1] - got[0]) // 2 if len(got) > 1 else None,
        )
        snmp = SnmpDeviceObservation(
            new_id(ObservationId),
            poll_id,
            col,
            dev,
            t + timedelta(seconds=1),
            SnmpVersion.V2C,
            SnmpState.RESPONDING,
            UptimeContinuity.CONTINUOUS,
            4,
            4,
            0,
            0,
            42,
            sys_uptime_ticks=int(t.timestamp() * 100) % (1 << 32),
        )
        poll = DevicePoll(
            new_id(ObservationId),
            poll_id,
            col,
            dev,
            t,
            60,
            60,
            ReachabilityStrategy.ICMP_GATED,
            PollExecution.COMPLETE,
            started_at=t,
            completed_at=t + timedelta(seconds=2),
        )
        return [poll, icmp, snmp]

    def _sample(
        self,
        col: CollectorId,
        poll: DevicePoll,
        iface: tuple[InterfaceId, DeviceId, int, float, int],
        diurnal: float,
        m: int,
    ) -> InterfaceObservation:
        """Build one interface sample with HC counters and derived rates.

        Args:
            col: The collector.
            poll: The device's poll this minute.
            iface: ``(interface, device, ifIndex, base bit/s, packet bytes)``.
            diurnal: Traffic level for the minute.
            m: Minute number.

        Returns:
            InterfaceObservation: The sample.

        Raises:
            None
        """
        ident, dev, index, base, size = iface
        rng = self._rng
        in_bps, out_bps = base * diurnal * rng.uniform(0.8, 1.2), base * 0.4 * rng.uniform(0.7, 1.3)
        counters = self._octets.setdefault(
            (ident, col), [rng.getrandbits(63), rng.getrandbits(40), rng.getrandbits(40), 0]
        )
        counters[0] = (counters[0] + int(in_bps * 7.5)) % (1 << 64)
        counters[1] = (counters[1] + int(out_bps * 7.5)) % (1 << 64)
        counters[2] += int(in_bps / 8 / size * 60)
        errors = rng.randint(1, 5) if rng.random() < 0.01 else 0
        counters[3] += errors
        first = m == 0
        rates: dict[str, Any] = (
            {}
            if first
            else {
                "in_bps": in_bps,
                "out_bps": out_bps,
                "in_pps": in_bps / 8 / size,
                "out_pps": out_bps / 8 / size,
                "in_error_delta": errors,
                "out_error_delta": 0,
                "in_error_rate": errors / 60,
                "out_error_rate": 0.0,
                "sample_elapsed_us": 60_000_000,
            }
        )
        return InterfaceObservation(
            new_id(ObservationId),
            poll.poll_id,
            col,
            dev,
            ident,
            poll.scheduled_at + timedelta(seconds=1),
            index,
            BaselineState.NO_BASELINE if first else BaselineState.VALID,
            Quality.BASELINE_FIRST_SAMPLE if first else Quality.NONE,
            admin_status=IfAdminStatus.UP,
            oper_status=IfOperStatus.UP,
            in_octets=counters[0],
            out_octets=counters[1],
            in_packets=counters[2],
            out_packets=counters[2] // 2,
            in_errors=counters[3],
            out_errors=0,
            counter_family=CounterFamily.HC64,
            **rates,
        )


class Rollups:
    """Collects native values per bucket and builds the buckets M4 would write."""

    def __init__(self) -> None:
        """Start empty.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._values: dict[int, dict[tuple[UUID, UUID, Metric], list[float | None]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def add(self, observations: list[Observation]) -> None:
        """Record the metric values of a minute's observations.

        Args:
            observations: Interface and ICMP observations (others are ignored).

        Returns:
            None

        Raises:
            None
        """
        width = timedelta(seconds=ROLLUP_BUCKET_SECONDS)
        for o in observations:
            if isinstance(o, InterfaceObservation):
                keys, metrics = (o.interface_id, o.collector_id), IFACE_METRICS
            elif isinstance(o, IcmpObservation):
                keys, metrics = (o.device_id, o.collector_id), ICMP_METRICS
            else:
                continue
            index = (o.timestamp - START) // width
            for metric in metrics:
                value = getattr(o, metric.field)
                self._values[index][(*keys, metric)].append(None if value is None else float(value))

    def take(self, index: int) -> list[RollupBucket]:
        """Build and forget the buckets of bucket number ``index``.

        Args:
            index: Bucket number after ``START``.

        Returns:
            list[RollupBucket]: One bucket per subject, collector, and metric.

        Raises:
            None
        """
        start = START + index * timedelta(seconds=ROLLUP_BUCKET_SECONDS)
        out = []
        for (subject, col, metric), values in self._values.pop(index, {}).items():
            valid = sorted(v for v in values if v is not None)
            stats: dict[str, Any] = (
                {}
                if not valid
                else {
                    "minimum": valid[0],
                    "mean": sum(valid) / len(valid),
                    "p95": p95(valid),
                    "maximum": valid[-1],
                }
            )
            if stats:
                stats["mean"] = min(max(stats["mean"], valid[0]), valid[-1])
            out.append(
                RollupBucket(
                    subject,
                    CollectorId(col),
                    metric,
                    start,
                    ROLLUP_BUCKET_SECONDS,
                    15,
                    len(values),
                    len(valid),
                    **stats,
                )
            )
        return out
