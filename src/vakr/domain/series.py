"""Time series read models: windows, metrics, points, and resolution (10.6, STORE-012).

A ``Series`` answers "what was this metric for this subject, from this
collector, in this window". Three rules make it honest:

- every query has a bounded ``TimeWindow`` and a ``max_points`` cap
  (API-006); ``truncated`` says when the cap cut the answer short;
- a gap is a point whose value is ``None``, never a missing point and never
  zero: an observation without a value stays in the series, and every rollup
  bucket in the window appears even when it holds no data (STORE-012);
- ``resolutions`` discloses which tier produced each part of the window and
  at what bucket width, so a graph spanning the native/rollup boundary says
  so instead of silently mixing them (STATE-005, DB-006).

Dependencies:
    vakr.domain.observations: Metric values come from observation fields.
    vakr.domain.checks, timestamps: Shared invariant checks.

Safety:
    Points are ordered and unique by timestamp; construction refuses
    duplicates, so a boundary query cannot double-count a sample.

Example:
    >>> Metric.LOSS_PCT.observation_type, Metric.IN_BPS.observation_type
    (<ObservationType.ICMP: 'icmp'>, <ObservationType.INTERFACE: 'interface'>)
"""

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from vakr.domain.checks import require_bool, require_enum, require_int, require_uuid
from vakr.domain.enums import ObservationType, SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.observations import IcmpObservation, InterfaceObservation
from vakr.domain.quality import Quality
from vakr.domain.timestamps import require_utc

MAX_SERIES_POINTS = 1200
"""Most points one series query returns (blueprint 10.6)."""


class Metric(StrEnum):
    """A graphable metric; the value names its API and storage key."""

    IN_BPS = "in_bps"
    OUT_BPS = "out_bps"
    IN_PPS = "in_pps"
    OUT_PPS = "out_pps"
    IN_ERRORS = "in_errors"
    OUT_ERRORS = "out_errors"
    RTT_MEDIAN_US = "rtt_median_us"
    LOSS_PCT = "loss_pct"
    JITTER_US = "jitter_us"

    @property
    def observation_type(self) -> ObservationType:
        """Return the observation type this metric is read from.

        Args:
            None

        Returns:
            ObservationType: ``ICMP`` for latency and loss, else ``INTERFACE``.

        Raises:
            None
        """
        return ObservationType.ICMP if self in _ICMP_METRICS else ObservationType.INTERFACE

    @property
    def field(self) -> str:
        """Return the observation attribute that holds this metric.

        Error metrics are per-second rates (``in_error_rate``), and loss is
        derived from probe counts (``IcmpObservation.loss_pct``).

        Args:
            None

        Returns:
            str: The attribute name.

        Raises:
            None
        """
        return _FIELDS.get(self, self.value)


_ICMP_METRICS = frozenset({Metric.RTT_MEDIAN_US, Metric.LOSS_PCT, Metric.JITTER_US})
_FIELDS = {
    Metric.IN_ERRORS: "in_error_rate",
    Metric.OUT_ERRORS: "out_error_rate",
    Metric.RTT_MEDIAN_US: "median_rtt_us",
}


def metric_value(
    observation: IcmpObservation | InterfaceObservation, metric: Metric
) -> float | None:
    """Read a metric from an observation, keeping ``None`` as ``None``.

    Args:
        observation: An ICMP or interface observation.
        metric: A metric of the observation's type.

    Returns:
        float | None: The value as a float, or ``None`` when missing.

    Raises:
        ValidationError: If the metric belongs to the other observation type.
    """
    expected = (
        IcmpObservation if metric.observation_type is ObservationType.ICMP else InterfaceObservation
    )
    if not isinstance(observation, expected):
        raise ValidationError(f"{metric} is not read from {type(observation).__name__}")
    value = getattr(observation, metric.field)
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A bounded, half-open UTC time range ``[start, end)`` (API-006).

    Attributes:
        start: Inclusive start (UTC).
        end: Exclusive end (UTC), after ``start``.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Validate the window.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If either end is not UTC or ``end <= start``.
        """
        require_utc(self.start, "start")
        require_utc(self.end, "end")
        if self.end <= self.start:
            raise ValidationError("a time window must end after it starts", field="end")

    @property
    def duration(self) -> timedelta:
        """Return the window length.

        Args:
            None

        Returns:
            timedelta: ``end - start``.

        Raises:
            None
        """
        return self.end - self.start

    def contains(self, moment: datetime) -> bool:
        """Tell whether ``moment`` lies in ``[start, end)``.

        Args:
            moment: Aware UTC datetime.

        Returns:
            bool: ``True`` when inside the window.

        Raises:
            None
        """
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One point: a value or an explicit gap, with its quality (STORE-012).

    Attributes:
        timestamp: Sample time, or bucket start for bucketed points (UTC).
        value: The metric, or ``None`` for a gap or missing value.
        quality: Quality of the underlying sample or union over the bucket.
        minimum: Bucket minimum for bucketed points, else ``None``.
        maximum: Bucket maximum for bucketed points, else ``None``.
    """

    timestamp: datetime
    value: float | None
    quality: Quality = Quality.NONE
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class SeriesResolution:
    """Which tier produced ``[start, end)`` of a series, and how finely (STATE-005).

    Attributes:
        source: ``NATIVE`` observations or ``ROLLUP`` buckets.
        start: Inclusive start of the span (UTC).
        end: Exclusive end of the span (UTC).
        bucket_seconds: Bucket width, or ``None`` for native sample times.
    """

    source: SeriesSource
    start: datetime
    end: datetime
    bucket_seconds: int | None = None

    def __post_init__(self) -> None:
        """Validate the span.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the span is empty or a rollup has no bucket width.
        """
        require_enum(self.source, SeriesSource, "source")
        TimeWindow(self.start, self.end)
        require_int(self.bucket_seconds, "bucket_seconds", minimum=1, optional=True)
        if self.source is SeriesSource.ROLLUP and self.bucket_seconds is None:
            raise ValidationError("a rollup span needs bucket_seconds", field="bucket_seconds")


@dataclass(frozen=True, slots=True)
class Series:
    """A metric over a window, with gaps, quality, and disclosed resolution.

    Attributes:
        subject_id: The interface (interface metrics) or device (ICMP metrics).
        collector_id: The one collector whose evidence this is (STATE-004).
        metric: The metric.
        window: The requested window.
        points: Points in strictly increasing time order, inside ``window``.
        resolutions: Contiguous spans covering ``window``, oldest first.
        truncated: ``True`` when ``max_points`` cut off later points.
    """

    subject_id: UUID
    collector_id: CollectorId
    metric: Metric
    window: TimeWindow
    points: tuple[SeriesPoint, ...]
    resolutions: tuple[SeriesResolution, ...]
    truncated: bool

    def __post_init__(self) -> None:
        """Validate ordering, uniqueness, and span coverage.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If points are unordered, duplicated, or outside
                the window, or the spans do not tile the window.
        """
        require_uuid(self.subject_id, "subject_id")
        require_uuid(self.collector_id, "collector_id")
        require_enum(self.metric, Metric, "metric")
        require_bool(self.truncated, "truncated")
        times = [p.timestamp for p in self.points]
        if any(a >= b for a, b in itertools.pairwise(times)):
            raise ValidationError("series points must be strictly increasing", field="points")
        if times and not (self.window.contains(times[0]) and self.window.contains(times[-1])):
            raise ValidationError("series points must lie inside the window", field="points")
        bounds = [self.window.start]
        for span in self.resolutions:
            if span.start != bounds[-1]:
                raise ValidationError("resolution spans must be contiguous", field="resolutions")
            bounds.append(span.end)
        if bounds[-1] != self.window.end:
            raise ValidationError("resolution spans must cover the window", field="resolutions")
