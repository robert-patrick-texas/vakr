"""Byte layout of spool segments: a checked header, then length-delimited records (6.3).

A collector's spool is a directory of append-only segment files. Each starts
with a 32-byte header and continues with records, each a 40-byte frame
header followed by one observation envelope in its canonical JSON form
(``vakr.domain.envelope``). The envelope already carries a SHA-256 over its
own content; the frame adds what the spool needs without parsing JSON:

====== ===== ==========================================================
Offset Bytes Field (big-endian)
====== ===== ==========================================================
0      4     payload length in bytes, 1 to ``MAX_RECORD_BYTES``
4      4     CRC-32 of bytes 0-3, bytes 8-39, and the payload
8      8     record sequence number, contiguous within a spool
16     8     append time, microseconds since the Unix epoch (UTC)
24     16    batch UUID, shared by the records of one append (E.8)
40     n     envelope bytes
====== ===== ==========================================================

The CRC is the IEEE polynomial of ``zlib.crc32``. CRC-32C was preferred, but
neither the standard library nor ``cryptography`` provides it, a pure-Python
version is two orders of magnitude slower, and vakr adds no dependency for
it. The IEEE CRC detects every burst error up to 32 bits and torn writes
just as well at these record sizes, Go reads it with
``hash/crc32.ChecksumIEEE``, and the envelope's SHA-256 remains the
end-to-end integrity check.

Dependencies:
    struct, zlib: Fixed-width fields and the CRC.

Safety:
    Decoding never trusts a length before bounding it, so a corrupt length
    cannot make a reader allocate gigabytes.

Example:
    >>> from uuid import UUID
    >>> raw = encode_record(7, 1_700_000_000_000_000, UUID(int=5), b'{"a":1}')
    >>> h = decode_frame_header(raw[:FRAME_HEADER_BYTES])
    >>> h.seq, h.length, check_frame(raw[:FRAME_HEADER_BYTES], raw[FRAME_HEADER_BYTES:])
    (7, 7, True)
"""

import struct
import zlib
from dataclasses import dataclass
from uuid import UUID

FORMAT_VERSION = 1
"""Segment format version; a change needs an ADR and a reader for old segments (6.3)."""

SEGMENT_MAGIC = b"VAKRSPOL"
SEGMENT_HEADER_BYTES = 32
FRAME_HEADER_BYTES = 40
MAX_RECORD_BYTES = 1 << 20
"""Largest envelope a frame may hold (E.24); real envelopes are about 1 KB."""

_SEGMENT = struct.Struct(">8sHHQq")  # magic, version, header size, first seq, created us
_FRAME_TAIL = struct.Struct(">Qq16s")  # seq, append us, batch UUID
_U32 = struct.Struct(">I")


@dataclass(frozen=True, slots=True)
class SegmentHeader:
    """The fixed header of one segment file. Immutable.

    Attributes:
        first_seq: Sequence number of the segment's first record.
        created_us: Creation time, microseconds since the epoch (UTC).
        version: Format version the segment was written with.
    """

    first_seq: int
    created_us: int
    version: int = FORMAT_VERSION


@dataclass(frozen=True, slots=True)
class FrameHeader:
    """The fixed part of one record frame. Immutable.

    Attributes:
        length: Payload length in bytes; not yet trusted until checked.
        crc: Stored CRC-32.
        seq: Record sequence number.
        append_us: Time the writer accepted the record (UTC microseconds).
        batch_id: Batch UUID shared by one append's records.
    """

    length: int
    crc: int
    seq: int
    append_us: int
    batch_id: UUID


def encode_segment_header(first_seq: int, created_us: int) -> bytes:
    """Encode a segment header with its trailing CRC.

    Args:
        first_seq: Sequence number the segment's first record will carry.
        created_us: Creation time in epoch microseconds.

    Returns:
        bytes: ``SEGMENT_HEADER_BYTES`` bytes.

    Raises:
        struct.error: If a value does not fit its field.
    """
    body = _SEGMENT.pack(SEGMENT_MAGIC, FORMAT_VERSION, SEGMENT_HEADER_BYTES, first_seq,
                         created_us)  # fmt: skip
    return body + _U32.pack(zlib.crc32(body))


def decode_segment_header(data: bytes) -> SegmentHeader | None:
    """Decode and verify a segment header.

    Args:
        data: The first ``SEGMENT_HEADER_BYTES`` bytes of a file (or fewer).

    Returns:
        SegmentHeader | None: The header, or ``None`` if it is short, has
        the wrong magic, size, or version, or fails its CRC.

    Raises:
        None
    """
    if len(data) < SEGMENT_HEADER_BYTES:
        return None
    body, (crc,) = data[: _SEGMENT.size], _U32.unpack(data[_SEGMENT.size : SEGMENT_HEADER_BYTES])
    magic, version, size, first_seq, created_us = _SEGMENT.unpack(body)
    if (magic, version, size) != (SEGMENT_MAGIC, FORMAT_VERSION, SEGMENT_HEADER_BYTES):
        return None
    if zlib.crc32(body) != crc:
        return None
    return SegmentHeader(first_seq, created_us, version)


def encode_record(seq: int, append_us: int, batch_id: UUID, payload: bytes) -> bytes:
    """Frame one envelope.

    Args:
        seq: The record's sequence number.
        append_us: Append time in epoch microseconds.
        batch_id: The append's batch UUID.
        payload: Canonical envelope bytes, 1 to ``MAX_RECORD_BYTES`` long.

    Returns:
        bytes: Frame header plus payload.

    Raises:
        ValueError: If the payload is empty or larger than ``MAX_RECORD_BYTES``.
    """
    if not 0 < len(payload) <= MAX_RECORD_BYTES:
        raise ValueError(f"spool record payload must be 1 to {MAX_RECORD_BYTES} bytes")
    length = _U32.pack(len(payload))
    tail = _FRAME_TAIL.pack(seq, append_us, batch_id.bytes)
    crc = zlib.crc32(payload, zlib.crc32(tail, zlib.crc32(length)))
    return length + _U32.pack(crc) + tail + payload


def decode_frame_header(data: bytes) -> FrameHeader:
    """Decode the fixed part of a frame without checking it.

    Args:
        data: Exactly ``FRAME_HEADER_BYTES`` bytes.

    Returns:
        FrameHeader: The parsed fields.

    Raises:
        struct.error: If ``data`` has the wrong length.
    """
    (length,), (crc,) = _U32.unpack(data[0:4]), _U32.unpack(data[4:8])
    seq, append_us, batch = _FRAME_TAIL.unpack(data[8:FRAME_HEADER_BYTES])
    return FrameHeader(length, crc, seq, append_us, UUID(bytes=batch))


def plausible_length(length: int) -> bool:
    """Report whether a frame's length field could be genuine.

    Args:
        length: The length field.

    Returns:
        bool: ``True`` for 1 to ``MAX_RECORD_BYTES``.

    Raises:
        None
    """
    return 0 < length <= MAX_RECORD_BYTES


def check_frame(header: bytes, payload: bytes) -> bool:
    """Verify a frame's CRC over its header fields and payload.

    Args:
        header: The ``FRAME_HEADER_BYTES`` header bytes.
        payload: The payload bytes the length field announced.

    Returns:
        bool: ``True`` when the stored CRC matches.

    Raises:
        None
    """
    crc = zlib.crc32(payload, zlib.crc32(header[8:FRAME_HEADER_BYTES], zlib.crc32(header[0:4])))
    return crc == _U32.unpack(header[4:8])[0]
