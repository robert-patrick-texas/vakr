"""Interface status records to ``obs_ifstat`` rows and back (E.6, E.35, schema 4).

The row mirrors an interface sample's key and identity columns: the
interface's ``series_key`` (whose dictionary entry belongs to the device),
the collector, the Stage 1 time, the observation UUID, then the poll, the
device key the writer fills in, the observed ``ifIndex``, both IF-MIB
statuses as their integers, the baseline flag, and quality.

Dependencies:
    vakr.adapters.sqlite.telemetry.codec_rows: Row helpers.
    vakr.domain: The record and IF-MIB enums.

Safety:
    Pure conversions; a damaged row raises ``StorageError``.

Example:
    >>> encode_status.__name__, decode_status.__name__
    ('encode_status', 'decode_status')
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
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId
from vakr.domain.quality import Quality
from vakr.domain.status_observations import InterfaceStatusObservation


def encode_status(o: InterfaceStatusObservation) -> EncodedRow:
    """Encode an interface status record.

    Args:
        o: The record.

    Returns:
        EncodedRow: Values in ``layout.COLUMNS["obs_ifstat"]`` order, the device key
        still to be filled in by the writer.

    Raises:
        None
    """
    admin = None if o.admin_status is None else int(o.admin_status)
    oper = None if o.oper_status is None else int(o.oper_status)
    values = (o.poll_id.bytes, None, o.if_index, admin, oper, int(o.baseline), int(o.quality))
    return EncodedRow(
        "obs_ifstat", "interface", o.interface_id, o.collector_id,
        encode_time(o.timestamp), o.observation_id.bytes, values, o.device_id,
    )  # fmt: skip


def decode_status(row: Sequence[object], uuid_of: UuidOf) -> InterfaceStatusObservation:
    """Rebuild a status record from a full ``obs_ifstat`` row.

    Args:
        row: Every column, in ``layout.all_columns("obs_ifstat")`` order.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        InterfaceStatusObservation: The record as it was written.

    Raises:
        StorageError: If the row is damaged or names an unknown key.
    """
    return rebuild(
        InterfaceStatusObservation,
        observation_id=ObservationId(blob_to_uuid(row[3])),
        poll_id=PollId(blob_to_uuid(row[4])),
        collector_id=CollectorId(uuid_of("collector", stored_int(row[1]))),
        device_id=DeviceId(uuid_of("device", stored_int(row[5]))),
        interface_id=InterfaceId(uuid_of("interface", stored_int(row[0]))),
        timestamp=decode_time(row[2]),
        if_index=row[6],
        admin_status=int_enum(IfAdminStatus, row[7]),
        oper_status=int_enum(IfOperStatus, row[8]),
        baseline=bool(stored_int(row[9])),
        quality=Quality(stored_int(row[10])),
    )
