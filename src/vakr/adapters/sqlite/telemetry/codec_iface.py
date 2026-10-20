"""Interface samples to ``obs_iface`` rows and back (blueprint 10.3, ADR-021).

Raw IF-MIB counters keep every bit: ``ifHCInOctets`` and friends can reach
2^64 - 1, beyond SQLite's signed 64-bit integers, so they are stored with
the signed mapping of ``vocab.to_signed64`` and restored on read (E.21).
Rates stay full-precision ``REAL`` (E.21 defers rounding). Missing values
are ``NULL`` both ways, so an agent that returned no counter is never read
back as an idle link (invariant 1). ``ifAdminStatus`` and ``ifOperStatus``
are stored as the integers the MIB defines.

Dependencies:
    vakr.adapters.sqlite.telemetry.codec_rows, vocab: Shared row helpers and codes.
    vakr.domain: ``InterfaceObservation`` and its enums.

Safety:
    Interface rows hold numbers and keys only; names, descriptions, and
    tags stay in metadata (STORE-008).

Example:
    >>> from vakr.domain.enums import BaselineState
    >>> encode_interface.__name__, decode_interface.__name__
    ('encode_interface', 'decode_interface')
"""

from collections.abc import Sequence

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.telemetry.codec_rows import (
    EncodedRow,
    UuidOf,
    int_enum,
    rebuild,
    stored_int,
)
from vakr.adapters.sqlite.telemetry.vocab import (
    code_of,
    from_signed64,
    member_of,
    optional_code,
    optional_member,
    to_signed64,
)
from vakr.domain.enums import BaselineState, CounterFamily, IfAdminStatus, IfOperStatus
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId
from vakr.domain.observations import InterfaceObservation
from vakr.domain.quality import Quality

_COUNTERS = ("in_octets", "out_octets", "in_packets", "out_packets", "in_errors", "out_errors")
_RATES = ("in_bps", "out_bps", "in_pps", "out_pps")
_DELTAS = ("in_error_delta", "out_error_delta")
_ERROR_RATES = ("in_error_rate", "out_error_rate")


def encode_interface(o: InterfaceObservation) -> EncodedRow:
    """Encode an interface sample.

    Args:
        o: The sample.

    Returns:
        EncodedRow: Values in ``layout.COLUMNS["obs_iface"]`` order, with the
        device key still to be filled in by the writer.

    Raises:
        KeyError: If an enum member has no pinned code.
    """
    admin = None if o.admin_status is None else int(o.admin_status)
    oper = None if o.oper_status is None else int(o.oper_status)
    values = (
        o.poll_id.bytes, None, o.if_index, admin, oper,
        *(to_signed64(getattr(o, name)) for name in _COUNTERS),
        *(getattr(o, name) for name in _RATES),
        *(to_signed64(getattr(o, name)) for name in _DELTAS),
        *(getattr(o, name) for name in _ERROR_RATES),
        o.sample_elapsed_us, optional_code(o.counter_family), o.counter_discontinuity_ticks,
        code_of(o.baseline_state), int(o.quality),
    )  # fmt: skip
    return EncodedRow(
        "obs_iface", "interface", o.interface_id, o.collector_id,
        encode_time(o.timestamp), o.observation_id.bytes, values, o.device_id,
    )  # fmt: skip


def decode_interface(row: Sequence[object], uuid_of: UuidOf) -> InterfaceObservation:
    """Rebuild an interface sample from a full ``obs_iface`` row.

    Args:
        row: Every column, in ``layout.all_columns("obs_iface")`` order.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        InterfaceObservation: The sample as it was written.

    Raises:
        StorageError: If the row is damaged or names an unknown key.
    """
    counters = {name: from_signed64(row[9 + i]) for i, name in enumerate(_COUNTERS)}
    rates = {name: row[15 + i] for i, name in enumerate(_RATES)}
    deltas = {name: from_signed64(row[19 + i]) for i, name in enumerate(_DELTAS)}
    error_rates = {name: row[21 + i] for i, name in enumerate(_ERROR_RATES)}
    return rebuild(
        InterfaceObservation,
        observation_id=ObservationId(blob_to_uuid(row[3])),
        poll_id=PollId(blob_to_uuid(row[4])),
        collector_id=CollectorId(uuid_of("collector", stored_int(row[1]))),
        device_id=DeviceId(uuid_of("device", stored_int(row[5]))),
        interface_id=InterfaceId(uuid_of("interface", stored_int(row[0]))),
        timestamp=decode_time(row[2]),
        if_index=row[6],
        baseline_state=member_of(BaselineState, row[26]),
        quality=Quality(stored_int(row[27])),
        admin_status=int_enum(IfAdminStatus, row[7]),
        oper_status=int_enum(IfOperStatus, row[8]),
        sample_elapsed_us=row[23],
        counter_family=optional_member(CounterFamily, row[24]),
        counter_discontinuity_ticks=row[25],
        **counters, **rates, **deltas, **error_rates,
    )  # fmt: skip
