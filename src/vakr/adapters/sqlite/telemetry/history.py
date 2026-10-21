"""``HistoryQuery`` for the SQLite telemetry store: downsampled series and rankings (M4).

Both reads already exist as adapter functions: ``downsample`` buckets a
series on the ladder (10.6) and ``rankings`` summarises every interface
over a window (DASH-003, DASH-004). This class binds them to one store as
the ``HistoryQuery`` port, so the query services need nothing
SQLite-specific.

Dependencies:
    vakr.adapters.sqlite.telemetry.downsample, rankings, store.
    vakr.domain: Read models.

Safety:
    Every read is bounded by a window and a point cap or row limit.

Example:
    >>> SqliteHistoryQuery.__name__
    'SqliteHistoryQuery'
"""

from uuid import UUID

from vakr.adapters.sqlite.telemetry.downsample import downsampled_series
from vakr.adapters.sqlite.telemetry.rankings import window_summaries
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.enums import SeriesSource
from vakr.domain.ids import CollectorId
from vakr.domain.series import MAX_SERIES_POINTS, Metric, Series, TimeWindow
from vakr.ports.telemetry.rankings import MAX_RANKED_INTERFACES, RankingScan


class SqliteHistoryQuery:
    """``HistoryQuery`` over one ``SqliteTelemetryStore``."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def downsampled_series(
        self, subject_id: UUID, collector_id: CollectorId, metric: Metric, window: TimeWindow,
        *, max_points: int = MAX_SERIES_POINTS, source: SeriesSource | None = None,
    ) -> Series:  # fmt: skip
        """Return a series bucketed on the fixed width ladder; see the port.

        Args:
            subject_id: Interface or device.
            collector_id: The collector.
            metric: The metric.
            window: The window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.
            source: ``None`` to split at the native horizon, else one source throughout.

        Returns:
            Series: One point per bucket, gaps as ``None``.

        Raises:
            ValidationError: If ``max_points`` is out of range.
        """
        return downsampled_series(self._store, subject_id, collector_id, metric, window,
                                  max_points, source)  # fmt: skip

    def window_summaries(
        self, collector_id: CollectorId, window: TimeWindow, *, limit: int = MAX_RANKED_INTERFACES
    ) -> RankingScan:
        """Return every interface's p95 rates and error totals; see the port.

        Args:
            collector_id: The collector.
            window: The window, at most one day.
            limit: Most rows.

        Returns:
            RankingScan: Rows ordered by interface UUID text.

        Raises:
            ValidationError: If the window or limit is out of bounds.
            StorageError: If the store cannot be read.
        """
        return window_summaries(self._store, collector_id, window, limit)
