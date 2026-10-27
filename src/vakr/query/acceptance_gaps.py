"""MVP-ACC-10 for one collector: every period without data, and the evidence that explains it.

``GapScan`` walks the inventory device by device. A device's slots whose
poll produced no evidence are device gaps, classified by
``acceptance_slots.device_cause``; they imply that its ICMP target and
interfaces had no data too, so those subjects list only the slots where
the poll ran and still left them without a sample
(``acceptance_subjects``), classified by ``acceptance_causes``. Interface
gaps of one device with the same stretch and cause share one row. The
collector passes only when no gap is unexplained and nothing was cut short.

Dependencies:
    vakr.domain, vakr.ports.telemetry: IDs, observations, the acceptance port.
    vakr.query.acceptance_*: Slots, subjects, causes, evidence, views.
    vakr.query.snapshot: Bounded paging.

Safety:
    Read-only; every read is one subject and one bounded window.

Example:
    >>> GapScan.__name__
    'GapScan'
"""

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from vakr.domain.enums import InterfaceLifecycle, ObservationType
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry.acceptance import AcceptanceBackend
from vakr.query.acceptance_causes import (
    CAUSE_TEXT,
    InterfaceEvidence,
    Run,
    icmp_cause,
    interface_cause,
    refine,
    runs,
)
from vakr.query.acceptance_evidence import CollectorEvidence, DeviceEvidence
from vakr.query.acceptance_limits import US
from vakr.query.acceptance_slots import (
    DEVICE_GAP_KINDS,
    GapCause,
    Slot,
    device_cause,
    device_slots,
)
from vakr.query.acceptance_subjects import (
    as_time,
    bucket_samples,
    down_slots,
    expected_per_bucket,
    missing_slots,
    snmp_by_slot,
    suspect_spans,
)
from vakr.query.acceptance_views import Check, CollectorGaps, GapRow, Verdict, combine


@dataclass(frozen=True, slots=True)
class DeviceInput:
    """One device to judge, with its inventory evidence.

    Attributes:
        id: The device.
        name: Its name.
        evidence: What the inventory says about it.
        interfaces: Its interfaces, active or not, with their evidence.
        icmp: Whether its strategy sends ICMP (not ``snmp_only``).
        snmp: Whether its strategy polls SNMP (not ``icmp_only``).
    """

    id: DeviceId
    name: str
    evidence: DeviceEvidence
    interfaces: tuple[tuple[UUID, InterfaceEvidence], ...]
    icmp: bool = True
    snmp: bool = True


@dataclass(slots=True)
class GapScan:
    """Gap rows of one collector, accumulated device by device.

    Attributes:
        backend: The telemetry backend.
        collector: The collector judged.
        evidence: Its activity, refusals, and losses.
        window: ``(start_us, end_us)``, the start aligned to a bucket.
        frontier_us: The rollup frontier, or ``None`` without rollups.
        rows: Every gap row found.
        by_cause: Missing subject-slots by cause.
        devices: Devices judged.
        interfaces: Interfaces judged.
        truncated: Reads cut short by a bound.
    """

    backend: AcceptanceBackend
    collector: CollectorId
    evidence: CollectorEvidence
    window: tuple[int, int]
    frontier_us: int | None
    rows: list[GapRow] = field(default_factory=list)
    by_cause: Counter[str] = field(default_factory=Counter)
    devices: int = 0
    interfaces: int = 0
    truncated: int = 0

    def device(self, d: DeviceInput) -> None:
        """Judge one device, its ICMP target, and its interfaces.

        Args:
            d: The device.

        Returns:
            None

        Raises:
            StorageError: If the store is unavailable.
        """
        window = TimeWindow(as_time(self.window[0]), as_time(self.window[1]))
        marks = self.backend.acceptance.poll_marks(d.id, self.collector, window)
        if not marks.items and d.evidence.offline_since(self.window[0]):
            return  # not scheduled at any time in the window
        self.devices += 1
        self.truncated += marks.truncated
        slots = device_slots(marks.items, self.window)
        gaps = [i for i, s in enumerate(slots) if s.kind in DEVICE_GAP_KINDS]
        found = runs(gaps, lambda i: device_cause(slots[i], self.evidence, d.evidence))
        for run in found:
            self._row("device", d, (), slots, run)
        expected = expected_per_bucket(slots)
        if d.icmp:
            missing = self._missing(ObservationType.ICMP, d.id, slots, expected, active=True)
            for run in runs(missing or [], lambda i: icmp_cause(slots[i])):
                self._row("icmp", d, (), slots, run)
        if d.snmp:
            self._interfaces(d, slots, expected)

    def _missing(
        self, kind: ObservationType, subject: UUID, slots: list[Slot], expected: dict[int, int],
        *, active: bool,
    ) -> list[int] | None:  # fmt: skip
        """Return a subject's ran slots without a sample, or ``None`` when it is not judged.

        Args:
            kind: ``ICMP`` or ``INTERFACE``.
            subject: Device or interface.
            slots: The device's timeline.
            expected: Ran slots per bucket.
            active: Judge the subject even without any sample in the window.

        Returns:
            list[int] | None: Slot indices; ``None`` for an inactive subject with no samples.

        Raises:
            StorageError: If the store is unavailable.
        """
        query = self.backend.acceptance
        counts, cut = bucket_samples(query, kind, subject, self.collector, self.window,
                                     self.frontier_us)  # fmt: skip
        if not active and not any(counts.values()):
            return None
        spans = suspect_spans(expected, counts)
        missing, cut_again = missing_slots(query, kind, subject, self.collector, slots, spans)
        self.truncated += cut + cut_again
        return missing

    def _interfaces(self, d: DeviceInput, slots: list[Slot], expected: dict[int, int]) -> None:
        """Judge a device's interfaces and add one row per shared stretch and cause.

        Args:
            d: The device.
            slots: Its timeline.
            expected: Ran slots per bucket.

        Returns:
            None

        Raises:
            StorageError: If the store is unavailable.
        """
        found: dict[UUID, list[int]] = {}
        for iface_id, iface in d.interfaces:
            active = iface.lifecycle is InterfaceLifecycle.ACTIVE
            missing = self._missing(ObservationType.INTERFACE, iface_id, slots, expected,
                                    active=active)  # fmt: skip
            if missing is not None:
                self.interfaces += 1
                found[iface_id] = missing
        indices = sorted({i for m in found.values() for i in m})
        snmp, cut = snmp_by_slot(self.backend.query, d.id, self.collector, slots, indices)
        self.truncated += cut
        grouped: dict[tuple[int, int, GapCause], list[str]] = {}
        evidence = dict(d.interfaces)
        for iface_id, missing in found.items():
            iface = evidence[iface_id]
            down = down_slots(self.backend.acceptance, iface_id, self.collector, slots, missing,
                              self.window)  # fmt: skip

            def cause_of(i: int, f: InterfaceEvidence = iface, d: set[int] = down) -> GapCause:
                """Explain one missing slot of this interface.

                Args:
                    i: Slot index.
                    f: The interface's evidence.
                    d: Slots at which its status records say it was down.

                Returns:
                    GapCause: The cause.

                Raises:
                    None
                """
                return interface_cause(slots[i], snmp.get(i), f, i in d)

            for run in runs(missing, cause_of):
                cause = refine(run, slots, d.evidence)
                grouped.setdefault((run.first, run.last, cause), []).append(iface.name)
        for (first, last, cause), names in sorted(grouped.items()):
            run = Run(first, last, last - first + 1, cause)
            self._row("interface", d, tuple(sorted(names)), slots, run)

    def _row(self, kind: str, d: DeviceInput, subjects: tuple[str, ...], slots: list[Slot],
             run: Run) -> None:  # fmt: skip
        """Record one gap row and count its subject-slots by cause.

        Args:
            kind: ``device``, ``icmp``, or ``interface``.
            d: The device.
            subjects: Interface names, for interface rows.
            slots: The device's timeline.
            run: The run.

        Returns:
            None

        Raises:
            None
        """
        cause = refine(run, slots, d.evidence)
        last = slots[run.last]
        row = GapRow(kind, d.name, subjects, as_time(slots[run.first].at_us),
                     as_time(last.at_us + last.interval_s * US), run.slots, cause.value,
                     cause is not GapCause.UNEXPLAINED, CAUSE_TEXT[cause])  # fmt: skip
        self.rows.append(row)
        self.by_cause[cause.value] += run.slots * max(1, len(subjects))

    def result(self, name: str, limit: int, notes: tuple[str, ...]) -> CollectorGaps:
        """Build the collector's section, unexplained rows first.

        Args:
            name: Collector name.
            limit: Rows to list.
            notes: Limits of the evidence found by the caller.

        Returns:
            CollectorGaps: Rows, counts, checks, and verdict.

        Raises:
            None
        """
        ordered = sorted(self.rows, key=lambda r: (r.explained, r.start, r.kind, r.device))
        unexplained = sum(not r.explained for r in self.rows)
        gaps = Check(
            "gaps",
            Verdict.PASS if not unexplained else Verdict.FAIL,
            f"{unexplained} of {len(self.rows)} gap rows have no explanation; 0 allowed",
        )
        judged = self.devices > 0 and not self.truncated
        detail = (f"{self.devices} devices and {self.interfaces} interfaces judged; "
                  f"{self.truncated} reads cut short by a bound")  # fmt: skip
        evidence = Check("evidence", Verdict.PASS if judged else Verdict.INCOMPLETE, detail)
        checks = (gaps, evidence)
        return CollectorGaps(name, self.devices, self.interfaces, tuple(ordered[:limit]),
                             len(self.rows), dict(sorted(self.by_cause.items())), unexplained,
                             notes, checks, combine(c.verdict for c in checks))  # fmt: skip
