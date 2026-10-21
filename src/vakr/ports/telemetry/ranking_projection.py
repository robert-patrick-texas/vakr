"""Port for the ranking projection: exact per-bucket ranking partials (E.28, DASH-003, DASH-004).

A 24-hour ranking needs every native sample of one collector over the day,
about 2.3 million at MVP scale, which takes longer than the 2 s dashboard
budget of 12.9. The projection stores, per 15-minute bucket (E.10),
collector, and interface, the *partial* a ranking would compute from that
bucket's samples: every available rate (exactly, so the window's
nearest-rank p95 can still be taken), the error and sample counts, and the
packet sum as exact parts (``vakr.logic.stats.exact_parts``). A ranking
read joins whole projected buckets with a native scan of the rest of the
window (its head, its tail, and any bucket not projected), so
``RankingQuery.window_summaries`` returns exactly what a native scan
returns; the projection changes only the cost.

Semantics every backend reproduces:

- **Projected buckets.** ``project(bucket)`` reads the bucket's native
  interface samples of every collector and stores their partials in the
  same transaction, then marks the bucket projected. A bucket before the
  native horizon is expired and never projected.
- **Late samples.** Writing an interface sample into a projected bucket,
  from any collector, unmarks that bucket in the same transaction, so a
  bucket marked projected always matches its native samples. The bucket is
  read natively until it is projected again.
- **Retention.** Each retention pass unmarks and removes buckets before the
  native horizon or older than ``PROJECTION_KEEP``: the projection is never
  kept longer than the native data it summarises, nor longer than the
  longest ranking window needs.
- **Rebuild.** ``forget()`` unmarks every bucket; the collector's projector
  rebuilds them from native data in bounded passes, newest first.

Dependencies:
    vakr.domain: Windows, bucket alignment, and errors.

Safety:
    Every call is bounded: a projection covers one bucket, and listings
    cover at most ``PROJECTION_KEEP``.

Example:
    >>> MAX_PROJECTION_BUCKETS, PROJECTION_KEEP.total_seconds() / 3600
    (100, 25.0)
    >>> check_projection_window(PROJECTION_KEEP.total_seconds(), 1)
"""

from datetime import datetime, timedelta
from typing import Protocol

from vakr.domain.errors import ValidationError
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import TimeWindow

PROJECTION_KEEP = timedelta(hours=25)
"""How far back the projection keeps buckets: the 24-hour window plus one hour (E.28)."""

MAX_PROJECTION_BUCKETS = int(PROJECTION_KEEP.total_seconds()) // ROLLUP_BUCKET_SECONDS
"""Most buckets one listing returns: every bucket in ``PROJECTION_KEEP`` (E.28)."""


def check_projection_window(window_s: float, limit: int) -> None:
    """Refuse a projection listing that is not bounded as E.28 requires.

    Args:
        window_s: Window length in seconds.
        limit: Requested bucket limit.

    Returns:
        None

    Raises:
        ValidationError: If the window is longer than ``PROJECTION_KEEP`` or
            ``limit`` is outside 1 to ``MAX_PROJECTION_BUCKETS``.
    """
    if window_s > PROJECTION_KEEP.total_seconds():
        raise ValidationError("a projection listing may cover at most 25 hours", field="window")
    if isinstance(limit, bool) or not 1 <= limit <= MAX_PROJECTION_BUCKETS:
        raise ValidationError("limit is out of range", field="limit")


def check_bucket(bucket: datetime) -> None:
    """Refuse a bucket start that is not on the 15-minute UTC grid (E.10).

    Args:
        bucket: The bucket start.

    Returns:
        None

    Raises:
        ValidationError: If ``bucket`` is not UTC or not aligned to 15 minutes.
    """
    if bucket.utcoffset() != timedelta(0):
        raise ValidationError("bucket must be UTC", field="bucket")
    if bucket.timestamp() % ROLLUP_BUCKET_SECONDS:
        raise ValidationError("bucket must start on a 15-minute boundary", field="bucket")


class RankingProjection(Protocol):
    """Builds and forgets the exact per-bucket ranking partials (E.28)."""

    def unprojected(
        self, window: TimeWindow, *, limit: int = MAX_PROJECTION_BUCKETS
    ) -> tuple[datetime, ...]:
        """List the buckets inside ``window`` that are not projected, newest first.

        A bucket counts when it lies wholly inside the window and starts at
        or after the native horizon.

        Args:
            window: At most ``PROJECTION_KEEP`` long.
            limit: Most buckets, 1 to ``MAX_PROJECTION_BUCKETS``.

        Returns:
            tuple[datetime, ...]: Bucket starts (UTC), newest first.

        Raises:
            ValidationError: If the window or limit is out of bounds.
            StorageError: If the store is unavailable.
        """
        ...

    def project(self, bucket: datetime) -> int:
        """Store one bucket's partials for every collector and interface, and mark it.

        Re-projecting a bucket replaces its partials. A bucket before the
        native horizon is left alone.

        Args:
            bucket: A 15-minute bucket start (UTC).

        Returns:
            int: Interface partials stored (one per collector and interface
            with samples in the bucket).

        Raises:
            ValidationError: If ``bucket`` is not on the 15-minute grid.
            StorageError: If the store is unavailable; nothing changes.
        """
        ...

    def forget(self) -> int:
        """Unmark every projected bucket, so the projector rebuilds them all.

        Args:
            None

        Returns:
            int: Buckets unmarked.

        Raises:
            StorageError: If the store is unavailable; nothing changes.
        """
        ...


class ProjectionBackend(Protocol):
    """A telemetry backend that keeps the ranking projection."""

    @property
    def ranking_projection(self) -> RankingProjection:
        """The ranking projection.

        Args:
            None

        Returns:
            RankingProjection: The port.

        Raises:
            None
        """
        ...
