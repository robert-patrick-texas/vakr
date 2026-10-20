"""Interface counter samples and the rates derived from them (blueprint 10.3).

One ``InterfaceObservation`` per interface per poll holds the raw IF-MIB
counters and, when a valid baseline existed, the derived rates. Two rules
from 4.3 shape it:

- missing is ``None``, never ``0``: an agent that did not return
  ``ifHCInOctets`` produced no value, not an idle link (invariant 1);
- a counter reset, discontinuity, or invalid baseline yields *no* rate
  rather than a spike (invariant 5), so rates and deltas may only be present
  when ``baseline_state`` is ``VALID`` and no baseline-invalidating quality
  flag is set (RATE-004).

Names, aliases, and tags are not repeated here; they live on ``Interface``
(STORE-008).

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, quality, timestamps: Shared rules and flags.

Safety:
    No field holds credentials or descriptive text.

Example:
    >>> InterfaceObservation.__dataclass_fields__["in_bps"].default is None
    True
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.checks import require_enum, require_float, require_int, require_uuid
from vakr.domain.enums import BaselineState, CounterFamily, IfAdminStatus, IfOperStatus
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId
from vakr.domain.interfaces import MAX_IF_INDEX
from vakr.domain.quality import BASELINE_INVALIDATING, Quality
from vakr.domain.timestamps import require_utc

_COUNTER_FIELDS = ("in_octets", "out_octets", "in_packets", "out_packets")
_ERROR_FIELDS = ("in_errors", "out_errors")
_DERIVED_INT_FIELDS = ("in_error_delta", "out_error_delta")
_DERIVED_FLOAT_FIELDS = (
    "in_bps",
    "out_bps",
    "in_pps",
    "out_pps",
    "in_error_rate",
    "out_error_rate",
)
_COUNTER_LIMIT = {CounterFamily.HC64: 1 << 64, CounterFamily.LEGACY32: 1 << 32}


@dataclass(frozen=True, slots=True)
class InterfaceObservation:
    """One interface's counters and derived rates from one poll (10.3).

    Attributes:
        observation_id: UUID of this observation, for deduplication.
        poll_id: The ``DevicePoll`` that produced it.
        collector_id: The collector that made it.
        device_id: Device the interface belongs to.
        interface_id: Persistent interface identity (never ``ifIndex``).
        timestamp: When the counters were sampled (UTC).
        if_index: ``ifIndex`` the counters were read from, as observed.
        baseline_state: Whether a valid previous sample existed (9.7).
        quality: Evidence flags; baseline flags explain missing rates.
        admin_status: ``ifAdminStatus``, if read.
        oper_status: ``ifOperStatus``, if read.
        in_octets: Raw inbound octet counter, if read.
        out_octets: Raw outbound octet counter, if read.
        in_packets: Raw inbound unicast+non-unicast packet counter, if read.
        out_packets: Raw outbound packet counter, if read.
        in_errors: Raw ``ifInErrors``, if read.
        out_errors: Raw ``ifOutErrors``, if read.
        in_bps: Inbound bits per second, when derivable.
        out_bps: Outbound bits per second, when derivable.
        in_pps: Inbound packets per second, when derivable.
        out_pps: Outbound packets per second, when derivable.
        in_error_delta: Inbound errors since the previous sample, when derivable.
        out_error_delta: Outbound errors since the previous sample, when derivable.
        in_error_rate: Inbound errors per second, when derivable.
        out_error_rate: Outbound errors per second, when derivable.
        sample_elapsed_us: Microseconds since the previous sample, if any.
        counter_family: Counter width the octet/packet values came from.
        counter_discontinuity_ticks: ``ifCounterDiscontinuityTime``, if read.
    """

    observation_id: ObservationId
    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    interface_id: InterfaceId
    timestamp: datetime
    if_index: int
    baseline_state: BaselineState
    quality: Quality = Quality.NONE
    admin_status: IfAdminStatus | None = None
    oper_status: IfOperStatus | None = None
    in_octets: int | None = None
    out_octets: int | None = None
    in_packets: int | None = None
    out_packets: int | None = None
    in_errors: int | None = None
    out_errors: int | None = None
    in_bps: float | None = None
    out_bps: float | None = None
    in_pps: float | None = None
    out_pps: float | None = None
    in_error_delta: int | None = None
    out_error_delta: int | None = None
    in_error_rate: float | None = None
    out_error_rate: float | None = None
    sample_elapsed_us: int | None = None
    counter_family: CounterFamily | None = None
    counter_discontinuity_ticks: int | None = None

    def __post_init__(self) -> None:
        """Validate the sample, including the no-rate-without-baseline rule.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or a rate or delta is
                present while the baseline is not valid.
        """
        for name in ("observation_id", "poll_id", "collector_id", "device_id", "interface_id"):
            require_uuid(getattr(self, name), name)
        require_utc(self.timestamp, "timestamp")
        require_int(self.if_index, "if_index", minimum=1, maximum=MAX_IF_INDEX)
        require_enum(self.baseline_state, BaselineState, "baseline_state")
        require_enum(self.quality, Quality, "quality")
        require_enum(self.admin_status, IfAdminStatus, "admin_status", optional=True)
        require_enum(self.oper_status, IfOperStatus, "oper_status", optional=True)
        require_enum(self.counter_family, CounterFamily, "counter_family", optional=True)
        self._check_counters()
        for name in _DERIVED_FLOAT_FIELDS:
            require_float(getattr(self, name), name, optional=True)
        for name in _DERIVED_INT_FIELDS:
            require_int(getattr(self, name), name, minimum=0, optional=True)
        require_int(self.sample_elapsed_us, "sample_elapsed_us", minimum=1, optional=True)
        require_int(self.counter_discontinuity_ticks, "counter_discontinuity_ticks", minimum=0,
                    maximum=(1 << 32) - 1, optional=True)  # fmt: skip
        self._check_baseline()

    def _check_counters(self) -> None:
        """Require raw counters to fit their counter width.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a counter is negative or too wide.
        """
        family = self.counter_family
        limit = 1 << 64 if family is None else _COUNTER_LIMIT[family]
        for name in _COUNTER_FIELDS:
            require_int(getattr(self, name), name, minimum=0, maximum=limit - 1, optional=True)
        for name in _ERROR_FIELDS:
            require_int(getattr(self, name), name, minimum=0, maximum=(1 << 64) - 1, optional=True)

    def _check_baseline(self) -> None:
        """Enforce invariant 5: no derived value without a valid baseline.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the baseline state and quality flags disagree,
                or a derived value is present without a valid baseline.
        """
        invalidated = bool(self.quality & BASELINE_INVALIDATING)
        if self.baseline_state is BaselineState.VALID and invalidated:
            raise ValidationError(
                "baseline_state is valid but quality carries a baseline-invalidating flag",
                field="baseline_state",
            )
        if self.baseline_state is BaselineState.VALID:
            return
        for name in (*_DERIVED_FLOAT_FIELDS, *_DERIVED_INT_FIELDS):
            if getattr(self, name) is not None:
                raise ValidationError(
                    f"{name} must be None when baseline_state is {self.baseline_state}",
                    field=name,
                )
