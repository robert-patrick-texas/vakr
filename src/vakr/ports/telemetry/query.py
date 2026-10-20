"""Read ports for telemetry: series, raw observations, and current state (STATE-*, API-006).

Shared semantics every backend reproduces (DB-004, DB-005, DB-006):

- **Series.** Points come from one subject, one collector, one metric. The
  window is split by ``vakr.domain.series_assembly.resolution_spans``: with
  rollups and a native horizon inside the window, buckets before the horizon
  come from rollups (every bucket present, ``None`` when missing) and samples
  from the horizon on are native; otherwise the whole window is native.
  Native points are one per observation, ordered by time, with ``None`` for
  a missing value; if two share a timestamp the larger observation UUID
  wins. At most ``max_points`` points are returned and ``truncated`` says
  whether more existed. Backends build the result with
  ``assemble_series`` so these rules are identical everywhere.
- **Raw reads.** Ordered by ``(timestamp, observation_id)``; the page's
  ``total`` is ``None`` because counting raw telemetry is not bounded-cost.
- **Current state.** The latest observation not flagged ``NOT_ATTEMPTED``,
  per collector, built with ``vakr.domain.projections`` so age, freshness,
  and quality are computed identically.

Dependencies:
    vakr.domain: Observations, read models, IDs, and paging.

Safety:
    Every query is bounded by a window, ``max_points``, or a page limit.

Example:
    >>> "interface_series" in dir(ObservationQuery), "device_state" in dir(CurrentStateQuery)
    (True, True)
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import Observation
from vakr.domain.paging import Page, PageRequest
from vakr.domain.series import MAX_SERIES_POINTS, Metric, Series, TimeWindow
from vakr.domain.state import CurrentDeviceState, CurrentInterfaceState


class ObservationQuery(Protocol):
    """Bounded reads of observations as series or raw records."""

    def interface_series(
        self,
        interface_id: InterfaceId,
        collector_id: CollectorId,
        metric: Metric,
        window: TimeWindow,
        *,
        max_points: int = MAX_SERIES_POINTS,
    ) -> Series:
        """Return an interface metric over a window.

        Args:
            interface_id: The interface.
            collector_id: Whose observations to use (STATE-004).
            metric: An interface metric (``Metric.observation_type`` is ``INTERFACE``).
            window: Bounded time window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.

        Returns:
            Series: Points, disclosed resolution, and truncation flag.

        Raises:
            ValidationError: If ``metric`` is not an interface metric or
                ``max_points`` is out of range.
        """
        ...

    def icmp_series(
        self,
        device_id: DeviceId,
        collector_id: CollectorId,
        metric: Metric,
        window: TimeWindow,
        *,
        max_points: int = MAX_SERIES_POINTS,
    ) -> Series:
        """Return an ICMP metric (RTT, loss, jitter) for a device over a window.

        Args:
            device_id: The device.
            collector_id: Whose probes to use (STATE-004).
            metric: An ICMP metric (``Metric.observation_type`` is ``ICMP``).
            window: Bounded time window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.

        Returns:
            Series: Points, disclosed resolution, and truncation flag.

        Raises:
            ValidationError: If ``metric`` is not an ICMP metric or
                ``max_points`` is out of range.
        """
        ...

    def read_raw(
        self,
        observation_type: ObservationType,
        subject_id: UUID,
        window: TimeWindow,
        page: PageRequest,
        *,
        collector_id: CollectorId | None = None,
    ) -> Page[Observation]:
        """Return stored observations of one type for one subject, oldest first.

        Args:
            observation_type: Which observations to read.
            subject_id: The interface for ``INTERFACE``, the collector for
                ``COLLECTOR_HEALTH``, else the device.
            window: Bounded time window on the observation timestamp.
            page: Limit and cursor.
            collector_id: Only this collector's observations, if given.

        Returns:
            Page[Observation]: Observations equal to those written; ``total`` is ``None``.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
        """
        ...


class CurrentStateQuery(Protocol):
    """Latest valid evidence per subject and collector (STATE-001..004)."""

    def device_state(
        self, device_id: DeviceId, collector_id: CollectorId, *, now: datetime
    ) -> CurrentDeviceState | None:
        """Return a device's current state as seen by one collector.

        Args:
            device_id: The device.
            collector_id: The collector.
            now: Reference time for age and freshness (UTC).

        Returns:
            CurrentDeviceState | None: ``None`` without valid ICMP or SNMP evidence.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        ...

    def interface_state(
        self, interface_id: InterfaceId, collector_id: CollectorId, *, now: datetime
    ) -> CurrentInterfaceState | None:
        """Return an interface's current state as seen by one collector.

        Args:
            interface_id: The interface.
            collector_id: The collector.
            now: Reference time for age and freshness (UTC).

        Returns:
            CurrentInterfaceState | None: ``None`` without a valid sample.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        ...

    def device_states(
        self, collector_id: CollectorId, page: PageRequest, *, now: datetime
    ) -> Page[CurrentDeviceState]:
        """List every device with state from one collector, ordered by device ID.

        Args:
            collector_id: The collector.
            page: Limit and cursor.
            now: Reference time for age and freshness (UTC).

        Returns:
            Page[CurrentDeviceState]: States and the total count of devices with state.

        Raises:
            ValidationError: If ``page.cursor`` is invalid or ``now`` is not UTC.
        """
        ...

    def interface_states(
        self, device_id: DeviceId, collector_id: CollectorId, page: PageRequest, *, now: datetime
    ) -> Page[CurrentInterfaceState]:
        """List a device's interface states from one collector, ordered by interface ID.

        Args:
            device_id: The device.
            collector_id: The collector.
            page: Limit and cursor.
            now: Reference time for age and freshness (UTC).

        Returns:
            Page[CurrentInterfaceState]: States and the total count.

        Raises:
            ValidationError: If ``page.cursor`` is invalid or ``now`` is not UTC.
        """
        ...
