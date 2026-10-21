"""``ObservationQuery`` for the SQLite telemetry store (DB-004, DB-006, STATE-005, API-006).

Series follow the port's shared rules exactly: ``resolution_spans`` splits
the window at the native horizon, rollup buckets serve the part before it
and native samples the rest, and ``assemble_series`` builds the result, so
gaps, ties (the larger observation UUID wins), truncation, and disclosed
resolution match every other backend. The SQL fetches only what the point
cap can use: rollup points come first and use the budget first, then at
most the remaining number of distinct native timestamps (``samples``).
Everything is read in one snapshot (ADR-021).

``downsampled_series`` (``downsample``) is the bucketed variant for long
native windows, an adapter extension. Raw reads are in ``raw_reads``.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Samples, rollup reads, raw reads, and the store.
    vakr.domain: Series assembly and errors.

Safety:
    Every read is bounded by a window and a point cap or page limit.

Example:
    >>> SqliteObservationQuery.__name__
    'SqliteObservationQuery'
"""

from uuid import UUID

from vakr.adapters.sqlite.telemetry.downsample import downsampled_series
from vakr.adapters.sqlite.telemetry.layout import FAMILY_OF
from vakr.adapters.sqlite.telemetry.raw_reads import raw_page
from vakr.adapters.sqlite.telemetry.reads import horizon
from vakr.adapters.sqlite.telemetry.rollup_reads import buckets_for_span
from vakr.adapters.sqlite.telemetry.samples import bucket_starts, native_samples
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.enums import ObservationType, SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import Observation
from vakr.domain.paging import Page, PageRequest
from vakr.domain.series import MAX_SERIES_POINTS, Metric, Series, TimeWindow
from vakr.domain.series_assembly import NativeSample, assemble_series, resolution_spans
from vakr.domain.state import RollupBucket
from vakr.domain.timestamps import from_epoch_us, to_epoch_us


class SqliteObservationQuery:
    """Series and raw reads over one ``SqliteTelemetryStore``."""

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

    def interface_series(
        self, interface_id: InterfaceId, collector_id: CollectorId, metric: Metric,
        window: TimeWindow, *, max_points: int = MAX_SERIES_POINTS,
    ) -> Series:  # fmt: skip
        """Return an interface metric series; see the port.

        Args:
            interface_id: The interface.
            collector_id: The collector.
            metric: An interface metric.
            window: The window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.

        Returns:
            Series: The series.

        Raises:
            ValidationError: For a non-interface metric or a bad ``max_points``.
        """
        return self._series(ObservationType.INTERFACE, interface_id, collector_id, metric,
                            window, max_points)  # fmt: skip

    def icmp_series(
        self, device_id: DeviceId, collector_id: CollectorId, metric: Metric,
        window: TimeWindow, *, max_points: int = MAX_SERIES_POINTS,
    ) -> Series:  # fmt: skip
        """Return an ICMP metric series; see the port.

        Args:
            device_id: The device.
            collector_id: The collector.
            metric: An ICMP metric.
            window: The window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.

        Returns:
            Series: The series.

        Raises:
            ValidationError: For a non-ICMP metric or a bad ``max_points``.
        """
        return self._series(ObservationType.ICMP, device_id, collector_id, metric, window,
                            max_points)  # fmt: skip

    def downsampled_series(
        self, subject_id: UUID, collector_id: CollectorId, metric: Metric, window: TimeWindow,
        *, max_points: int = MAX_SERIES_POINTS, source: SeriesSource | None = None,
    ) -> Series:  # fmt: skip
        """Return a series bucketed on the fixed width ladder (10.6); see ``HistoryQuery``.

        Args:
            subject_id: Interface (interface metrics) or device (ICMP metrics).
            collector_id: The collector.
            metric: The metric.
            window: The window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.
            source: ``None`` to split at the native horizon, else one source throughout.

        Returns:
            Series: One min/mean/max point per bucket, gaps as ``None``.

        Raises:
            ValidationError: If ``max_points`` is out of range.
        """
        return downsampled_series(self._store, subject_id, collector_id, metric, window,
                                  max_points, source)  # fmt: skip

    def read_raw(
        self, observation_type: ObservationType, subject_id: UUID, window: TimeWindow,
        page: PageRequest, *, collector_id: CollectorId | None = None,
    ) -> Page[Observation]:  # fmt: skip
        """Return stored observations oldest first by ``(timestamp, id)``; ``total`` is ``None``.

        Args:
            observation_type: Which observations.
            subject_id: Interface, device, or (health) collector.
            window: The window.
            page: Limit and cursor.
            collector_id: Only this collector, if given.

        Returns:
            Page[Observation]: The page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        return raw_page(self._store, observation_type, subject_id, window, page, collector_id)

    def _series(
        self, kind: ObservationType, subject: UUID, collector: CollectorId, metric: Metric,
        window: TimeWindow, max_points: int,
    ) -> Series:  # fmt: skip
        """Fetch what the point cap can use, then assemble by the shared rules.

        Args:
            kind: Observation type the metric must belong to.
            subject: Interface or device.
            collector: The collector.
            metric: The metric.
            window: The window.
            max_points: Point cap.

        Returns:
            Series: The series.

        Raises:
            ValidationError: For a metric of another type or a bad ``max_points``.
        """
        if metric.observation_type is not kind:
            raise ValidationError(f"{metric} is not a {kind} metric", field="metric")
        if not 1 <= max_points <= MAX_SERIES_POINTS:
            raise ValidationError("max_points is out of range", field="max_points")
        buckets: list[RollupBucket] = []
        native: list[NativeSample] = []
        with self._store.reading() as conn:
            cut = horizon(conn)
            spans = resolution_spans(
                window, None if cut is None else from_epoch_us(cut), rollups=True
            )
            budget = max_points + 1
            for span in spans:
                if span.source is SeriesSource.ROLLUP:
                    buckets = buckets_for_span(self._store, conn, subject, collector, metric,
                                               span, budget)  # fmt: skip
                    budget -= bucket_starts(span.start, span.end)
                else:
                    bounds = (to_epoch_us(span.start), to_epoch_us(span.end))
                    native = native_samples(self._store, conn, FAMILY_OF[kind], subject,
                                            collector, metric, bounds, budget)  # fmt: skip
        return assemble_series(
            subject_id=subject, collector_id=collector, metric=metric, window=window,
            spans=spans, native=native, rollups=buckets, max_points=max_points,
        )  # fmt: skip
