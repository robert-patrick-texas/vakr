"""The rollup job's read port: one bucket's native evidence, a page of subjects at a time (RET-004).

A rollup bucket summarises every native observation of one subject from
one collector in 15 minutes. Building whole ``InterfaceObservation``
objects for that would cost about a second per bucket at the MVP workload
(ADR-021 amendment 5), so interface samples come back slim: the six metric
values, the quality flags, and the identity needed to order and
de-duplicate them. Device evidence carries the ICMP cycles themselves, the
poll schedule that sets the expected sample count, and whether SNMP saw the
agent restart (RET-006).

Shared semantics every backend reproduces (contract ``contract_rollup_source``):

- Rows before the native horizon are expired and never returned.
- ``first_native_time`` is the earliest interface or ICMP observation time
  in the window, so the job can step over a collector outage.
- Pages are keyset pages ordered by subject UUID, then collector UUID. A
  page covers at most ``page.limit`` subjects with every collector's
  evidence for each; it may hold fewer, even none, before the last page.
  Read until ``next_cursor`` is ``None``. ``total`` is always ``None``.
- Samples and cycles are ordered by ``(timestamp, observation_id)``; two
  observations at one instant are both returned (the job keeps the larger
  UUID, as series do, DB-004).
- ``schedule`` is the latest poll record (by scheduled time, then
  observation UUID) scheduled in ``[start - SCHEDULE_LOOKBACK, end)``, so a
  bucket of a device polled every hour still finds its schedule.

Dependencies:
    vakr.domain: Observations, IDs, metrics, windows, quality, and paging.

Safety:
    Read-only, and bounded: one window and a page of subjects per call.

Example:
    >>> [m.value for m in INTERFACE_METRICS][:2], SCHEDULE_LOOKBACK.total_seconds()
    (['in_bps', 'out_bps'], 3600.0)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from vakr.domain.ids import CollectorId
from vakr.domain.observations import IcmpObservation
from vakr.domain.paging import Page, PageRequest
from vakr.domain.quality import Quality
from vakr.domain.series import Metric, TimeWindow

INTERFACE_METRICS = (Metric.IN_BPS, Metric.OUT_BPS, Metric.IN_PPS, Metric.OUT_PPS,
                     Metric.IN_ERRORS, Metric.OUT_ERRORS)  # fmt: skip
"""Order of ``InterfaceSample.values``: every interface metric the Compact layout rolls up."""

ICMP_METRICS = (Metric.RTT_MEDIAN_US, Metric.LOSS_PCT, Metric.JITTER_US)
"""Every ICMP metric the Compact layout rolls up."""

SCHEDULE_LOOKBACK = timedelta(hours=1)
"""How far before a bucket a device's poll schedule is looked for: E.18's longest interval."""


@dataclass(frozen=True, slots=True)
class InterfaceSample:
    """One interface observation reduced to what a rollup needs. Immutable.

    Attributes:
        timestamp: When the counters were sampled (UTC).
        observation_id: The observation's UUID, which breaks ties at one instant.
        values: One value per ``INTERFACE_METRICS`` entry; ``None`` when missing.
        quality: The observation's quality flags.
    """

    timestamp: datetime
    observation_id: UUID
    values: tuple[float | None, ...]
    quality: Quality


@dataclass(frozen=True, slots=True)
class InterfaceSamples:
    """One interface's samples from one collector in one window. Immutable.

    Attributes:
        interface_id: Persistent interface identity.
        collector_id: The collector.
        device_id: The device of the newest sample.
        samples: Ordered by ``(timestamp, observation_id)``.
    """

    interface_id: UUID
    collector_id: CollectorId
    device_id: UUID
    samples: tuple[InterfaceSample, ...]


@dataclass(frozen=True, slots=True)
class PollSlot:
    """One scheduled poll: the anchor and period of a device's schedule. Immutable.

    Attributes:
        scheduled_at: The poll's intended slot (UTC).
        interval_s: Its effective interval in seconds.
    """

    scheduled_at: datetime
    interval_s: int


@dataclass(frozen=True, slots=True)
class DeviceEvidence:
    """One device's rollup evidence from one collector in one window. Immutable.

    Attributes:
        device_id: The device.
        collector_id: The collector.
        cycles: ICMP cycles, ordered by ``(timestamp, observation_id)``.
        schedule: The latest poll record, as the module docstring defines.
        restarted: An SNMP device observation in the window detected an agent restart.
    """

    device_id: UUID
    collector_id: CollectorId
    cycles: tuple[IcmpObservation, ...]
    schedule: PollSlot | None
    restarted: bool


class RollupSource(Protocol):
    """Bounded native reads for the rollup job (RET-004, RET-006, RET-009)."""

    def first_native_time(self, window: TimeWindow) -> datetime | None:
        """Return the earliest interface or ICMP observation time in the window.

        Args:
            window: Bounded window on observation time.

        Returns:
            datetime | None: The time, or ``None`` when the window holds none.

        Raises:
            StorageError: If the store is unavailable.
        """
        ...

    def device_evidence(self, window: TimeWindow, page: PageRequest) -> Page[DeviceEvidence]:
        """Return the evidence of a page of devices, as the module docstring describes.

        Args:
            window: One bucket.
            page: At most ``page.limit`` devices, after ``page.cursor``.

        Returns:
            Page[DeviceEvidence]: Devices with an ICMP cycle or an SNMP
            restart in the window, or a poll in its schedule lookback.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
            StorageError: If the store is unavailable.
        """
        ...

    def interface_samples(self, window: TimeWindow, page: PageRequest) -> Page[InterfaceSamples]:
        """Return the samples of a page of interfaces, as the module docstring describes.

        Args:
            window: One bucket.
            page: At most ``page.limit`` interfaces, after ``page.cursor``.

        Returns:
            Page[InterfaceSamples]: Interfaces with a sample in the window.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
            StorageError: If the store is unavailable.
        """
        ...
