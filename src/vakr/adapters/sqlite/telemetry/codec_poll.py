"""Poll records and collector health snapshots to rows and back (9.1, 10.4, ADR-021).

``DevicePoll`` rows live in their own ``obs_poll`` family, partitioned by
day like ICMP: they are as numerous as ICMP cycles, current state needs
the latest one per device, and an interface's freshness needs the poll a
sample came from. Their timestamp is the scheduled time (invariant 10);
start and completion are stored as offsets from it, which take three or
four bytes instead of eight. ``CollectorHealthObservation`` rows go to
``obs_health``, whose subject is the collector itself, so health history
is read and expired exactly like every other observation.

Dependencies:
    vakr.adapters.sqlite.telemetry.codec_rows, vocab: Row helpers and codes.
    vakr.domain: The observation types and enums.

Safety:
    Health rows hold counts and a version string, never configuration
    values or credentials.

Example:
    >>> encode_poll.__name__, encode_health.__name__
    ('encode_poll', 'encode_health')
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.telemetry.codec_rows import (
    EncodedRow,
    UuidOf,
    optional_bytes,
    optional_uuid,
    rebuild,
    stored_int,
)
from vakr.adapters.sqlite.telemetry.vocab import code_of, member_of
from vakr.domain.enums import CollectorState, PollExecution, ReachabilityStrategy
from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId, WorkerId
from vakr.domain.observations import CollectorHealthObservation, DevicePoll
from vakr.domain.quality import Quality

_HEALTH_COUNTS = (
    "clock_offset_us", "event_loop_lag_us", "queue_depth", "queue_capacity", "polls_completed",
    "polls_late", "polls_missed", "spool_bytes", "spool_used_pct", "active_devices",
    "active_interfaces",
)  # fmt: skip


def _offset(moment: datetime | None, base: datetime) -> int | None:
    """Encode an optional time as microseconds after ``base``.

    Args:
        moment: The time, or ``None``.
        base: The scheduled time.

    Returns:
        int | None: The signed offset, or ``None``.

    Raises:
        None
    """
    return None if moment is None else (moment - base) // timedelta(microseconds=1)


def _moment(offset: object, base: datetime) -> datetime | None:
    """Decode an optional offset from the scheduled time.

    Args:
        offset: Column value, or ``None``.
        base: The scheduled time.

    Returns:
        datetime | None: The time, or ``None``.

    Raises:
        StorageError: If the offset is not an integer.
    """
    return None if offset is None else base + timedelta(microseconds=stored_int(offset))


def encode_poll(o: DevicePoll) -> EncodedRow:
    """Encode a poll record.

    Args:
        o: The poll.

    Returns:
        EncodedRow: Values in ``layout.COLUMNS["obs_poll"]`` order.

    Raises:
        KeyError: If an enum member has no pinned code.
    """
    values = (
        o.poll_id.bytes, o.configured_interval_s, o.effective_interval_s,
        code_of(o.reachability), code_of(o.execution), optional_bytes(o.worker_id),
        _offset(o.started_at, o.scheduled_at), _offset(o.completed_at, o.scheduled_at),
        int(o.quality),
    )  # fmt: skip
    return EncodedRow(
        "obs_poll", "device", o.device_id, o.collector_id, encode_time(o.scheduled_at),
        o.observation_id.bytes, values,
    )  # fmt: skip


def decode_poll(row: Sequence[object], uuid_of: UuidOf) -> DevicePoll:
    """Rebuild a poll record from a full ``obs_poll`` row.

    Args:
        row: Every column, in ``layout.all_columns("obs_poll")`` order.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        DevicePoll: The poll as it was written.

    Raises:
        StorageError: If the row is damaged.
    """
    scheduled = decode_time(row[2])
    worker = optional_uuid(row[9])
    return rebuild(
        DevicePoll,
        observation_id=ObservationId(blob_to_uuid(row[3])),
        poll_id=PollId(blob_to_uuid(row[4])),
        collector_id=CollectorId(uuid_of("collector", stored_int(row[1]))),
        device_id=DeviceId(uuid_of("device", stored_int(row[0]))),
        scheduled_at=scheduled,
        configured_interval_s=row[5],
        effective_interval_s=row[6],
        reachability=member_of(ReachabilityStrategy, row[7]),
        execution=member_of(PollExecution, row[8]),
        quality=Quality(stored_int(row[12])),
        worker_id=None if worker is None else WorkerId(worker),
        started_at=_moment(row[10], scheduled),
        completed_at=_moment(row[11], scheduled),
    )


def encode_health(o: CollectorHealthObservation) -> EncodedRow:
    """Encode a collector health snapshot; its subject is the collector.

    Args:
        o: The snapshot.

    Returns:
        EncodedRow: Values in ``layout.COLUMNS["obs_health"]`` order.

    Raises:
        KeyError: If the state has no pinned code.
    """
    values = (
        code_of(o.state), o.software_version, optional_bytes(o.worker_id),
        *(getattr(o, name) for name in _HEALTH_COUNTS), int(o.quality),
    )  # fmt: skip
    return EncodedRow(
        "obs_health", "collector", o.collector_id, o.collector_id, encode_time(o.timestamp),
        o.observation_id.bytes, values,
    )  # fmt: skip


def decode_health(row: Sequence[object], uuid_of: UuidOf) -> CollectorHealthObservation:
    """Rebuild a collector health snapshot from a full ``obs_health`` row.

    Args:
        row: Every column, in ``layout.all_columns("obs_health")`` order.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        CollectorHealthObservation: The snapshot as it was written.

    Raises:
        StorageError: If the row is damaged.
    """
    worker = optional_uuid(row[6])
    counts = {name: row[7 + i] for i, name in enumerate(_HEALTH_COUNTS)}
    return rebuild(
        CollectorHealthObservation,
        observation_id=ObservationId(blob_to_uuid(row[3])),
        collector_id=CollectorId(uuid_of("collector", stored_int(row[1]))),
        timestamp=decode_time(row[2]),
        state=member_of(CollectorState, row[4]),
        software_version=row[5],
        quality=Quality(stored_int(row[18])),
        worker_id=None if worker is None else WorkerId(worker),
        **counts,
    )
