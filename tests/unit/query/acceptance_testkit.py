"""Constructed telemetry for the acceptance report tests (MVP-ACC-01, MVP-ACC-02, MVP-ACC-10).

``Fleet`` holds one in-memory metadata store and one telemetry store (the
reference adapters, held to the SQLite adapter by the contract suites) and
writes what a collector would for each slot of a device: the poll record,
its ICMP and SNMP device observations, and one sample per interface the
poll sampled. ``SlotPlan`` says how one slot went, so a test states its
known answer slot by slot. ``Fleet.rollup`` builds the 15-minute buckets
the rollup job would, so the gap report's rollup path is exercised too.

Dependencies:
    tests.contract.contract_samples: Valid observation and inventory builders.
    vakr.adapters.memory: The reference stores.

Example:
    >>> Fleet().collector.name
    'east'
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tests.contract import contract_samples as s
from vakr.adapters.memory import MemoryTelemetryStore, memory_telemetry_factory, memory_uow_factory
from vakr.domain.enums import BaselineState, IcmpState, PollExecution, SnmpState, UptimeContinuity
from vakr.domain.ids import BatchId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.domain.observations import Observation
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket, bucket_start
from vakr.ports.telemetry.rollup_source import ICMP_METRICS, INTERFACE_METRICS
from vakr.query.acceptance import AcceptanceQueries
from vakr.query.acceptance_evidence import LossRange

T0 = s.T0
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """How one slot of one device went.

    Attributes:
        execution: The poll's execution state.
        quality: The poll's quality flags.
        start_s: Start offset from the slot, in seconds (``None``: never started).
        duration_s: How long it ran.
        icmp: Whether an ICMP result was stored.
        snmp: The SNMP device state, or ``None`` for no SNMP observation.
        sampled: Interface names sampled (``None``: every interface).
        sample_quality: Per-interface sample flags (rates are then left out).
        uptime: The SNMP uptime continuity.
        fields: Per-interface sample field overrides, such as a missing packet rate.
    """

    execution: PollExecution = PollExecution.COMPLETE
    quality: Quality = Quality.NONE
    start_s: float | None = 0.2
    duration_s: float = 3.0
    icmp: bool = True
    snmp: SnmpState | None = SnmpState.RESPONDING
    sampled: frozenset[str] | None = None
    sample_quality: dict[str, Quality] = field(default_factory=dict)
    uptime: UptimeContinuity = UptimeContinuity.CONTINUOUS
    fields: dict[str, dict[str, object]] = field(default_factory=dict)


OK = SlotPlan()
_MISS = Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED
NOT_ATTEMPTED = SlotPlan(PollExecution.NOT_ATTEMPTED, _MISS, None, 0, icmp=False, snmp=None,
                         sampled=frozenset())  # fmt: skip
SKIP = None
"""A plan of ``None`` writes nothing for the slot (no record at all)."""


class Fleet:
    """One collector, its inventory, and constructed telemetry."""

    def __init__(self, *, rollups: bool = True) -> None:
        """Create empty stores and the ``east`` collector.

        Args:
            rollups: Whether the telemetry store keeps rollups.

        Returns:
            None

        Raises:
            None
        """
        self.store = MemoryTelemetryStore(rollups=rollups)
        self.telemetry = memory_telemetry_factory(self.store)
        self.uow = memory_uow_factory()
        self.collector = s.collector("east")
        self.losses: list[LossRange] = []
        self.interfaces: dict[str, list[Interface]] = defaultdict(list)
        with self.uow() as uow:
            uow.collectors.add(self.collector)
            uow.commit()

    def device(self, name: str, **overrides: object) -> Device:
        """Add a device.

        Args:
            name: Device name.
            **overrides: Device fields.

        Returns:
            Device: The stored device.

        Raises:
            None
        """
        device = s.device(name, poll_address=f"192.0.2.{len(self.interfaces) + 1}", **overrides)
        with self.uow() as uow:
            uow.devices.add(device)
            uow.commit()
        self.interfaces[name] = []
        return device

    def interface(self, device: Device, name: str, **overrides: object) -> Interface:
        """Add an interface to a device.

        Args:
            device: The device.
            name: ``ifName``.
            **overrides: Interface fields.

        Returns:
            Interface: The stored interface.

        Raises:
            None
        """
        iface = s.interface(device.id, name, if_index=len(self.interfaces[device.name]) + 1,
                            **overrides)  # fmt: skip
        with self.uow() as uow:
            uow.interfaces.add(iface)
            uow.commit()
        self.interfaces[device.name].append(iface)
        return iface

    def write(self, rows: list[Observation]) -> None:
        """Store observations in batches.

        Args:
            rows: Observations.

        Returns:
            None

        Raises:
            None
        """
        backend = self.telemetry()
        for i in range(0, len(rows), 20_000):
            backend.writer.write_batch(new_id(BatchId), rows[i : i + 20_000])

    def run(
        self, device: Device, start: datetime, slots: int,
        plan: Callable[[int], SlotPlan | None] = lambda _k: OK, *, phase_s: float = 7.0,
    ) -> None:  # fmt: skip
        """Write ``slots`` 60 s slots of a device, each as ``plan(k)`` says.

        Args:
            device: The device.
            start: The first slot's minute.
            slots: How many slots.
            plan: Slot number to its plan; ``None`` writes nothing.
            phase_s: The device's slot offset into each minute.

        Returns:
            None

        Raises:
            None
        """
        rows: list[Observation] = []
        for k in range(slots):
            p = plan(k)
            if p is not None:
                rows += self._slot(device, start + k * MINUTE + timedelta(seconds=phase_s), p)
        self.write(rows)

    def _slot(self, device: Device, at: datetime, p: SlotPlan) -> list[Observation]:
        """Build one slot's observations.

        Args:
            device: The device.
            at: The slot.
            p: Its plan.

        Returns:
            list[Observation]: Poll, ICMP, SNMP, and samples.

        Raises:
            None
        """
        col = self.collector.id
        started = None if p.start_s is None else at + timedelta(seconds=p.start_s)
        done = None if started is None else started + timedelta(seconds=p.duration_s)
        rows: list[Observation] = [s.poll(device.id, col, at, execution=p.execution,
                                          quality=p.quality, started_at=started,
                                          completed_at=done)]  # fmt: skip
        if started is None:
            return rows
        if p.icmp:
            rows.append(
                s.icmp(
                    device.id,
                    col,
                    started + timedelta(seconds=1),
                    state=IcmpState.REACHABLE,
                    quality=Quality.NONE,
                    rtts_us=(1000,) * 5,
                    max_rtt_us=1000,
                    min_rtt_us=1000,
                )
            )
        if p.snmp is not None:
            rows.append(s.snmp(device.id, col, started + timedelta(seconds=2), state=p.snmp,
                               uptime_continuity=p.uptime))  # fmt: skip
        for iface in self.interfaces[device.name]:
            if p.sampled is None or iface.if_name in p.sampled:
                rows.append(
                    self._sample(
                        device,
                        iface,
                        started + timedelta(seconds=2.5),
                        p.sample_quality.get(iface.if_name, Quality.NONE),
                        p.fields.get(iface.if_name, {}),
                    )
                )
        return rows

    def _sample(
        self, device: Device, iface: Interface, at: datetime, flags: Quality,
        fields: dict[str, object],
    ) -> Observation:  # fmt: skip
        """Build one interface sample; a baseline-invalidating flag leaves every rate out.

        Args:
            device: The device.
            iface: The interface.
            at: Sample time.
            flags: Quality flags.
            fields: Field overrides.

        Returns:
            Observation: The sample.

        Raises:
            None
        """
        rates = {"in_bps": 1e6, "out_bps": 2e6, "in_pps": 100.0, "out_pps": 200.0,
                 "in_error_rate": 0.0, "out_error_rate": 0.0}  # fmt: skip
        reset = flags & ~(Quality.RATE_EXCEEDS_CAPACITY | Quality.RETRIED)
        if reset:
            rates = dict.fromkeys(rates)
        state = BaselineState.INVALIDATED if reset else BaselineState.VALID
        return s.sample(device.id, iface.id, self.collector.id, at, quality=flags,
                        baseline_state=state, **(rates | fields))  # fmt: skip

    def health(self, start: datetime, minutes: int, *, gaps: tuple[tuple[int, int], ...] = (),
               spool_pct: Callable[[int], float] = lambda _k: 1.0) -> None:  # fmt: skip
        """Write one collector-health report a minute, except inside ``gaps``.

        Args:
            start: First report.
            minutes: Reports to write.
            gaps: ``(from, to)`` minute ranges with no report.
            spool_pct: Spool fill by minute.

        Returns:
            None

        Raises:
            None
        """
        rows: list[Observation] = [
            s.health(self.collector.id, start + k * MINUTE, spool_used_pct=spool_pct(k),
                     active_interfaces=1600, active_devices=100)
            for k in range(minutes) if not any(a <= k < b for a, b in gaps)
        ]  # fmt: skip
        self.write(rows)

    def rollup(self, until: datetime) -> None:
        """Build the 15-minute buckets the rollup job would, for every bucket ending by ``until``.

        Args:
            until: Rollup frontier.

        Returns:
            None

        Raises:
            None
        """
        groups: dict[tuple, list] = defaultdict(list)
        restarts: set[tuple] = set()
        for o in list(self.store.observations.values()):
            start = bucket_start(o.timestamp, ROLLUP_BUCKET_SECONDS)
            if getattr(o, "uptime_continuity", None) is UptimeContinuity.RESET_DETECTED:
                restarts.add((o.device_id, o.collector_id, start))  # RET-006
            subject = getattr(o, "interface_id", None)
            if subject is None and type(o).__name__ != "IcmpObservation":
                continue
            if start + timedelta(seconds=ROLLUP_BUCKET_SECONDS) <= until:
                for kind in INTERFACE_METRICS if subject is not None else ICMP_METRICS:
                    groups[subject or o.device_id, o.collector_id, kind, start].append(o)
        buckets = []
        for (subject, col, metric, start), members in groups.items():
            values = [getattr(m, metric.field) for m in members]
            valid = [float(v) for v in values if v is not None]
            q = Quality.BASELINE_UPTIME_RESET if (subject, col, start) in restarts else Quality.NONE
            for m in members:
                q |= m.quality
            agg = (
                (min(valid), sum(valid) / len(valid), max(valid), max(valid))
                if valid
                else (None,) * 4
            )
            buckets.append(
                RollupBucket(
                    subject,
                    col,
                    metric,
                    start,
                    ROLLUP_BUCKET_SECONDS,
                    15,
                    len(members),
                    len(valid),
                    agg[0],
                    agg[1],
                    agg[2],
                    agg[3],
                    q,
                )
            )
        self.telemetry().rollups.write(buckets)

    def queries(self, now: datetime, local: str = "east") -> AcceptanceQueries:
        """Build the report service at a fixed time.

        Args:
            now: The report time.
            local: This node's collector name (``east`` is the fleet's own).

        Returns:
            AcceptanceQueries: The service.

        Raises:
            None
        """
        return AcceptanceQueries(self.uow, self.telemetry, losses=lambda _c: tuple(self.losses),
                                 local_collector=local, clock=lambda: now)  # fmt: skip
