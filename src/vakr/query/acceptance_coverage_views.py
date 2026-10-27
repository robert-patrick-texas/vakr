"""View models of the MVP-ACC-02 coverage report (E.35).

For each monitored interface the report says whether its traffic, packet,
and error graphs hold data at the 1 h, 24 h, 7 d, 30 d, and 400 d ranges,
and how each device reload and counter discontinuity in the event window
was drawn: a reset (no rate, so the graph shows a gap) or a spike.

Dependencies:
    dataclasses, datetime: Immutable views.
    vakr.query.acceptance_views: Checks and verdicts.

Safety:
    Views hold names, counts, and times, never credentials or addresses.

Example:
    >>> RangeCount("1h", "traffic", 3, 0, 0, 1).missing
    1
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.query.acceptance_views import Check, Verdict


@dataclass(frozen=True, slots=True)
class RangeCount:
    """How many monitored interfaces had each state for one range and family (MVP-ACC-02).

    Attributes:
        range: Range label, such as ``24h``.
        family: ``traffic``, ``packets``, or ``errors``.
        data: Interfaces whose graph holds data in the range.
        gap: Interfaces whose graph shows only a gap there (``vakr acceptance gaps``).
        pending: Interfaces whose rollups are not built yet (400 d).
        missing: Interfaces with no value of the family at all: the graph is empty.
    """

    range: str
    family: str
    data: int
    gap: int
    pending: int
    missing: int


@dataclass(frozen=True, slots=True)
class CoverageRow:
    """One monitored interface whose graphs are not complete at every range.

    Attributes:
        device: Device name.
        interface: Interface name.
        missing: ``range/family`` pairs with no value at all.
        gaps: ``range/family`` pairs that show only a gap.
        pending: ``range/family`` pairs waiting for rollups.
    """

    device: str
    interface: str
    missing: tuple[str, ...]
    gaps: tuple[str, ...]
    pending: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EventRow:
    """One reload or counter discontinuity and how the graph handled it (MVP-ACC-02).

    Attributes:
        kind: ``reload`` or ``discontinuity``.
        device: Device name.
        interface: Interface name, or empty for a reload row.
        at: Time of the event.
        resets: Interfaces (or samples) that reset: no rate, so the graph shows a gap.
        spikes: Samples with a rate across the event, or over capacity just after it.
        detail: One operator sentence.
    """

    kind: str
    device: str
    interface: str
    at: datetime
    resets: int
    spikes: int
    detail: str


@dataclass(frozen=True, slots=True)
class CollectorCoverage:
    """MVP-ACC-02 for one collector.

    Attributes:
        collector: Collector name.
        interfaces: Monitored interfaces judged.
        ranges: Counts per range and family.
        rows: Interfaces that are not complete, at most the row limit.
        events: Reloads and discontinuities in the event window, at most the row limit.
        discontinuities: Discontinuity samples found.
        reloads: Device reloads found.
        spikes: Spikes found across reloads or discontinuities.
        over_capacity: Samples over the plausibility bound not following an event.
        notes: Limits of the evidence.
        checks: The judged criteria.
        verdict: The collector's verdict.
    """

    collector: str
    interfaces: int
    ranges: tuple[RangeCount, ...]
    rows: tuple[CoverageRow, ...]
    events: tuple[EventRow, ...]
    discontinuities: int
    reloads: int
    spikes: int
    over_capacity: int
    notes: tuple[str, ...]
    checks: tuple[Check, ...]
    verdict: Verdict
