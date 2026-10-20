"""Golden observations behind ``tests/fixtures/observations/*.json`` (ARCH-002, blueprint 24).

Each entry is a fully deterministic observation: fixed UUIDs, fixed times
with sub-second precision, and values chosen to pin the awkward parts of the
wire format: ``None`` RTTs and counters, combined quality flags, integers
above 2^53, floats that print in exponent form, a negative integer, and
non-ASCII text. A fixture file holds the canonical envelope bytes, with no
trailing newline, of ``seal(observation, GOLDEN_BATCH)``.

Regenerate the files only after a deliberate schema change (and a
``SCHEMA_VERSION`` bump when old readers would break):

    $ uv run python -m tests.unit.domain.golden_observations --write

Dependencies:
    vakr.domain: Observation types and the envelope.

Example:
    >>> sorted(GOLDEN)[0]
    'collector_health'
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from vakr.domain.enums import (
    AddressFamily,
    BaselineState,
    CollectorState,
    CounterFamily,
    IcmpState,
    IfAdminStatus,
    IfOperStatus,
    PollExecution,
    ReachabilityStrategy,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
from vakr.domain.envelope import envelope_to_bytes, seal
from vakr.domain.ids import (
    BatchId,
    CollectorId,
    DeviceId,
    InterfaceId,
    ObservationId,
    PollId,
    WorkerId,
)
from vakr.domain.observations import (
    CollectorHealthObservation,
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    Observation,
    SnmpDeviceObservation,
)
from vakr.domain.quality import Quality

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "observations"
GOLDEN_BATCH = BatchId(UUID("01929b4a-0000-7000-8000-00000000b001"))
COLLECTOR = CollectorId(UUID("01929b4a-0000-7000-8000-00000000c001"))
DEVICE = DeviceId(UUID("01929b4a-0000-7000-8000-00000000d001"))
INTERFACE = InterfaceId(UUID("01929b4a-0000-7000-8000-00000000e001"))
POLL = PollId(UUID("01929b4a-0000-7000-8000-00000000f001"))
WORKER = WorkerId(UUID("01929b4a-0000-7000-8000-00000000a001"))
AT = datetime(2026, 10, 1, 12, 0, 0, 123456, tzinfo=UTC)


def _oid(n: int) -> ObservationId:
    """Return the deterministic observation UUID number ``n``.

    Args:
        n: Small positive integer.

    Returns:
        ObservationId: The UUID.

    Raises:
        None
    """
    return ObservationId(UUID(f"01929b4a-0000-7000-8000-{n:012x}"))


GOLDEN: dict[str, Observation] = {
    "icmp_impaired": IcmpObservation(
        _oid(1), POLL, COLLECTOR, DEVICE, AT, AddressFamily.IPV4, "192.0.2.10",
        (812, None, 1033, 954, 0), IcmpState.IMPAIRED, Quality.PARTIAL_LOSS | Quality.RETRIED,
        source_address="198.51.100.2", min_rtt_us=0, avg_rtt_us=699, median_rtt_us=883,
        max_rtt_us=1033, jitter_us=377,
    ),
    "icmp_unreachable": IcmpObservation(
        _oid(2), POLL, COLLECTOR, DEVICE, AT, AddressFamily.IPV6, "2001:db8::10",
        (None, None, None, None, None), IcmpState.UNREACHABLE,
    ),
    "snmp_device_responding": SnmpDeviceObservation(
        _oid(3), POLL, COLLECTOR, DEVICE, AT, SnmpVersion.V2C, SnmpState.RESPONDING,
        UptimeContinuity.CONTINUOUS, 4, 4, 0, 0, 37, sys_uptime_ticks=(1 << 32) - 1,
    ),
    "snmp_device_timeout": SnmpDeviceObservation(
        _oid(4), POLL, COLLECTOR, DEVICE, AT, SnmpVersion.V3, SnmpState.TIMEOUT,
        UptimeContinuity.UNKNOWN, 2, 0, 2, 1, 4000,
        Quality.RETRIED | Quality.REQUEST_SIZE_REDUCED, error_code="timeout",
    ),
    "interface_valid_rates": InterfaceObservation(
        _oid(5), POLL, COLLECTOR, DEVICE, INTERFACE, AT, 2_147_483_647, BaselineState.VALID,
        Quality.RATE_EXCEEDS_CAPACITY | Quality.MAINTENANCE, IfAdminStatus.UP, IfOperStatus.UP,
        in_octets=(1 << 64) - 1, out_octets=(1 << 53) + 1, in_packets=0, out_packets=12,
        in_errors=0, out_errors=7, in_bps=1234567.891, out_bps=0.0, in_pps=1e-05, out_pps=1e16,
        in_error_delta=0, out_error_delta=3, in_error_rate=0.0, out_error_rate=0.05,
        sample_elapsed_us=60_000_123, counter_family=CounterFamily.HC64,
        counter_discontinuity_ticks=0,
    ),
    "interface_no_baseline": InterfaceObservation(
        _oid(6), POLL, COLLECTOR, DEVICE, INTERFACE, AT, 3, BaselineState.NO_BASELINE,
        Quality.BASELINE_FIRST_SAMPLE | Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS,
        oper_status=IfOperStatus.LOWER_LAYER_DOWN, in_octets=4294967295,
        counter_family=CounterFamily.LEGACY32,
    ),
    "device_poll_complete": DevicePoll(
        _oid(7), POLL, COLLECTOR, DEVICE, AT, 60, 120, ReachabilityStrategy.ICMP_GATED,
        PollExecution.COMPLETE, worker_id=WORKER, started_at=AT + timedelta(microseconds=1),
        completed_at=AT + timedelta(seconds=2, microseconds=7),
    ),
    "device_poll_not_attempted": DevicePoll(
        _oid(8), PollId(UUID("01929b4a-0000-7000-8000-00000000f002")), COLLECTOR, DEVICE, AT,
        60, 60, ReachabilityStrategy.SNMP_ONLY, PollExecution.NOT_ATTEMPTED,
        Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED,
    ),
    "collector_health": CollectorHealthObservation(
        _oid(9), COLLECTOR, AT, CollectorState.DEGRADED, "0.0.0-gölden",
        Quality.CLOCK_UNSYNCED, worker_id=WORKER, clock_offset_us=-250_000,
        event_loop_lag_us=1500, queue_depth=0, queue_capacity=4096, polls_completed=98,
        polls_late=2, polls_missed=0, spool_bytes=0, spool_used_pct=12.5,
    ),
}  # fmt: skip
"""Golden observations by fixture name."""


def golden_bytes(name: str) -> bytes:
    """Return the canonical envelope bytes the fixture ``name`` must contain.

    Args:
        name: Key of ``GOLDEN``.

    Returns:
        bytes: Canonical envelope JSON.

    Raises:
        KeyError: If ``name`` is unknown.
    """
    return envelope_to_bytes(seal(GOLDEN[name], GOLDEN_BATCH))


def write_all() -> list[Path]:
    """Rewrite every golden fixture file from ``GOLDEN``.

    Args:
        None

    Returns:
        list[Path]: Files written.

    Raises:
        OSError: If a file cannot be written.
    """
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name in sorted(GOLDEN):
        path = FIXTURE_DIR / f"{name}.json"
        path.write_bytes(golden_bytes(name))
        written.append(path)
    return written


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python -m tests.unit.domain.golden_observations --write")
    for written_path in write_all():
        print(written_path)
