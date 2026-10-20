"""The packed payload of one rollup row: every metric of one subject, collector, and bucket.

A ``RollupBucket`` describes one metric. Stored as one SQLite row each, or
as one wide row with a column per field, six interface metrics cost
199-249 bytes per interface-bucket, which puts a node at 52-58 GB against
the 40 GB envelope of blueprint 27.3 (ADR-021 amendment 3). The payload
removes the per-column header bytes and repeated counts:

- byte 0: presence bitmap; bit ``slot - 1`` marks a stored metric
  (``vocab.ROLLUP_SLOTS`` pins the slots);
- byte 1: bit set when the metric reuses the expected count, sample count,
  and quality of the previous stored metric;
- byte 2: bit set when the metric's valid count equals its sample count;
- per stored metric, in slot order: unless shared, ``expected``,
  ``sample``, and ``quality`` as unsigned LEB128 varints; unless equal to
  ``sample``, ``valid``; and when ``valid > 0`` a mode byte (two bits per
  aggregate: minimum, mean, p95, maximum) followed by the values.

Value modes: 0 is ``0.0`` (no bytes), 1 an integral value as a varint, 2 a
big-endian float32, 3 a big-endian float64. Every value takes the shortest
mode that holds it exactly. With ``quantize`` (interface metrics, E.21), a
value that would need more than four bytes is stored as float32 instead,
rounding to 24 significant bits (relative error at most 2^-24, about
6e-8). Rounding to nearest is monotonic, so minimum <= mean, p95 <= maximum
still holds when read back. ICMP metrics are never quantized.

Dependencies:
    struct: float32 and float64 packing.
    vakr.adapters.sqlite.telemetry.payload_reader: Sequential decoding.
    vakr.domain.errors: ``StorageError`` for a damaged payload.

Safety:
    Pure functions. A truncated or malformed payload raises ``StorageError``.

Example:
    >>> a = Aggregates(15, 15, 15, 0.0, 7.0, 13.0, 14.0, 0)
    >>> blob = encode_payload({1: a}, quantize=True)
    >>> decode_payload(blob) == {1: a}, len(blob)
    (True, 10)
"""

import struct
from collections.abc import Mapping
from dataclasses import dataclass

from vakr.adapters.sqlite.telemetry.payload_reader import PayloadReader
from vakr.domain.errors import StorageError

_F32 = struct.Struct(">f")
_F64 = struct.Struct(">d")
_F32_MIN, _F32_MAX = 1.1754943508222875e-38, 3.4028234663852886e38  # normal float32 range
_QUANTIZE_ABOVE = 4  # bytes; values needing more are stored as float32 when quantizing
_VARINT_MAX = 1 << 56  # integral values below this fit in at most 8 varint bytes


@dataclass(frozen=True, slots=True)
class Aggregates:
    """The stored fields of one metric's bucket.

    Attributes:
        expected_count: Samples the schedule intended.
        sample_count: Observations present.
        valid_count: Observations with a value.
        minimum: Smallest value, or ``None`` without valid samples.
        mean: Mean value, or ``None``.
        p95: Nearest-rank 95th percentile, or ``None``.
        maximum: Largest value, or ``None``.
        quality: Union of quality flags, as an integer.
    """

    expected_count: int
    sample_count: int
    valid_count: int
    minimum: float | None
    mean: float | None
    p95: float | None
    maximum: float | None
    quality: int


def _varint(value: int) -> bytes:
    """Encode a non-negative integer as unsigned LEB128.

    Args:
        value: The integer, 0 or more.

    Returns:
        bytes: Seven bits per byte, low bits first.

    Raises:
        None
    """
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _value(value: float, quantize: bool) -> tuple[int, bytes]:
    """Choose a value's mode and bytes as the module docstring describes.

    Args:
        value: A finite, non-negative aggregate.
        quantize: Allow float32 rounding for values that need over four bytes.

    Returns:
        tuple[int, bytes]: The mode (0-3) and its encoded bytes.

    Raises:
        None
    """
    if value == 0.0:
        return 0, b""
    exact: list[tuple[int, bytes]] = [(3, _F64.pack(value))]
    if value.is_integer() and value < _VARINT_MAX:
        exact.append((1, _varint(int(value))))
    in_range = _F32_MIN <= value <= _F32_MAX
    if in_range and _F32.unpack(_F32.pack(value))[0] == value:
        exact.append((2, _F32.pack(value)))
    best = min(exact, key=lambda c: len(c[1]))
    if quantize and in_range and len(best[1]) > _QUANTIZE_ABOVE:
        return 2, _F32.pack(value)
    return best


def encode_payload(slots: Mapping[int, Aggregates], *, quantize: bool) -> bytes:
    """Pack the metrics of one rollup row.

    Args:
        slots: Slot number (1-8) to that metric's aggregates.
        quantize: Store values needing over four bytes as float32 (E.21).

    Returns:
        bytes: The payload.

    Raises:
        None
    """
    present = shared = valid_is_sample = 0
    body = bytearray()
    previous: Aggregates | None = None
    for slot in sorted(slots):
        a, bit = slots[slot], 1 << (slot - 1)
        present |= bit
        same = previous is not None and _context(previous) == _context(a)
        if same:
            shared |= bit
        else:
            body += _varint(a.expected_count) + _varint(a.sample_count) + _varint(a.quality)
        if a.valid_count == a.sample_count:
            valid_is_sample |= bit
        else:
            body += _varint(a.valid_count)
        if a.valid_count > 0:
            body += _values(a, quantize)
        previous = a
    return bytes((present, shared, valid_is_sample)) + bytes(body)


def _context(a: Aggregates) -> tuple[int, int, int]:
    """Return the fields a metric may share with the previous one.

    Args:
        a: The aggregates.

    Returns:
        tuple[int, int, int]: Expected count, sample count, and quality.

    Raises:
        None
    """
    return a.expected_count, a.sample_count, a.quality


def _values(a: Aggregates, quantize: bool) -> bytes:
    """Encode the mode byte and the four aggregates of a metric with valid samples.

    Args:
        a: Aggregates with every value present.
        quantize: As for ``encode_payload``.

    Returns:
        bytes: The mode byte and the values.

    Raises:
        None
    """
    modes, out = 0, bytearray()
    for i, value in enumerate((a.minimum, a.mean, a.p95, a.maximum)):
        mode, data = _value(float(value or 0.0), quantize)
        modes |= mode << (2 * i)
        out += data
    return bytes((modes,)) + bytes(out)


def decode_payload(payload: object, want: int | None = None) -> dict[int, Aggregates]:
    """Unpack a rollup row's metrics.

    Args:
        payload: The stored blob.
        want: Decode only this slot (the others are skipped), or ``None``.

    Returns:
        dict[int, Aggregates]: Slot to aggregates; empty when ``want`` is absent.

    Raises:
        StorageError: If the payload is truncated or malformed.
    """
    if not isinstance(payload, bytes) or len(payload) < 3:
        raise StorageError("a stored rollup payload is damaged", retryable=False)
    reader = PayloadReader(payload, 3)
    present, shared, valid_is_sample = payload[0], payload[1], payload[2]
    found: dict[int, Aggregates] = {}
    context = (0, 0, 0)
    for slot in range(1, 9):
        bit = 1 << (slot - 1)
        if not present & bit:
            continue
        if not shared & bit:
            context = (reader.varint(), reader.varint(), reader.varint())
        valid = context[1] if valid_is_sample & bit else reader.varint()
        values = reader.values() if valid > 0 else (None, None, None, None)
        if want is None or want == slot:
            found[slot] = Aggregates(context[0], context[1], valid, *values, context[2])
    if not reader.done():
        raise StorageError("a stored rollup payload has trailing bytes", retryable=False)
    return found


def decode_slot(payload: object, slot: int) -> Aggregates | None:
    """Decode one metric of a rollup row, stepping over the slots before it.

    Long-range graphs read tens of thousands of rows for one metric, so this
    skips the other slots' values instead of building them.

    Args:
        payload: The stored blob.
        slot: The wanted slot (1-8).

    Returns:
        Aggregates | None: The metric's aggregates, or ``None`` if the row lacks it.

    Raises:
        StorageError: If the payload is truncated or malformed.
    """
    if not isinstance(payload, bytes) or len(payload) < 3:
        raise StorageError("a stored rollup payload is damaged", retryable=False)
    present, shared, valid_is_sample = payload[0], payload[1], payload[2]
    if not present & (1 << (slot - 1)):
        return None
    reader = PayloadReader(payload, 3)
    context = (0, 0, 0)
    for current in range(1, slot + 1):
        bit = 1 << (current - 1)
        if not present & bit:
            continue
        if not shared & bit:
            context = (reader.varint(), reader.varint(), reader.varint())
        valid = context[1] if valid_is_sample & bit else reader.varint()
        if current == slot:
            values = reader.values() if valid > 0 else (None, None, None, None)
            return Aggregates(context[0], context[1], valid, *values, context[2])
        if valid > 0:
            reader.skip_values()
    return None
