"""In-memory ``ObservationQuery`` and ``CurrentStateQuery`` (DB-004, DB-005, DB-006).

Selection is a scan over the store; the rules that make answers comparable
across backends come from ``vakr.domain.series_assembly`` and
``vakr.domain.projections``.

Dependencies:
    vakr.adapters.memory.telemetry_store, paging: Storage and keyset paging.
    vakr.domain: Assembly and projection rules.

Safety:
    Every read is bounded by a window, a point cap, or a page limit.

Example:
    >>> from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
    >>> MemoryObservationQuery(MemoryTelemetryStore()).__class__.__name__
    'MemoryObservationQuery'
"""

from collections.abc import Iterator
from uuid import UUID

from vakr.adapters.memory.paging import paginate
from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
from vakr.domain.enums import ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import (
    IcmpObservation,
    InterfaceObservation,
    Observation,
    observation_subject,
    observation_type,
)
from vakr.domain.paging import CursorKey, Page, PageRequest
from vakr.domain.series import MAX_SERIES_POINTS, Metric, Series, TimeWindow
from vakr.domain.series_assembly import assemble_series, native_sample, resolution_spans
from vakr.domain.timestamps import to_epoch_us


def _select(
    store: MemoryTelemetryStore, kind: ObservationType, subject: UUID, collector: UUID | None
) -> Iterator[Observation]:
    """Yield stored observations of one type, subject, and (optionally) collector.

    Args:
        store: The store.
        kind: Observation type.
        subject: Subject UUID (see ``observation_subject``).
        collector: Collector UUID, or ``None`` for any.

    Returns:
        Iterator[Observation]: Matching observations, unordered.

    Raises:
        None
    """
    for o in list(store.observations.values()):
        same = observation_type(o) is kind and observation_subject(o) == subject
        if same and (collector is None or o.collector_id == collector):
            yield o


class MemoryObservationQuery:
    """``ObservationQuery`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The store.

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
            max_points: Point cap.

        Returns:
            Series: The series.

        Raises:
            ValidationError: For a non-interface metric or a bad ``max_points``.
        """
        return self._series(
            ObservationType.INTERFACE, interface_id, collector_id, metric, window, max_points
        )

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
            max_points: Point cap.

        Returns:
            Series: The series.

        Raises:
            ValidationError: For a non-ICMP metric or a bad ``max_points``.
        """
        return self._series(
            ObservationType.ICMP, device_id, collector_id, metric, window, max_points
        )

    def _series(
        self, kind: ObservationType, subject: UUID, collector: CollectorId, metric: Metric,
        window: TimeWindow, max_points: int,
    ) -> Series:  # fmt: skip
        """Fetch native samples and buckets, then assemble.

        Args:
            kind: Observation type the metric must belong to.
            subject: Subject UUID.
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
        caps = self._store.capabilities
        native = [
            native_sample(o, metric)
            for o in _select(self._store, kind, subject, collector)
            if isinstance(o, IcmpObservation | InterfaceObservation)
            and window.contains(o.timestamp)
        ]
        buckets = [
            b for b in self._store.buckets.values()
            if b.subject_id == subject and b.collector_id == collector and b.metric is metric
        ] if caps.rollups else []  # fmt: skip
        return assemble_series(
            subject_id=subject, collector_id=collector, metric=metric, window=window,
            spans=resolution_spans(window, self._store.horizon, rollups=caps.rollups),
            native=native, rollups=buckets, max_points=max_points,
        )  # fmt: skip

    def read_raw(
        self, observation_type: ObservationType, subject_id: UUID, window: TimeWindow,
        page: PageRequest, *, collector_id: CollectorId | None = None,
    ) -> Page[Observation]:  # fmt: skip
        """Return stored observations oldest first; ``total`` is ``None``.

        Args:
            observation_type: Which observations.
            subject_id: Subject UUID.
            window: The window.
            page: Limit and cursor.
            collector_id: Only this collector, if given.

        Returns:
            Page[Observation]: The page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        found = [
            o for o in _select(self._store, observation_type, subject_id, collector_id)
            if window.contains(o.timestamp)
        ]  # fmt: skip
        return paginate(found, raw_key, page, count=False)


def raw_key(observation: Observation) -> CursorKey:
    """Return the raw-read order key ``(timestamp_us, observation_id)``.

    Args:
        observation: Any observation.

    Returns:
        CursorKey: The key.

    Raises:
        None
    """
    return (to_epoch_us(observation.timestamp), str(observation.observation_id))
