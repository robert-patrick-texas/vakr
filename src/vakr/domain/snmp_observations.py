"""The SNMP part of one device poll: availability, uptime, and effort (blueprint 10.2).

A device can answer ICMP and still fail SNMP (wrong community, ACL, an
overloaded agent), so SNMP availability is recorded separately from
reachability. ``sys_uptime_ticks`` feeds the uptime-continuity rules of
Appendix E.5, which tell a real reload from a counter wrap.

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, quality, timestamps: Shared rules and flags.

Safety:
    No credential values or community/user secrets are stored (10.2).

Example:
    >>> SnmpDeviceObservation.__dataclass_fields__["sys_uptime_ticks"].default is None
    True
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.checks import require_enum, require_int, require_name, require_uuid
from vakr.domain.enums import SnmpState, SnmpVersion, UptimeContinuity
from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId
from vakr.domain.quality import Quality
from vakr.domain.timestamps import require_utc


@dataclass(frozen=True, slots=True)
class SnmpDeviceObservation:
    """The SNMP part of one device poll (10.2, SNMP-014).

    Attributes:
        observation_id: UUID of this observation, for deduplication.
        poll_id: The ``DevicePoll`` that produced it.
        collector_id: The collector that sent the requests.
        device_id: The polled device.
        timestamp: When the SNMP exchange started (UTC).
        snmp_version: Protocol version used.
        state: Outcome of the exchange.
        uptime_continuity: What ``sysUpTime`` says about agent restarts (E.5).
        request_count: PDUs sent, retries included.
        response_count: PDUs answered.
        timeout_count: PDUs that timed out.
        retry_count: Retries sent.
        elapsed_ms: Wall-clock duration of the exchange.
        quality: Evidence flags, such as ``PARTIAL_RESPONSE``.
        sys_uptime_ticks: ``sysUpTime`` in hundredths of a second, if read.
        error_code: Stable partial-reason or error code, if any.
    """

    observation_id: ObservationId
    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    timestamp: datetime
    snmp_version: SnmpVersion
    state: SnmpState
    uptime_continuity: UptimeContinuity
    request_count: int
    response_count: int
    timeout_count: int
    retry_count: int
    elapsed_ms: int
    quality: Quality = Quality.NONE
    sys_uptime_ticks: int | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Validate the exchange.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or more responses or
                timeouts are reported than requests were sent.
        """
        for name in ("observation_id", "poll_id", "collector_id", "device_id"):
            require_uuid(getattr(self, name), name)
        require_utc(self.timestamp, "timestamp")
        require_enum(self.snmp_version, SnmpVersion, "snmp_version")
        require_enum(self.state, SnmpState, "state")
        require_enum(self.uptime_continuity, UptimeContinuity, "uptime_continuity")
        require_enum(self.quality, Quality, "quality")
        for name in ("request_count", "retry_count", "elapsed_ms"):
            require_int(getattr(self, name), name, minimum=0)
        for name in ("response_count", "timeout_count"):
            require_int(getattr(self, name), name, minimum=0, maximum=self.request_count)
        require_int(self.sys_uptime_ticks, "sys_uptime_ticks", minimum=0,
                    maximum=(1 << 32) - 1, optional=True)  # fmt: skip
        require_name(self.error_code, "error_code", optional=True)
