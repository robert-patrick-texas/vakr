"""Observations: immutable facts a collector measured (blueprint 7.2, 9.1, 10).

Every observation carries its own UUID for deduplication (STORE-009), the
collector that made it (STATE-004), an aware UTC timestamp (invariant 11),
and combinable ``Quality`` flags. Observations are never edited; a correction
is a new fact. This module defines the ICMP observation and
is the single import point for every observation type:
``SnmpDeviceObservation`` (``vakr.domain.snmp_observations``),
``InterfaceObservation`` (``vakr.domain.interface_observations``), and
``DevicePoll`` / ``CollectorHealthObservation`` (``vakr.domain.polls``) are
re-exported here.

ICMP RTTs are integer microseconds per probe, with ``None`` for a probe that
got no reply. The ``-1`` sentinel of ICMP-004 is a storage-adapter detail and
never appears in the domain, so no calculation can mistake it for a latency
(ICMP-005).

Dependencies:
    dataclasses, ipaddress: Records and address checks.
    vakr.domain.checks, quality, timestamps: Shared rules and flags.

Safety:
    SNMP observations never carry community strings or user secrets (10.2).

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId, new_id
    >>> obs = IcmpObservation(new_id(ObservationId), new_id(PollId), new_id(CollectorId),
    ...     new_id(DeviceId), datetime(2026, 10, 20, tzinfo=UTC), AddressFamily.IPV4,
    ...     "192.0.2.1", (900, None, 1100, 1000, 950), IcmpState.IMPAIRED)
    >>> obs.sent, obs.received, obs.loss_pct
    (5, 4, 20.0)
"""

import ipaddress
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from vakr.domain.checks import require_enum, require_int, require_uuid
from vakr.domain.enums import AddressFamily, IcmpState, ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId
from vakr.domain.interface_observations import InterfaceObservation
from vakr.domain.polls import CollectorHealthObservation, DevicePoll
from vakr.domain.quality import Quality
from vakr.domain.snmp_observations import SnmpDeviceObservation
from vakr.domain.timestamps import require_utc

__all__ = [
    "CollectorHealthObservation",
    "DevicePoll",
    "IcmpObservation",
    "InterfaceObservation",
    "Observation",
    "SnmpDeviceObservation",
    "observation_subject",
    "observation_type",
]

_SUMMARIES = ("min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us")


def _require_ip(value: object, field: str, family: AddressFamily | None) -> None:
    """Require a canonical IP literal, optionally of a given family.

    Args:
        value: Candidate address.
        field: Field name for the error message.
        family: Required family, or ``None`` for either.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a canonical IP literal of ``family``.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be an IP address string", field=field)
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise ValidationError(f"{field} must be an IP address", field=field) from None
    expected = None if family is None else 4 if family is AddressFamily.IPV4 else 6
    if str(ip) != value or (expected is not None and ip.version != expected):
        raise ValidationError(f"{field} must be a canonical {family or 'IP'} literal", field=field)


@dataclass(frozen=True, slots=True)
class IcmpObservation:
    """One five-probe (by default) ICMP reachability cycle (10.1, ICMP-001..005).

    Counts and loss are derived from ``rtts_us`` so they can never disagree
    with it; summary statistics are computed by the ICMP math and are
    ``None`` when no probe was answered.

    Attributes:
        observation_id: UUID of this observation, for deduplication.
        poll_id: The ``DevicePoll`` that produced it.
        collector_id: The collector that sent the probes.
        device_id: The probed device.
        timestamp: When the cycle started (UTC).
        address_family: Family of ``target_address``.
        target_address: The resolved IP literal that was probed.
        rtts_us: Per-probe round-trip time in microseconds; ``None`` = no reply.
        state: Reachability derived from the replies (Appendix E.1).
        quality: Evidence flags, such as ``PARTIAL_LOSS``.
        source_address: Source IP the probes left from, if pinned.
        min_rtt_us: Fastest reply, if any.
        avg_rtt_us: Mean reply time, if any.
        median_rtt_us: Median reply time, if any.
        max_rtt_us: Slowest reply, if any.
        jitter_us: Jitter, if enough replies arrived.
    """

    observation_id: ObservationId
    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    timestamp: datetime
    address_family: AddressFamily
    target_address: str
    rtts_us: tuple[int | None, ...]
    state: IcmpState
    quality: Quality = Quality.NONE
    source_address: str | None = None
    min_rtt_us: int | None = None
    avg_rtt_us: int | None = None
    median_rtt_us: int | None = None
    max_rtt_us: int | None = None
    jitter_us: int | None = None

    @property
    def sent(self) -> int:
        """Return the number of probes sent.

        Args:
            None

        Returns:
            int: ``len(rtts_us)``.

        Raises:
            None
        """
        return len(self.rtts_us)

    @property
    def received(self) -> int:
        """Return the number of probes answered.

        Args:
            None

        Returns:
            int: Probes whose RTT is not ``None``.

        Raises:
            None
        """
        return sum(1 for rtt in self.rtts_us if rtt is not None)

    @property
    def loss_pct(self) -> float:
        """Return packet loss as a percentage of probes sent (RET-008).

        Args:
            None

        Returns:
            float: ``100 * (sent - received) / sent``.

        Raises:
            None
        """
        return 100.0 * (self.sent - self.received) / self.sent

    def __post_init__(self) -> None:
        """Validate the cycle.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or summaries are
                present without replies or out of order.
        """
        for name in ("observation_id", "poll_id", "collector_id", "device_id"):
            require_uuid(getattr(self, name), name)
        require_utc(self.timestamp, "timestamp")
        require_enum(self.address_family, AddressFamily, "address_family")
        _require_ip(self.target_address, "target_address", self.address_family)
        if self.source_address is not None:
            _require_ip(self.source_address, "source_address", self.address_family)
        require_enum(self.state, IcmpState, "state")
        require_enum(self.quality, Quality, "quality")
        if not isinstance(self.rtts_us, tuple) or not self.rtts_us:
            raise ValidationError("rtts_us must be a non-empty tuple", field="rtts_us")
        for rtt in self.rtts_us:
            require_int(rtt, "rtts_us", minimum=0, optional=True)
        for name in _SUMMARIES:
            require_int(getattr(self, name), name, minimum=0, optional=True)
        if self.received == 0 and any(getattr(self, name) is not None for name in _SUMMARIES):
            raise ValidationError("RTT summaries must be None when no probe was answered")
        lo, hi = self.min_rtt_us, self.max_rtt_us
        for name in ("avg_rtt_us", "median_rtt_us"):
            value = getattr(self, name)
            if lo is not None and hi is not None and value is not None and not lo <= value <= hi:
                raise ValidationError(f"{name} must lie between min and max", field=name)


type Observation = (
    IcmpObservation
    | SnmpDeviceObservation
    | InterfaceObservation
    | DevicePoll
    | CollectorHealthObservation
)
"""Any observation an ``ObservationWriter`` accepts."""

_TYPES: dict[type, ObservationType] = {
    IcmpObservation: ObservationType.ICMP,
    SnmpDeviceObservation: ObservationType.SNMP_DEVICE,
    InterfaceObservation: ObservationType.INTERFACE,
    DevicePoll: ObservationType.DEVICE_POLL,
    CollectorHealthObservation: ObservationType.COLLECTOR_HEALTH,
}


def observation_type(observation: Observation) -> ObservationType:
    """Return the envelope type of an observation.

    Args:
        observation: Any observation.

    Returns:
        ObservationType: Its stable type name.

    Raises:
        ValidationError: If ``observation`` is not an observation type.
    """
    try:
        return _TYPES[type(observation)]
    except KeyError:
        raise ValidationError(f"{type(observation).__name__} is not an observation") from None


def observation_subject(observation: Observation) -> UUID:
    """Return the entity an observation is about, used to key queries.

    Args:
        observation: Any observation.

    Returns:
        UUID: The interface for interface samples, the collector for health
        snapshots, and the device for everything else.

    Raises:
        None
    """
    if isinstance(observation, InterfaceObservation):
        return observation.interface_id
    if isinstance(observation, CollectorHealthObservation):
        return observation.collector_id
    return observation.device_id
