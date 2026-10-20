"""Field-by-field payload schemas (schema version 1) and value converters.

Each observation type lists its payload fields here, in the order a reader
would document them. A field has a wire *kind* that fixes its JSON form:

========  ==========================================  =====================
kind      JSON form                                   domain value
========  ==========================================  =====================
uuid      canonical lower-case UUID string            ``uuid.UUID``
time      integer microseconds since the UTC epoch    aware UTC datetime
int       integer (never ``true``/``false``)          ``int``
float     number                                      ``float``
str       string                                      ``str``
enum      the enum's value (string or integer)        enum member
rtts      array of integers or ``null``               ``tuple[int | None]``
========  ==========================================  =====================

Optional fields may be ``null``; no key is ever omitted. A ``time`` field's
key is its attribute name plus ``_us``.

Dependencies:
    vakr.domain.observations: Observation classes and their enums.
    vakr.domain.codec_values: ``Field`` and the per-kind converters.

Safety:
    Changing a list here changes the wire format: bump ``SCHEMA_VERSION``
    in ``vakr.domain.envelope`` and regenerate the golden fixtures.

Example:
    >>> [f.key for f in SPECS[ObservationType.DEVICE_POLL].payload][-2:]
    ['started_at_us', 'completed_at_us']
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from vakr.domain.codec_values import Field
from vakr.domain.enums import (
    AddressFamily,
    BaselineState,
    CollectorState,
    CounterFamily,
    IcmpState,
    IfAdminStatus,
    IfOperStatus,
    ObservationType,
    PollExecution,
    ReachabilityStrategy,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
from vakr.domain.observations import (
    CollectorHealthObservation,
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    SnmpDeviceObservation,
)


@dataclass(frozen=True, slots=True)
class Spec:
    """The wire schema of one observation type.

    Attributes:
        cls: Observation class.
        timestamp_attr: Attribute the header ``timestamp_us`` maps to.
        has_poll: Whether the header ``poll_id`` is required (else null).
        payload: Payload fields in documentation order.
    """

    cls: Callable[..., Any]
    timestamp_attr: str
    has_poll: bool
    payload: tuple[Field, ...]


def _e(attr: str, enum: type[Enum], *, optional: bool = False) -> Field:
    """Build an enum field.

    Args:
        attr: Attribute name.
        enum: Enum class.
        optional: Whether ``null`` is allowed.

    Returns:
        Field: The field.

    Raises:
        None
    """
    return Field(attr, "enum", optional, enum)


def _many(kind: str, *attrs: str, optional: bool = True) -> tuple[Field, ...]:
    """Build several fields of one kind.

    Args:
        kind: Wire kind.
        *attrs: Attribute names.
        optional: Whether ``null`` is allowed.

    Returns:
        tuple[Field, ...]: The fields, in the order given.

    Raises:
        None
    """
    return tuple(Field(a, kind, optional) for a in attrs)


_DEVICE = Field("device_id", "uuid")
SPECS: dict[ObservationType, Spec] = {
    ObservationType.ICMP: Spec(IcmpObservation, "timestamp", True, (
        _DEVICE, _e("address_family", AddressFamily), Field("target_address", "str"),
        Field("source_address", "str", optional=True), Field("rtts_us", "rtts"),
        _e("state", IcmpState),
        *_many("int", "min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us"),
    )),
    ObservationType.SNMP_DEVICE: Spec(SnmpDeviceObservation, "timestamp", True, (
        _DEVICE, _e("snmp_version", SnmpVersion), _e("state", SnmpState),
        _e("uptime_continuity", UptimeContinuity),
        *_many("int", "request_count", "response_count", "timeout_count", "retry_count",
               "elapsed_ms", optional=False),
        Field("sys_uptime_ticks", "int", optional=True), Field("error_code", "str", optional=True),
    )),
    ObservationType.INTERFACE: Spec(InterfaceObservation, "timestamp", True, (
        _DEVICE, Field("interface_id", "uuid"), Field("if_index", "int"),
        _e("baseline_state", BaselineState),
        _e("admin_status", IfAdminStatus, optional=True),
        _e("oper_status", IfOperStatus, optional=True),
        *_many("int", "in_octets", "out_octets", "in_packets", "out_packets", "in_errors",
               "out_errors"),
        *_many("float", "in_bps", "out_bps", "in_pps", "out_pps"),
        *_many("int", "in_error_delta", "out_error_delta"),
        *_many("float", "in_error_rate", "out_error_rate"),
        Field("sample_elapsed_us", "int", optional=True),
        _e("counter_family", CounterFamily, optional=True),
        Field("counter_discontinuity_ticks", "int", optional=True),
    )),
    ObservationType.DEVICE_POLL: Spec(DevicePoll, "scheduled_at", True, (
        _DEVICE, *_many("int", "configured_interval_s", "effective_interval_s", optional=False),
        _e("reachability", ReachabilityStrategy), _e("execution", PollExecution),
        Field("worker_id", "uuid", optional=True), *_many("time", "started_at", "completed_at"),
    )),
    ObservationType.COLLECTOR_HEALTH: Spec(CollectorHealthObservation, "timestamp", False, (
        _e("state", CollectorState), Field("software_version", "str"),
        Field("worker_id", "uuid", optional=True),
        *_many("int", "clock_offset_us", "event_loop_lag_us", "queue_depth", "queue_capacity",
               "polls_completed", "polls_late", "polls_missed", "spool_bytes"),
        Field("spool_used_pct", "float", optional=True),
        *_many("int", "active_devices", "active_interfaces"),
    )),
}  # fmt: skip
"""Payload schema of every observation type, schema version 1."""
