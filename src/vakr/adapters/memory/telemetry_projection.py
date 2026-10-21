"""In-memory ``RankingProjection``: the reference bookkeeping of projected buckets (E.28).

The memory backend's rankings always scan native observations, which is
the reference answer every backend must return
(``tests/contract/contract_telemetry_history.py``). Its projection therefore
stores no partials, only which buckets are listed, with the same rules the
SQLite adapter keeps: projecting lists a bucket, storing an interface sample
unlists its bucket (``MemoryObservationWriter``), retention unlists buckets
before the native horizon or older than ``PROJECTION_KEEP``
(``MemoryRetentionManager``), and ``forget`` unlists everything. The
contract suite ``contract_ranking_projection`` holds both backends to it.

Dependencies:
    vakr.adapters.memory.telemetry_store: Storage.
    vakr.domain, vakr.ports.telemetry.ranking_projection: Buckets and bounds.

Safety:
    Bounded like the port: one bucket per call, listings of at most 25 hours.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
    >>> projection = MemoryRankingProjection(MemoryTelemetryStore())
    >>> projection.project(datetime(2026, 10, 1, tzinfo=UTC)), projection.forget()
    (0, 1)
"""

from datetime import datetime, timedelta

from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
from vakr.domain.observations import InterfaceObservation
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, bucket_start
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry.ranking_projection import (
    MAX_PROJECTION_BUCKETS,
    PROJECTION_KEEP,
    check_bucket,
    check_projection_window,
)

_BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def prune_projection(store: MemoryTelemetryStore, now: datetime) -> None:
    """Unlist buckets before the native horizon or older than ``PROJECTION_KEEP`` (retention).

    Args:
        store: The store; the caller holds its lock.
        now: The retention pass's reference time.

    Returns:
        None

    Raises:
        None
    """
    cut = bucket_start(now - PROJECTION_KEEP)
    if store.horizon is not None:
        cut = max(cut, store.horizon)
    store.projected = {b for b in store.projected if b >= cut}


class MemoryRankingProjection:
    """``RankingProjection`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the projection.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def unprojected(
        self, window: TimeWindow, *, limit: int = MAX_PROJECTION_BUCKETS
    ) -> tuple[datetime, ...]:
        """List buckets inside ``window`` that are not projected, newest first; see the port.

        Args:
            window: At most ``PROJECTION_KEEP`` long.
            limit: Most buckets.

        Returns:
            tuple[datetime, ...]: Bucket starts (UTC).

        Raises:
            ValidationError: If the window or limit is out of bounds.
        """
        check_projection_window(window.duration.total_seconds(), limit)
        start = window.start
        if self._store.horizon is not None:
            start = max(start, self._store.horizon)
        found: list[datetime] = []
        bucket = bucket_start(window.end) - _BUCKET  # the newest bucket ending by window.end
        while bucket >= start and len(found) < limit:
            if bucket not in self._store.projected:
                found.append(bucket)
            bucket -= _BUCKET
        return tuple(found)

    def project(self, bucket: datetime) -> int:
        """List one bucket as projected; see the port.

        Args:
            bucket: A 15-minute bucket start (UTC).

        Returns:
            int: Collector and interface pairs with samples in the bucket.

        Raises:
            ValidationError: If ``bucket`` is not on the 15-minute grid.
        """
        check_bucket(bucket)
        store = self._store
        with store.lock:
            if store.horizon is not None and bucket < store.horizon:
                return 0
            store.projected.add(bucket)
            pairs = {(o.collector_id, o.interface_id) for o in store.observations.values()
                     if isinstance(o, InterfaceObservation)
                     and bucket <= o.timestamp < bucket + _BUCKET}  # fmt: skip
        return len(pairs)

    def forget(self) -> int:
        """Unlist every projected bucket; see the port.

        Args:
            None

        Returns:
            int: Buckets unlisted.

        Raises:
            None
        """
        with self._store.lock:
            count = len(self._store.projected)
            self._store.projected = set()
        return count
