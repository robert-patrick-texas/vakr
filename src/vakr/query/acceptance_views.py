"""View models of the MVP acceptance reports (MVP-ACC-01, MVP-ACC-02, MVP-ACC-10, E.35).

Each report is per collector, because each position is accepted on its
own evidence and positions are never merged (MPOS-008). A report ends in a
``Verdict``: ``pass`` only when every check passed, ``fail`` when one
failed, and ``incomplete`` when the evidence could not judge a check (too
few days, nothing to judge, a read cut short by a bound). ``to_jsonable``
serialises them for ``--json``.

Dependencies:
    dataclasses, datetime, enum: Immutable views.

Safety:
    Views hold names, counts, and times, never credentials or addresses.

Example:
    >>> combine([Verdict.PASS, Verdict.INCOMPLETE]).value
    'incomplete'
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Verdict(StrEnum):
    """How a check, a collector, or a report came out."""

    PASS = "pass"  # noqa: S105 - a verdict, not a credential
    FAIL = "fail"
    INCOMPLETE = "incomplete"


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    """Combine verdicts: any failure fails, else anything unjudged is incomplete.

    Args:
        verdicts: Verdicts of the parts.

    Returns:
        Verdict: ``incomplete`` when there are no parts.

    Raises:
        None
    """
    seen = set(verdicts)
    if Verdict.FAIL in seen:
        return Verdict.FAIL
    if Verdict.INCOMPLETE in seen or not seen:
        return Verdict.INCOMPLETE
    return Verdict.PASS


@dataclass(frozen=True, slots=True)
class Check:
    """One judged criterion.

    Attributes:
        name: Stable short name, such as ``on_time``.
        verdict: The outcome.
        detail: One operator sentence with the measured value and the threshold.
    """

    name: str
    verdict: Verdict
    detail: str


@dataclass(frozen=True, slots=True)
class DayDrift:
    """One UTC day of start offsets and slot phases (MVP-ACC-01 drift).

    Attributes:
        day: ``YYYY-MM-DD``.
        polls: Polls that started, of healthy devices.
        median_offset_ms: Median start offset (start minus schedule).
        median_phase_ms: Median slot phase error against each device's first slot.
    """

    day: str
    polls: int
    median_offset_ms: float | None
    median_phase_ms: float | None


@dataclass(frozen=True, slots=True)
class Overlap:
    """Two polls of one device that ran at the same time.

    Attributes:
        device: Device name.
        scheduled_at: The later poll's slot.
        overlap_ms: How long it started before the earlier poll ended.
    """

    device: str
    scheduled_at: datetime
    overlap_ms: float


@dataclass(frozen=True, slots=True)
class CollectorSchedule:
    """MVP-ACC-01 for one collector.

    Attributes:
        collector: Collector name.
        devices: Devices with poll records in the window.
        interfaces: Interfaces sampled, from the newest health report, if any.
        records: Poll records read.
        healthy: Records that count toward the on-time share (E.35 definition).
        on_time: Healthy-device polls that started within the tolerance.
        late: Healthy-device polls that started outside it.
        missed: Healthy-device slots that never started.
        excluded: Records left out, by reason.
        on_time_share: ``on_time / healthy``, if any.
        offset_p50_ms: Median start offset of healthy-device polls.
        offset_p99_ms: 99th percentile of the absolute start offset.
        offset_max_ms: Largest absolute start offset.
        overlaps: Same-device polls that ran at once.
        duplicates: Slots recorded twice.
        overlap_rows: The first overlaps.
        slots_without_record: Slots with no record at all (see ``vakr acceptance gaps``).
        intervals: Records by effective interval in seconds.
        days: Per-day drift figures.
        offset_slope_ms_per_day: Slope of the daily median start offset.
        phase_slope_ms_per_day: Slope of the daily median slot phase.
        checks: The judged criteria.
        verdict: The collector's verdict.
    """

    collector: str
    devices: int
    interfaces: int | None
    records: int
    healthy: int
    on_time: int
    late: int
    missed: int
    excluded: dict[str, int]
    on_time_share: float | None
    offset_p50_ms: float | None
    offset_p99_ms: float | None
    offset_max_ms: float | None
    overlaps: int
    duplicates: int
    overlap_rows: tuple[Overlap, ...]
    slots_without_record: int
    intervals: dict[int, int]
    days: tuple[DayDrift, ...]
    offset_slope_ms_per_day: float | None
    phase_slope_ms_per_day: float | None
    checks: tuple[Check, ...]
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class GapRow:
    """One period without data, with the evidence that explains it (MVP-ACC-10).

    Attributes:
        kind: ``device``, ``icmp``, or ``interface``.
        device: Device name.
        subjects: Interface names for interface rows (several share one row), else empty.
        start: First missing slot.
        end: End of the last missing slot.
        slots: Missing slots per subject.
        cause: A ``GapCause`` value.
        explained: Whether the cause is evidence, not ``unexplained``.
        detail: One operator sentence.
    """

    kind: str
    device: str
    subjects: tuple[str, ...]
    start: datetime
    end: datetime
    slots: int
    cause: str
    explained: bool
    detail: str


@dataclass(frozen=True, slots=True)
class CollectorGaps:
    """MVP-ACC-10 for one collector.

    Attributes:
        collector: Collector name.
        devices: Devices judged.
        interfaces: Interfaces judged.
        rows: Gap rows listed, oldest first, at most the row limit.
        rows_total: Gap rows found.
        by_cause: Missing subject-slots by cause, every row counted.
        unexplained: Gap rows without an explanation.
        notes: Limits of the evidence, such as rollups behind.
        checks: The judged criteria.
        verdict: The collector's verdict.
    """

    collector: str
    devices: int
    interfaces: int
    rows: tuple[GapRow, ...]
    rows_total: int
    by_cause: dict[str, int]
    unexplained: int
    notes: tuple[str, ...]
    checks: tuple[Check, ...]
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    """One report: its window, one section per collector, and the overall verdict.

    Attributes:
        report: ``schedule``, ``gaps``, or ``coverage``.
        criterion: The MVP acceptance criterion judged, such as ``MVP-ACC-01``.
        generated_at: When the report was computed (UTC).
        window_start: Start of the judged window.
        window_end: End of the judged window.
        collectors: Per-collector sections: ``CollectorSchedule``, ``CollectorGaps``, or
            ``acceptance_coverage_views.CollectorCoverage``.
        verdict: Combined verdict.
    """

    report: str
    criterion: str
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    collectors: tuple[object, ...]
    verdict: Verdict
