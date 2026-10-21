"""Read port for history graphs and rankings, and the backend bundle that offers it (M4).

``ObservationQuery`` returns one point per observation, which suits graphs
of up to about 20 hours at the 60 s default. Longer graphs need server-side
downsampling with min/max envelopes (10.6), and rankings need per-interface
summaries (``vakr.ports.telemetry.rankings``). ``HistoryQuery`` adds both;
``HistoryBackend`` is a ``TelemetryBackend`` that also exposes it, which
every adapter in this repository is.

``downsampled_series`` semantics every backend reproduces (DB-004, DB-006):
the width, spans, bucket alignment, folding, and point rules of
``vakr.domain.downsampling``. With ``source=None`` the window splits at the
native horizon (rollups before it, native after); ``NATIVE`` or ``ROLLUP``
reads the whole window from that source, so a 90-day or 400-day graph can
come entirely from 15-minute rollups (10.6 table). Native data before the
horizon has expired, so a forced native span shows gaps there.

Dependencies:
    vakr.domain: Series read models.
    vakr.ports.telemetry: The base backend protocol and the ranking port.

Safety:
    Every read is bounded by a window and ``max_points``.

Example:
    >>> "downsampled_series" in dir(HistoryQuery), "history" in dir(HistoryBackend)
    (True, True)
"""

from typing import Protocol
from uuid import UUID

from vakr.domain.enums import SeriesSource
from vakr.domain.ids import CollectorId
from vakr.domain.series import MAX_SERIES_POINTS, Metric, Series, TimeWindow
from vakr.ports.telemetry import TelemetryBackend
from vakr.ports.telemetry.rankings import RankingQuery


class HistoryQuery(RankingQuery, Protocol):
    """Downsampled series and ranking summaries."""

    def downsampled_series(
        self,
        subject_id: UUID,
        collector_id: CollectorId,
        metric: Metric,
        window: TimeWindow,
        *,
        max_points: int = MAX_SERIES_POINTS,
        source: SeriesSource | None = None,
    ) -> Series:
        """Return a metric bucketed on the fixed width ladder (10.6).

        Args:
            subject_id: Interface (interface metrics) or device (ICMP metrics).
            collector_id: The one collector whose evidence to use (STATE-004).
            metric: The metric.
            window: Bounded window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.
            source: ``None`` to split at the native horizon, else the one
                source for the whole window.

        Returns:
            Series: One mean/min/max point per bucket, ``None`` for an empty
            bucket, every span's source and bucket width disclosed.

        Raises:
            ValidationError: If ``max_points`` is out of range, or ``ROLLUP``
                is forced on a backend without rollups.
        """
        ...


class HistoryBackend(TelemetryBackend, Protocol):
    """A telemetry backend that also serves history graphs and rankings."""

    @property
    def history(self) -> HistoryQuery:
        """Downsampled series and ranking summaries.

        Args:
            None

        Returns:
            HistoryQuery: The port.

        Raises:
            None
        """
        ...
