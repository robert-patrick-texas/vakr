"""Rollup buckets: fixed-width summaries of native observations (10.5, RET-004).

Native one-minute data is kept for 30 days; after that, history lives in
15-minute buckets aligned to UTC (Appendix E.10). A bucket summarises one
metric for one subject from one collector. It keeps coverage counts, so a
bucket with three samples out of fifteen is visibly thin rather than looking
like a full quarter-hour, and it keeps min, max, and p95 so peaks survive
(RET-004, 10.6). Loss buckets must be computed from summed probe counts,
never by averaging percentages (RET-008); that is the rollup job's duty.

Dependencies:
    vakr.domain.series: ``Metric``.
    vakr.domain.checks, timestamps: Shared invariant checks.

Safety:
    A bucket with no valid sample has no aggregates: ``None``, not zero.

Example:
    >>> ROLLUP_BUCKET_SECONDS // 60
    15
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from vakr.domain.checks import require_enum, require_float, require_int, require_uuid
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.quality import Quality
from vakr.domain.series import Metric
from vakr.domain.timestamps import EPOCH, require_utc

ROLLUP_BUCKET_SECONDS = 900
"""Reference rollup bucket width, aligned to UTC (RET-003, Appendix E.10)."""

_AGGREGATES = ("minimum", "mean", "p95", "maximum")


def bucket_start(moment: datetime, bucket_seconds: int = ROLLUP_BUCKET_SECONDS) -> datetime:
    """Return the start of the UTC-aligned bucket containing ``moment``.

    Args:
        moment: Aware UTC datetime.
        bucket_seconds: Bucket width.

    Returns:
        datetime: The bucket start, at or before ``moment``.

    Raises:
        None
    """
    width = timedelta(seconds=bucket_seconds)
    return EPOCH + ((moment - EPOCH) // width) * width


@dataclass(frozen=True, slots=True)
class RollupBucket:
    """One metric summarised over one bucket for one subject and collector.

    The identity of a bucket is ``(subject_id, collector_id, metric,
    bucket_start, bucket_seconds)``; writing the same identity again replaces
    it, which makes an interrupted rollup job safe to re-run (RET-009).

    Attributes:
        subject_id: Interface (interface metrics) or device (ICMP metrics).
        collector_id: Collector whose observations were summarised.
        metric: The metric.
        bucket_start: Bucket start, aligned to ``bucket_seconds`` (UTC).
        bucket_seconds: Bucket width.
        expected_count: Samples the schedule intended in the bucket.
        sample_count: Observations present in the bucket.
        valid_count: Observations with a value for the metric.
        minimum: Smallest value, if any valid sample.
        mean: Central value, if any valid sample: the mean; for loss, from summed
            counts (RET-008); for RTT, the median reply (E.10).
        p95: 95th percentile value, if any valid sample.
        maximum: Largest value, if any valid sample.
        quality: Union of the member observations' quality flags.
    """

    subject_id: UUID
    collector_id: CollectorId
    metric: Metric
    bucket_start: datetime
    bucket_seconds: int
    expected_count: int
    sample_count: int
    valid_count: int
    minimum: float | None = None
    mean: float | None = None
    p95: float | None = None
    maximum: float | None = None
    quality: Quality = Quality.NONE

    @property
    def bucket_end(self) -> datetime:
        """Return the exclusive end of the bucket.

        Args:
            None

        Returns:
            datetime: ``bucket_start + bucket_seconds``.

        Raises:
            None
        """
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    def __post_init__(self) -> None:
        """Validate alignment, counts, and aggregate consistency.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the bucket is misaligned, counts are
                inconsistent, or aggregates disagree with ``valid_count``.
        """
        require_uuid(self.subject_id, "subject_id")
        require_uuid(self.collector_id, "collector_id")
        require_enum(self.metric, Metric, "metric")
        require_enum(self.quality, Quality, "quality")
        require_utc(self.bucket_start, "bucket_start")
        require_int(self.bucket_seconds, "bucket_seconds", minimum=1)
        if bucket_start(self.bucket_start, self.bucket_seconds) != self.bucket_start:
            raise ValidationError(
                "bucket_start must be aligned to bucket_seconds", field="bucket_start"
            )
        require_int(self.expected_count, "expected_count", minimum=0)
        require_int(self.sample_count, "sample_count", minimum=0)
        require_int(self.valid_count, "valid_count", minimum=0, maximum=self.sample_count)
        values = [getattr(self, name) for name in _AGGREGATES]
        for name, value in zip(_AGGREGATES, values, strict=True):
            require_float(value, name, optional=True)
        present = [v is not None for v in values]
        if any(present) != (self.valid_count > 0) or any(present) != all(present):
            raise ValidationError(
                "aggregates must all be present exactly when valid_count > 0", field="valid_count"
            )
        lo, hi = self.minimum, self.maximum
        if lo is not None and hi is not None and not all(lo <= v <= hi for v in values):
            raise ValidationError("mean and p95 must lie between minimum and maximum")
