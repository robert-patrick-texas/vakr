"""Current-state read models and freshness (STATE-001..004, TIME-005, Appendix E.3).

Current state is never just a status word. It says which observation it
came from, when that was, how old it is now, which collector saw it, what
quality flags it carried, and whether it is still fresh, so a dashboard can
never present a two-hour-old "up" as current (TIME-006). Each state belongs
to one collector; positions are kept apart (STATE-004).

"Latest valid" means the newest observation that is real evidence about the
target: observations flagged ``NOT_ATTEMPTED`` (the collector never polled)
are skipped, and ties on timestamp go to the larger observation UUID so
every backend picks the same one (DB-005).

This module is also the import point for the other read models:
``TimeWindow``, ``Metric``, ``Series`` and friends (``vakr.domain.series``),
``PageRequest`` and ``Page`` (``vakr.domain.paging``), and ``RollupBucket``
(``vakr.domain.rollups``) are re-exported here.

Dependencies:
    vakr.domain.observations: The evidence a state carries.
    vakr.domain.series, paging, rollups: Re-exported read models.

Safety:
    Read models hold UUIDs and values only; never backend row IDs (API-002).

Example:
    >>> from datetime import timedelta
    >>> stale_after(60), freshness_at(timedelta(seconds=151), 60)
    (datetime.timedelta(seconds=150), <Freshness.STALE: 'stale'>)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.domain.checks import require_enum, require_instance, require_int, require_uuid
from vakr.domain.enums import Freshness
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import (
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    SnmpDeviceObservation,
)
from vakr.domain.paging import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page, PageRequest
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket
from vakr.domain.series import (
    MAX_SERIES_POINTS,
    Metric,
    Series,
    SeriesPoint,
    SeriesResolution,
    TimeWindow,
)
from vakr.domain.timestamps import require_utc

__all__ = [
    "DEFAULT_EFFECTIVE_INTERVAL_S",
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "MAX_SERIES_POINTS",
    "ROLLUP_BUCKET_SECONDS",
    "CurrentDeviceState",
    "CurrentInterfaceState",
    "Metric",
    "Page",
    "PageRequest",
    "RollupBucket",
    "Series",
    "SeriesPoint",
    "SeriesResolution",
    "TimeWindow",
    "freshness_at",
    "stale_after",
]

DEFAULT_EFFECTIVE_INTERVAL_S = 60
"""Interval assumed for freshness when no poll record says otherwise (E.3)."""


def stale_after(effective_interval_s: int) -> timedelta:
    """Return the age beyond which data is stale (Appendix E.3).

    ``max(2.5 x interval, interval + 30 s)``: one late or missed poll does
    not make data stale; two do.

    Args:
        effective_interval_s: Effective poll interval in seconds.

    Returns:
        timedelta: The staleness threshold (150 s at the 60 s default).

    Raises:
        None
    """
    return max(
        timedelta(seconds=effective_interval_s * 2.5), timedelta(seconds=effective_interval_s + 30)
    )


def freshness_at(age: timedelta, effective_interval_s: int) -> Freshness:
    """Classify an observation age as fresh or stale (TIME-005, TIME-006).

    Args:
        age: Time since the observation.
        effective_interval_s: Effective poll interval in seconds.

    Returns:
        Freshness: ``STALE`` when ``age`` exceeds ``stale_after``.

    Raises:
        None
    """
    return Freshness.STALE if age > stale_after(effective_interval_s) else Freshness.FRESH


def _require_state(
    observed_at: datetime, freshness: Freshness, interval: int, quality: Quality
) -> None:
    """Check the fields both current-state models share.

    Args:
        observed_at: Time of the newest evidence.
        freshness: Freshness classification.
        interval: Effective interval used for freshness.
        quality: Quality of the evidence.

    Returns:
        None

    Raises:
        ValidationError: If a field breaks its rule.
    """
    require_utc(observed_at, "observed_at")
    require_enum(freshness, Freshness, "freshness")
    require_int(interval, "effective_interval_s", minimum=1)
    require_enum(quality, Quality, "quality")


@dataclass(frozen=True, slots=True)
class CurrentDeviceState:
    """A device's latest valid evidence from one collector (STATE-003).

    Attributes:
        device_id: The device.
        collector_id: The collector whose evidence this is.
        observed_at: Time of the newest valid ICMP or SNMP observation.
        age: ``now - observed_at`` at query time (negative if clocks disagree).
        freshness: Fresh or stale per Appendix E.3.
        effective_interval_s: Interval used for freshness (from ``poll``, else 60).
        quality: Union of the quality of ``icmp`` and ``snmp``.
        icmp: Latest valid ICMP observation, if any.
        snmp: Latest valid SNMP device observation, if any.
        poll: Latest poll record of any outcome, if any; shows not-attempted polls.
    """

    device_id: DeviceId
    collector_id: CollectorId
    observed_at: datetime
    age: timedelta
    freshness: Freshness
    effective_interval_s: int
    quality: Quality
    icmp: IcmpObservation | None
    snmp: SnmpDeviceObservation | None
    poll: DevicePoll | None

    def __post_init__(self) -> None:
        """Validate the state.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule or there is no evidence.
        """
        require_uuid(self.device_id, "device_id")
        require_uuid(self.collector_id, "collector_id")
        _require_state(self.observed_at, self.freshness, self.effective_interval_s, self.quality)
        require_instance(self.icmp, IcmpObservation, "icmp", optional=True)
        require_instance(self.snmp, SnmpDeviceObservation, "snmp", optional=True)
        require_instance(self.poll, DevicePoll, "poll", optional=True)
        if self.icmp is None and self.snmp is None:
            raise ValidationError("a device state needs ICMP or SNMP evidence", field="icmp")


@dataclass(frozen=True, slots=True)
class CurrentInterfaceState:
    """An interface's latest valid sample from one collector (STATE-003).

    Attributes:
        interface_id: The interface.
        device_id: Its device.
        collector_id: The collector whose evidence this is.
        observed_at: Time of the sample.
        age: ``now - observed_at`` at query time.
        freshness: Fresh or stale per Appendix E.3.
        effective_interval_s: Interval used for freshness (from the sample's poll, else 60).
        quality: Quality of the sample.
        observation: The sample itself.
    """

    interface_id: InterfaceId
    device_id: DeviceId
    collector_id: CollectorId
    observed_at: datetime
    age: timedelta
    freshness: Freshness
    effective_interval_s: int
    quality: Quality
    observation: InterfaceObservation

    def __post_init__(self) -> None:
        """Validate the state.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        for name in ("interface_id", "device_id", "collector_id"):
            require_uuid(getattr(self, name), name)
        _require_state(self.observed_at, self.freshness, self.effective_interval_s, self.quality)
        require_instance(self.observation, InterfaceObservation, "observation")
