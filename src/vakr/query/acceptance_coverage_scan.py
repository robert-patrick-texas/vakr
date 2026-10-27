"""MVP-ACC-02 for one collector: graph ranges per monitored interface, reloads, discontinuities.

``CoverageScan`` judges each monitored interface (lifecycle ``active`` on a
device that is enabled or in maintenance and polls SNMP): every range and
family (``acceptance_coverage``) and every event in the event window
(``acceptance_events``). Each device's reloads are found from its ICMP
rollup buckets, which carry ``BASELINE_UPTIME_RESET`` when SNMP saw the
agent restart (RET-006), confirmed from the SNMP device observations, and
then every monitored interface of the device must have reset at the
reload. The collector passes when no graph is empty and nothing spiked.

Dependencies:
    vakr.domain, vakr.ports.telemetry: Observations and the acceptance port.
    vakr.query.acceptance_*: Coverage, events, views, bounds.
    vakr.query.snapshot: Bounded paging.

Safety:
    Read-only and bounded per interface and device.

Example:
    >>> CoverageScan.__name__
    'CoverageScan'
"""

from collections import Counter
from dataclasses import dataclass, field

from vakr.domain.enums import ObservationType, UptimeContinuity
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import SnmpDeviceObservation
from vakr.domain.quality import Quality
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.acceptance import AcceptanceBackend
from vakr.query.acceptance_coverage import newest, range_states
from vakr.query.acceptance_coverage_views import (
    CollectorCoverage,
    CoverageRow,
    EventRow,
    RangeCount,
)
from vakr.query.acceptance_events import interface_events, reload_check
from vakr.query.acceptance_limits import BUCKET_US, COVERAGE_RANGES, DEFAULT_INTERVAL_S, FAMILIES
from vakr.query.acceptance_subjects import as_time
from vakr.query.acceptance_views import Check, Verdict, combine
from vakr.query.snapshot import pages

MAX_SNMP_READ = 50_000
"""Most SNMP device observations read to place one device's reloads."""


@dataclass(slots=True)
class CoverageScan:
    """MVP-ACC-02 figures of one collector, accumulated interface by interface.

    Attributes:
        backend: The telemetry backend.
        collector: The collector judged.
        now_us: The report time.
        events_window: ``(start_us, end_us)`` for reloads and discontinuities.
        frontier_us: The rollup frontier, or ``None``.
        counts: ``(range, family)`` to state counts.
        rows: Interfaces whose graphs are not complete.
        events: Reload and discontinuity rows.
        interfaces: Interfaces judged.
        discontinuities: Event samples found.
        reloads: Device reloads found.
        spikes: Spikes found.
        over_capacity: Over-capacity samples following no event.
        truncated: Reads cut short by a bound.
    """

    backend: AcceptanceBackend
    collector: CollectorId
    now_us: int
    events_window: tuple[int, int]
    frontier_us: int | None
    counts: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    rows: list[CoverageRow] = field(default_factory=list)
    events: list[EventRow] = field(default_factory=list)
    interfaces: int = 0
    discontinuities: int = 0
    reloads: int = 0
    spikes: int = 0
    over_capacity: int = 0
    truncated: int = 0

    def interface(self, device: str, iface: InterfaceId, name: str, created_us: int) -> None:
        """Judge one monitored interface's ranges and events.

        Args:
            device: Device name.
            iface: The interface.
            name: Interface name.
            created_us: When it was discovered.

        Returns:
            None

        Raises:
            StorageError: If the store is unavailable.
        """
        self.interfaces += 1
        query, rollups = self.backend.acceptance, self.backend.capabilities.rollups
        found = newest(query, iface, self.collector, self.now_us, self.frontier_us,
                       rollups=rollups)  # fmt: skip
        self.truncated += found.truncated
        too_new = created_us > self.now_us - 3_600_000_000
        states = range_states(found, self.now_us, self.frontier_us, too_new=too_new)
        by_state: dict[str, list[str]] = {"missing": [], "gap": [], "pending": []}
        for (label, family), state in states.items():
            self.counts.setdefault((label, family), Counter())[state] += 1
            if state in by_state:
                by_state[state].append(f"{label}/{family}")
        if any(by_state.values()):
            self.rows.append(
                CoverageRow(
                    device,
                    name,
                    tuple(by_state["missing"]),
                    tuple(by_state["gap"]),
                    tuple(by_state["pending"]),
                )
            )
        self._events(device, iface, name)

    def _events(self, device: str, iface: InterfaceId, name: str) -> None:
        """Judge one interface's discontinuities in the event window.

        Args:
            device: Device name.
            iface: The interface.
            name: Interface name.

        Returns:
            None

        Raises:
            StorageError: If the store is unavailable.
        """
        found = interface_events(self.backend.acceptance, iface, self.collector,
                                 self.events_window, self.frontier_us)  # fmt: skip
        self.truncated += found.truncated
        self.over_capacity += found.over_capacity
        for at_us, flags, reset, spike in found.events:
            self.discontinuities += 1
            self.spikes += spike
            names = ", ".join(f.name.lower() for f in Quality if f and f in flags)
            detail = ("rate across the event, or over capacity just after it" if spike
                      else "no rate stored: the graph shows a gap")  # fmt: skip
            self.events.append(
                EventRow(
                    "discontinuity",
                    device,
                    name,
                    as_time(at_us),
                    int(reset),
                    int(spike),
                    f"{names}: {detail}",
                )
            )

    def device_reloads(
        self, device: DeviceId, name: str, monitored: list[tuple[InterfaceId, str]],
        interval_s: int = DEFAULT_INTERVAL_S,
    ) -> None:  # fmt: skip
        """Find a device's reloads and confirm each monitored interface reset at them.

        Args:
            device: The device.
            name: Device name.
            monitored: Its monitored interfaces and their names.
            interval_s: Its poll interval.

        Returns:
            None

        Raises:
            StorageError: If the store is unavailable.
        """
        for reload_us in self._reload_times(device):
            self.reloads += 1
            verdicts = Counter(
                reload_check(self.backend.acceptance, i, self.collector, reload_us, interval_s)
                for i, _ in monitored
            )
            self.spikes += verdicts["spike"]
            detail = (f"{verdicts['reset']} interfaces reset, {verdicts['spike']} carried a "
                      f"rate across the reload, {verdicts['unsampled']} not sampled within "
                      "10 minutes")  # fmt: skip
            self.events.append(
                EventRow(
                    "reload",
                    name,
                    "",
                    as_time(reload_us),
                    verdicts["reset"],
                    verdicts["spike"],
                    detail,
                )
            )

    def _reload_times(self, device: DeviceId) -> list[int]:
        """Return the times SNMP detected the device's agent restarting in the event window.

        Args:
            device: The device.

        Returns:
            list[int]: Detection times, oldest first.

        Raises:
            StorageError: If the store is unavailable.
        """
        start, end = self.events_window
        split = start if self.frontier_us is None else min(max(self.frontier_us, start), end)
        spans = [(split, end)] if split < end else []
        if split > start:
            rolled = self.backend.acceptance.bucket_marks(
                ObservationType.ICMP,
                device,
                self.collector,
                TimeWindow(as_time(start), as_time(split)),
            )
            spans = [(b.start_us, b.start_us + BUCKET_US) for b in rolled.items
                     if b.quality & Quality.BASELINE_UPTIME_RESET] + spans  # fmt: skip
        found: list[int] = []
        query = self.backend.query
        for low, high in spans:
            window = TimeWindow(as_time(low), as_time(high))
            obs, cut = pages(lambda p, w=window: query.read_raw(
                ObservationType.SNMP_DEVICE, device, w, p, collector_id=self.collector),
                MAX_SNMP_READ)  # fmt: skip
            self.truncated += cut
            found.extend(
                to_epoch_us(o.timestamp)
                for o in obs
                if isinstance(o, SnmpDeviceObservation)
                and o.uptime_continuity is UptimeContinuity.RESET_DETECTED
            )
        return sorted(found)

    def result(self, name: str, limit: int, notes: tuple[str, ...]) -> CollectorCoverage:
        """Build the collector's section.

        Args:
            name: Collector name.
            limit: Rows to list.
            notes: Limits of the evidence found by the caller.

        Returns:
            CollectorCoverage: Counts, rows, checks, and verdict.

        Raises:
            None
        """
        ranges = tuple(
            RangeCount(label, family, c["data"], c["gap"], c["pending"], c["missing"])
            for label, _span in COVERAGE_RANGES for family, _bits in FAMILIES
            for c in [self.counts.get((label, family), Counter())]
        )  # fmt: skip
        empty = sum(1 for r in self.rows if r.missing)
        graphs = Check("graphs", Verdict.PASS if not empty else Verdict.FAIL,
                       f"{empty} of {self.interfaces} monitored interfaces have an empty graph "
                       "at some range; 0 allowed")  # fmt: skip
        spikes = Check("spikes", Verdict.PASS if not self.spikes else Verdict.FAIL,
                       f"{self.spikes} spikes across {self.reloads} reloads and "
                       f"{self.discontinuities} discontinuities; 0 allowed")  # fmt: skip
        judged = self.interfaces > 0 and not self.truncated
        evidence = Check("evidence", Verdict.PASS if judged else Verdict.INCOMPLETE,
                         f"{self.interfaces} monitored interfaces judged; "
                         f"{self.truncated} reads cut short by a bound")  # fmt: skip
        checks = (graphs, spikes, evidence)
        rows = sorted(self.rows, key=lambda r: (not r.missing, r.device, r.interface))
        events = sorted(self.events, key=lambda e: (not e.spikes, e.at, e.device))
        return CollectorCoverage(name, self.interfaces, ranges, tuple(rows[:limit]),
                                 tuple(events[:limit]), self.discontinuities, self.reloads,
                                 self.spikes, self.over_capacity, notes, checks,
                                 combine(c.verdict for c in checks))  # fmt: skip
