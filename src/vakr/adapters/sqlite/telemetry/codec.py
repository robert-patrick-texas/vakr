"""Encode any observation for storage and decode any native row (blueprint 10.1-10.4).

The writer and the readers go through these two functions only, so each
observation type has exactly one encoder and one decoder, and adding a
type means adding one entry to each table.

Dependencies:
    vakr.adapters.sqlite.telemetry.codec_*: The per-type codecs.
    vakr.domain.observations: The observation union and its type names.

Safety:
    ``encode`` validates the type before anything touches the database, so
    a batch holding a non-observation is refused whole (STORE-010).

Example:
    >>> sorted(DECODERS)
    ['obs_health', 'obs_icmp', 'obs_iface', 'obs_poll', 'obs_snmp']
"""

from collections.abc import Callable, Sequence

from vakr.adapters.sqlite.telemetry.codec_iface import decode_interface, encode_interface
from vakr.adapters.sqlite.telemetry.codec_poll import (
    decode_health,
    decode_poll,
    encode_health,
    encode_poll,
)
from vakr.adapters.sqlite.telemetry.codec_probe import (
    decode_icmp,
    decode_snmp,
    encode_icmp,
    encode_snmp,
)
from vakr.adapters.sqlite.telemetry.codec_rows import EncodedRow, UuidOf
from vakr.adapters.sqlite.telemetry.codec_status import decode_status, encode_status
from vakr.domain.errors import ValidationError
from vakr.domain.observations import (
    CollectorHealthObservation,
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    Observation,
    SnmpDeviceObservation,
)
from vakr.domain.status_observations import InterfaceStatusObservation

DECODERS: dict[str, Callable[[Sequence[object], UuidOf], Observation]] = {
    "obs_iface": decode_interface,
    "obs_icmp": decode_icmp,
    "obs_snmp": decode_snmp,
    "obs_poll": decode_poll,
    "obs_health": decode_health,
    "obs_ifstat": decode_status,
}
"""Native family to its row decoder."""


def encode(observation: Observation) -> EncodedRow:
    """Encode one observation of any type.

    Args:
        observation: The observation.

    Returns:
        EncodedRow: Its row, keys still as UUIDs.

    Raises:
        ValidationError: If ``observation`` is not an observation, or a
            value cannot be stored (an RTT beyond 64 bits).
    """
    try:
        match observation:
            case InterfaceObservation():
                return encode_interface(observation)
            case IcmpObservation():
                return encode_icmp(observation)
            case SnmpDeviceObservation():
                return encode_snmp(observation)
            case DevicePoll():
                return encode_poll(observation)
            case CollectorHealthObservation():
                return encode_health(observation)
            case InterfaceStatusObservation():
                return encode_status(observation)
    except OverflowError:
        raise ValidationError("an observation has a number too large to store") from None
    raise ValidationError(f"{type(observation).__name__} is not an observation")


def decode(family: str, row: Sequence[object], uuid_of: UuidOf) -> Observation:
    """Decode one full native row.

    Args:
        family: The row's native family.
        row: Every column of the family, keys first.
        uuid_of: Resolves dictionary keys to UUIDs.

    Returns:
        Observation: The observation as it was written.

    Raises:
        StorageError: If the row is damaged.
    """
    return DECODERS[family](row, uuid_of)
