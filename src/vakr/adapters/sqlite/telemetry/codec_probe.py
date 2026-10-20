"""ICMP cycles and SNMP device exchanges to rows and back (blueprint 10.1, 10.2).

Every probe RTT is kept (ICMP-003). In raw storage a probe that got no
reply is ``-1`` (ICMP-004), as the blueprint fixes; the domain carries
``None`` instead, so ``-1`` can never reach a latency or jitter calculation
(ICMP-005). The first five probes, the E.9 default, have their own columns
(``rtt_1_us`` .. ``rtt_5_us``, ``NULL`` for a probe that was never sent);
any further probes are packed into ``rtt_more`` as big-endian signed 64-bit
integers with the same ``-1`` sentinel, so a longer probe train survives
too. Counts and loss are stored beside the probes, as 10.1 lists them.

Dependencies:
    vakr.adapters.sqlite.telemetry.codec_rows, vocab: Row helpers and codes.
    vakr.domain: The observation types and enums.

Safety:
    SNMP rows hold counts, states, and an error code, never community
    strings or other credentials (10.2).

Example:
    >>> probe_columns((900, None, 1100))
    (900, -1, 1100, None, None, None)
    >>> probes_from((900, -1, 1100, None, None), None)
    (900, None, 1100)
"""

from collections.abc import Sequence

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.telemetry.codec_rows import EncodedRow, UuidOf, rebuild, stored_int
from vakr.adapters.sqlite.telemetry.vocab import code_of, member_of
from vakr.domain.enums import AddressFamily, IcmpState, SnmpState, SnmpVersion, UptimeContinuity
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId
from vakr.domain.observations import IcmpObservation, SnmpDeviceObservation
from vakr.domain.quality import Quality

NO_REPLY = -1
"""Raw-storage sentinel for a probe that got no reply (ICMP-004)."""

COLUMN_PROBES = 5
"""Probes stored in their own columns; the E.9 default probe count."""

_PACKED = 8  # bytes per probe in rtt_more


def probe_columns(rtts: Sequence[int | None]) -> tuple[int | bytes | None, ...]:
    """Encode probe RTTs as ``rtt_1_us`` .. ``rtt_5_us`` and ``rtt_more``.

    Args:
        rtts: Per-probe RTT in microseconds, ``None`` for no reply.

    Returns:
        tuple[int | bytes | None, ...]: Five column values (``-1`` for no
        reply, ``None`` past the last probe) and the packed extra probes.

    Raises:
        OverflowError: If an RTT does not fit a signed 64-bit integer.
    """
    raw = [NO_REPLY if rtt is None else rtt for rtt in rtts]
    first = raw[:COLUMN_PROBES]
    more = b"".join(r.to_bytes(_PACKED, "big", signed=True) for r in raw[COLUMN_PROBES:])
    return (*first, *([None] * (COLUMN_PROBES - len(first))), more or None)


def probes_from(columns: Sequence[object], more: object) -> tuple[int | None, ...]:
    """Decode stored probe columns back into per-probe RTTs.

    Args:
        columns: ``rtt_1_us`` .. ``rtt_5_us``.
        more: ``rtt_more``, or ``None``.

    Returns:
        tuple[int | None, ...]: RTTs with ``None`` for no reply.

    Raises:
        StorageError: If a probe column is not an integer or ``rtt_more`` is
            not a whole number of packed probes.
    """
    raw: list[int] = []
    for value in columns:
        if value is None:
            break
        raw.append(stored_int(value))
    if more is not None:
        if not isinstance(more, bytes) or len(more) % _PACKED:
            raise StorageError("stored extra ICMP probes are damaged", retryable=False)
        raw.extend(
            int.from_bytes(more[i : i + _PACKED], "big", signed=True)
            for i in range(0, len(more), _PACKED)
        )
    return tuple(None if r == NO_REPLY else r for r in raw)


def encode_icmp(o: IcmpObservation) -> EncodedRow:
    """Encode an ICMP cycle.

    Args:
        o: The cycle.

    Returns:
        EncodedRow: Values in ``layout.COLUMNS["obs_icmp"]`` order.

    Raises:
        OverflowError: If an RTT does not fit a signed 64-bit integer.
    """
    values = (
        o.poll_id.bytes, code_of(o.address_family), o.source_address, o.target_address,
        *probe_columns(o.rtts_us), o.sent, o.received, o.sent - o.received, o.loss_pct,
        o.min_rtt_us, o.avg_rtt_us, o.median_rtt_us, o.max_rtt_us, o.jitter_us,
        code_of(o.state), int(o.quality),
    )  # fmt: skip
    return EncodedRow(
        "obs_icmp", "device", o.device_id, o.collector_id, encode_time(o.timestamp),
        o.observation_id.bytes, values,
    )  # fmt: skip


def decode_icmp(row: Sequence[object], uuid_of: UuidOf) -> IcmpObservation:
    """Rebuild an ICMP cycle from a full ``obs_icmp`` row.

    Args:
        row: Every column, in ``layout.all_columns("obs_icmp")`` order.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        IcmpObservation: The cycle as it was written.

    Raises:
        StorageError: If the row is damaged or its counts disagree with its probes.
    """
    rtts = probes_from(row[8:13], row[13])
    if len(rtts) != row[14]:
        raise StorageError("a stored ICMP cycle's probe count is damaged", retryable=False)
    return rebuild(
        IcmpObservation,
        observation_id=ObservationId(blob_to_uuid(row[3])),
        poll_id=PollId(blob_to_uuid(row[4])),
        collector_id=CollectorId(uuid_of("collector", stored_int(row[1]))),
        device_id=DeviceId(uuid_of("device", stored_int(row[0]))),
        timestamp=decode_time(row[2]),
        address_family=member_of(AddressFamily, row[5]),
        source_address=row[6],
        target_address=row[7],
        rtts_us=rtts,
        min_rtt_us=row[18], avg_rtt_us=row[19], median_rtt_us=row[20], max_rtt_us=row[21],
        jitter_us=row[22],
        state=member_of(IcmpState, row[23]),
        quality=Quality(stored_int(row[24])),
    )  # fmt: skip


def encode_snmp(o: SnmpDeviceObservation) -> EncodedRow:
    """Encode an SNMP device exchange.

    Args:
        o: The exchange.

    Returns:
        EncodedRow: Values in ``layout.COLUMNS["obs_snmp"]`` order.

    Raises:
        KeyError: If an enum member has no pinned code.
    """
    values = (
        o.poll_id.bytes, code_of(o.snmp_version), o.request_count, o.response_count,
        o.timeout_count, o.retry_count, o.elapsed_ms, o.sys_uptime_ticks,
        code_of(o.uptime_continuity), code_of(o.state), o.error_code, int(o.quality),
    )  # fmt: skip
    return EncodedRow(
        "obs_snmp", "device", o.device_id, o.collector_id, encode_time(o.timestamp),
        o.observation_id.bytes, values,
    )  # fmt: skip


def decode_snmp(row: Sequence[object], uuid_of: UuidOf) -> SnmpDeviceObservation:
    """Rebuild an SNMP device exchange from a full ``obs_snmp`` row.

    Args:
        row: Every column, in ``layout.all_columns("obs_snmp")`` order.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        SnmpDeviceObservation: The exchange as it was written.

    Raises:
        StorageError: If the row is damaged.
    """
    return rebuild(
        SnmpDeviceObservation,
        observation_id=ObservationId(blob_to_uuid(row[3])),
        poll_id=PollId(blob_to_uuid(row[4])),
        collector_id=CollectorId(uuid_of("collector", stored_int(row[1]))),
        device_id=DeviceId(uuid_of("device", stored_int(row[0]))),
        timestamp=decode_time(row[2]),
        snmp_version=member_of(SnmpVersion, row[5]),
        request_count=row[6], response_count=row[7], timeout_count=row[8], retry_count=row[9],
        elapsed_ms=row[10], sys_uptime_ticks=row[11],
        uptime_continuity=member_of(UptimeContinuity, row[12]),
        state=member_of(SnmpState, row[13]),
        error_code=row[14],
        quality=Quality(stored_int(row[15])),
    )  # fmt: skip
