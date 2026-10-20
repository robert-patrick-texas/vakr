"""Backend-neutral series assembly: spans, gap points, deduplication, truncation.

Every telemetry adapter fetches native samples and rollup buckets its own
way (SQL, ClickHouse, a dict), then hands them to ``assemble_series`` so the
rules that make series comparable across backends live in one place
(STORE-012, STORE-013, DB-004, DB-006):

1. ``resolution_spans`` splits the window at the native horizon: rollups
   before it (when the backend has rollups), native from it on.
2. In a rollup span every UTC-aligned bucket whose start lies in the span
   yields one point: the bucket's mean with its min/max envelope, or a
   ``None`` gap when no bucket was stored.
3. In a native span every sample yields one point, ``None`` values kept;
   samples outside the span are dropped, so nothing appears twice at the
   boundary; equal timestamps keep the larger observation UUID.
4. At most ``max_points`` points are kept, oldest first; ``truncated`` says
   whether more existed.

Dependencies:
    vakr.domain.series, rollups: Read models and bucket alignment.

Safety:
    Pure functions; inputs are never mutated.

Example:
    >>> from datetime import UTC, datetime
    >>> w = TimeWindow(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    >>> spans = resolution_spans(w, datetime(2026, 1, 1, 6, tzinfo=UTC), rollups=True)
    >>> [s.source.value for s in spans]
    ['rollup', 'native']
"""

import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from vakr.domain.checks import require_int
from vakr.domain.enums import SeriesSource
from vakr.domain.ids import CollectorId
from vakr.domain.observations import IcmpObservation, InterfaceObservation
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket, bucket_start
from vakr.domain.series import (
    MAX_SERIES_POINTS,
    Metric,
    Series,
    SeriesPoint,
    SeriesResolution,
    TimeWindow,
    metric_value,
)


@dataclass(frozen=True, slots=True)
class NativeSample:
    """The parts of one native observation a series needs.

    Attributes:
        timestamp: Observation time (UTC).
        observation_id: Observation UUID, the tie-breaker on equal timestamps.
        value: Metric value, or ``None``.
        quality: Observation quality flags.
    """

    timestamp: datetime
    observation_id: UUID
    value: float | None
    quality: Quality


def native_sample(
    observation: IcmpObservation | InterfaceObservation, metric: Metric
) -> NativeSample:
    """Extract a ``NativeSample`` from an observation.

    Args:
        observation: An ICMP or interface observation.
        metric: A metric of the observation's type.

    Returns:
        NativeSample: The sample.

    Raises:
        ValidationError: If the metric belongs to the other observation type.
    """
    value = metric_value(observation, metric)
    return NativeSample(
        observation.timestamp, observation.observation_id, value, observation.quality
    )


def resolution_spans(
    window: TimeWindow, horizon: datetime | None, *, rollups: bool
) -> tuple[SeriesResolution, ...]:
    """Split a window into rollup and native spans at the native horizon.

    Args:
        window: The requested window.
        horizon: Time before which native data was expired, or ``None``.
        rollups: Whether the backend serves rollups.

    Returns:
        tuple[SeriesResolution, ...]: One or two contiguous spans covering the window.

    Raises:
        None
    """
    native = SeriesSource.NATIVE
    if not rollups or horizon is None or horizon <= window.start:
        return (SeriesResolution(native, window.start, window.end),)
    rollup = SeriesSource.ROLLUP
    if horizon >= window.end:
        return (SeriesResolution(rollup, window.start, window.end, ROLLUP_BUCKET_SECONDS),)
    return (
        SeriesResolution(rollup, window.start, horizon, ROLLUP_BUCKET_SECONDS),
        SeriesResolution(native, horizon, window.end),
    )


def assemble_series(
    *,
    subject_id: UUID,
    collector_id: CollectorId,
    metric: Metric,
    window: TimeWindow,
    spans: tuple[SeriesResolution, ...],
    native: Iterable[NativeSample],
    rollups: Iterable[RollupBucket],
    max_points: int,
) -> Series:
    """Build a ``Series`` from fetched samples and buckets by the module's rules.

    Args:
        subject_id: Interface or device.
        collector_id: The collector.
        metric: The metric.
        window: The requested window.
        spans: Output of ``resolution_spans``.
        native: Native samples for the subject, collector, and metric, any order.
        rollups: Rollup buckets for the subject, collector, and metric, any order.
        max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.

    Returns:
        Series: The assembled series.

    Raises:
        ValidationError: If ``max_points`` is out of range.
    """
    require_int(max_points, "max_points", minimum=1, maximum=MAX_SERIES_POINTS)
    buckets = {b.bucket_start: b for b in rollups if b.bucket_seconds == ROLLUP_BUCKET_SECONDS}
    samples = _deduplicated(native)
    chained = itertools.chain.from_iterable(
        _bucket_points(span, buckets) if span.source is SeriesSource.ROLLUP
        else (_point(s) for s in samples if span.start <= s.timestamp < span.end)
        for span in spans
    )  # fmt: skip
    points = list(itertools.islice(chained, max_points + 1))
    return Series(
        subject_id=subject_id,
        collector_id=collector_id,
        metric=metric,
        window=window,
        points=tuple(points[:max_points]),
        resolutions=spans,
        truncated=len(points) > max_points,
    )


def _deduplicated(native: Iterable[NativeSample]) -> list[NativeSample]:
    """Sort samples by time, keeping the larger UUID on equal timestamps.

    Args:
        native: Samples in any order.

    Returns:
        list[NativeSample]: Samples with strictly increasing timestamps.

    Raises:
        None
    """
    ordered: list[NativeSample] = []
    for sample in sorted(native, key=lambda s: (s.timestamp, s.observation_id)):
        if ordered and ordered[-1].timestamp == sample.timestamp:
            ordered[-1] = sample
        else:
            ordered.append(sample)
    return ordered


def _point(sample: NativeSample) -> SeriesPoint:
    """Turn a native sample into a point.

    Args:
        sample: The sample.

    Returns:
        SeriesPoint: Value and quality, no envelope.

    Raises:
        None
    """
    return SeriesPoint(sample.timestamp, sample.value, sample.quality)


def _bucket_points(
    span: SeriesResolution, buckets: dict[datetime, RollupBucket]
) -> Iterator[SeriesPoint]:
    """Yield one point per aligned bucket start in the span, gaps included.

    Args:
        span: A rollup span.
        buckets: Stored buckets by start time.

    Returns:
        Iterator[SeriesPoint]: Points in time order.

    Raises:
        None
    """
    width = timedelta(seconds=ROLLUP_BUCKET_SECONDS)
    start = bucket_start(span.start)
    if start < span.start:
        start += width
    while start < span.end:
        b = buckets.get(start)
        if b is None:
            yield SeriesPoint(start, None)
        else:
            yield SeriesPoint(start, b.mean, b.quality, b.minimum, b.maximum)
        start += width
