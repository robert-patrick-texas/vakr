"""Immutable, JSON-ready view models for history graphs (10.6, 12.6, API-006, UI-010).

A graph answer says everything a reader needs to trust it: the window and
the zone to draw it in, which source and bucket width produced each part
(native or rollup), whether the point cap cut it short, where data is
missing (``value=None``, never zero), and why values are absent or suspect
(markers). Values are in the base unit named by ``UnitView.base``; the
``display`` unit and ``divisor`` are a suggestion for the axis (SI network
units: ``kbit/s``, ``Mbit/s``, ``Gbit/s``). Each series is one collector's
evidence; series are never merged across collectors (MPOS-008).

``vakr.query.jsonable.to_jsonable`` turns any of these into plain JSON
types (UUIDs as text, times as ISO 8601 UTC with ``Z``), so the API can
return them as they are.

Dependencies:
    dataclasses, datetime, enum, uuid: Records and vocabularies.
    vakr.domain.ids: Collector IDs.

Safety:
    Views hold names, identifiers, and measurements; never credentials.

Example:
    >>> UnitView("bit/s", "Mbit/s", 1e6).divisor
    1000000.0
    >>> MarkerKind.RESTART.value
    'restart'
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from vakr.domain.ids import CollectorId


class MarkerKind(StrEnum):
    """Why part of a series is missing or needs context (12.6, RATE-004)."""

    GAP = "gap"
    BASELINE = "baseline"
    RESTART = "restart"
    DISCONTINUITY = "discontinuity"
    PARTIAL = "partial"
    COLLECTOR = "collector"
    OVER_CAPACITY = "over_capacity"


@dataclass(frozen=True, slots=True)
class UnitView:
    """The unit of a graph's values and the suggested display unit.

    Attributes:
        base: Unit of every value in the view, such as ``bit/s`` or ``us``.
        display: Suggested axis unit, such as ``Mbit/s`` or ``ms``.
        divisor: ``display value = base value / divisor``.
    """

    base: str
    display: str
    divisor: float


@dataclass(frozen=True, slots=True)
class PointView:
    """One point: a value or a gap, with its envelope and quality flags.

    Attributes:
        t: Sample time, or bucket start for bucketed points (UTC).
        value: The value (bucket mean), or ``None`` for a gap.
        low: Bucket minimum when bucketed, else ``None``.
        high: Bucket maximum when bucketed, else ``None``.
        flags: Quality flag names, such as ``baseline_uptime_reset``.
    """

    t: datetime
    value: float | None
    low: float | None = None
    high: float | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolutionView:
    """Which source produced ``[start, end)`` of a series, and how finely (STATE-005).

    Attributes:
        source: ``native`` or ``rollup``.
        start: Span start (UTC).
        end: Span end (UTC).
        bucket_seconds: Bucket width; ``None`` means one point per observation.
    """

    source: str
    start: datetime
    end: datetime
    bucket_seconds: int | None


@dataclass(frozen=True, slots=True)
class MarkerView:
    """A stretch of a series that is missing or carries context (12.6).

    Attributes:
        kind: What happened.
        start: Start (UTC).
        end: End (UTC), after ``start``.
        text: Operator words.
    """

    kind: MarkerKind
    start: datetime
    end: datetime
    text: str


@dataclass(frozen=True, slots=True)
class SeriesView:
    """One metric from one collector over the window.

    Attributes:
        metric: Metric key, such as ``in_bps``.
        label: Legend text, such as ``Inbound (east)``.
        collector_id: The one collector whose evidence this is (STATE-004).
        position: That collector's position label.
        points: Points in time order, gaps included.
        resolutions: Spans covering the window, oldest first.
        truncated: True when the point cap cut off later points.
        gaps: Points with no value, including inserted gap points (E.19).
        markers: Gaps and quality context, oldest first.
    """

    metric: str
    label: str
    collector_id: CollectorId
    position: str
    points: tuple[PointView, ...]
    resolutions: tuple[ResolutionView, ...]
    truncated: bool
    gaps: int
    markers: tuple[MarkerView, ...]


@dataclass(frozen=True, slots=True)
class ReferenceView:
    """A horizontal reference line, such as capacity or the contracted rate (12.6).

    Attributes:
        kind: ``capacity`` or ``contracted``.
        value: Value in the graph's base unit.
        label: Operator words, such as ``Capacity 1 Gbit/s (ifHighSpeed)``.
    """

    kind: str
    value: float
    label: str


@dataclass(frozen=True, slots=True)
class WindowView:
    """The time window a view covers and the zone to show it in (12.6, API-006).

    Attributes:
        start: Window start (UTC, inclusive).
        end: Window end (UTC, exclusive).
        preset: The preset used, such as ``24h``; ``None`` for a custom range.
        timezone: Display timezone (IANA name; TIME-002, TIME-003).
        site_timezone: The subject's site zone, when it has one.
    """

    start: datetime
    end: datetime
    preset: str | None
    timezone: str
    site_timezone: str | None = None


@dataclass(frozen=True, slots=True)
class GraphView:
    """An interface graph: traffic, packets, errors, or utilization (12.5, 12.6).

    Attributes:
        kind: ``traffic``, ``packets``, ``errors``, or ``utilization``.
        subject_id: The interface.
        subject: Display text, such as ``core1 Gi0/0/1``.
        window: Window and zone.
        source: Requested source: ``auto``, ``native``, or ``rollup``.
        unit: Unit of every value.
        series: One per metric and collector; never merged (MPOS-008).
        references: Capacity and contracted-rate lines, when known.
        truncated: True when any series hit the point cap.
        available: False when the graph cannot be drawn at all.
        reason: Why not, in operator words.
        generated_at: When the view was computed (UTC).
        notes: Standing explanations.
    """

    kind: str
    subject_id: UUID
    subject: str
    window: WindowView
    source: str
    unit: UnitView
    series: tuple[SeriesView, ...]
    references: tuple[ReferenceView, ...]
    truncated: bool
    available: bool
    reason: str | None
    generated_at: datetime
    notes: tuple[str, ...] = ()
