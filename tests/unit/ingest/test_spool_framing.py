"""SPOOL-T01: versioned, length-delimited spool records round-trip with checksum validation.

The frame CRC must catch a flipped bit anywhere in a record, header or
payload, and a corrupt length must never make a reader allocate or read past
``MAX_RECORD_BYTES``. The tail classifier decides what recovery may truncate.

Dependencies:
    pytest; vakr.ingest.framing, scan; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_framing.py
"""

import io
from pathlib import Path
from uuid import UUID

import pytest

from tests.unit.ingest.spool_testkit import interface_samples
from vakr.domain.envelope import envelope_from_bytes, envelope_to_bytes, open_envelope, seal
from vakr.domain.ids import BatchId, new_id
from vakr.ingest.framing import (
    FRAME_HEADER_BYTES,
    MAX_RECORD_BYTES,
    SEGMENT_HEADER_BYTES,
    check_frame,
    decode_frame_header,
    decode_segment_header,
    encode_record,
    encode_segment_header,
)
from vakr.ingest.losses import estimate_records
from vakr.ingest.scan import ReadStatus, TailVerdict, classify_tail, read_frame, scan_segment

BATCH = UUID("01929b4a-0000-7000-8000-0000000000b1")


def _segment(path: Path, payloads: list[bytes], first: int = 1) -> bytes:
    """Write a segment file with the given payloads.

    Args:
        path: File to create.
        payloads: Record payloads.
        first: First sequence number.

    Returns:
        bytes: The file's content.

    Raises:
        None
    """
    data = encode_segment_header(first, 1_000) + b"".join(
        encode_record(first + i, 2_000 + i, BATCH, p) for i, p in enumerate(payloads)
    )
    path.write_bytes(data)
    return data


def test_spool_t01_envelope_round_trips_through_a_frame() -> None:
    """SPOOL-T01: an M2 envelope survives framing byte for byte and decodes to the observation.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    (observation,) = interface_samples(1)
    payload = envelope_to_bytes(seal(observation, new_id(BatchId)))
    raw = encode_record(42, 1_760_000_000_000_000, BATCH, payload)
    status, frame = read_frame(io.BytesIO(raw), 0)
    assert status is ReadStatus.RECORD
    assert frame is not None
    assert (frame.header.seq, frame.header.batch_id, frame.payload) == (42, BATCH, payload)
    assert frame.end == len(raw) == FRAME_HEADER_BYTES + len(payload)
    assert open_envelope(envelope_from_bytes(frame.payload)) == observation


def test_spool_t01_every_flipped_bit_is_detected() -> None:
    """SPOOL-T01: flipping any single bit of a frame fails the CRC or the length check.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    raw = encode_record(7, 123, BATCH, b'{"observation_type":"icmp","x":12345}')
    for index in range(len(raw)):
        for bit in range(8):
            damaged = bytearray(raw)
            damaged[index] ^= 1 << bit
            status, _ = read_frame(io.BytesIO(bytes(damaged)), 0)
            assert status in {ReadStatus.CORRUPT, ReadStatus.TORN}, (index, bit)


def test_spool_t01_segment_header_round_trip_and_rejections() -> None:
    """SPOOL-T01: the versioned segment header verifies magic, version, size, and CRC.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    header = encode_segment_header(99, 5_000)
    assert len(header) == SEGMENT_HEADER_BYTES
    decoded = decode_segment_header(header)
    assert decoded is not None
    assert (decoded.first_seq, decoded.created_us) == (99, 5000)
    assert decode_segment_header(header[:-1]) is None
    assert decode_segment_header(b"NOTVAKR!" + header[8:]) is None
    flipped = bytearray(header)
    flipped[20] ^= 0x01
    assert decode_segment_header(bytes(flipped)) is None
    newer = bytearray(header)
    newer[9] = 2  # format version 2: refused, never misread (6.3)
    assert decode_segment_header(bytes(newer)) is None


def test_spool_t01_lengths_are_bounded() -> None:
    """SPOOL-T01: empty or oversized payloads are refused; a huge length reads as corrupt.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="1 to"):
        encode_record(1, 0, BATCH, b"")
    with pytest.raises(ValueError, match="1 to"):
        encode_record(1, 0, BATCH, b"x" * (MAX_RECORD_BYTES + 1))
    raw = bytearray(encode_record(1, 0, BATCH, b"{}"))
    raw[0:4] = (MAX_RECORD_BYTES + 1).to_bytes(4, "big")
    assert read_frame(io.BytesIO(bytes(raw)), 0) == (ReadStatus.CORRUPT, None)
    header = decode_frame_header(bytes(raw[:FRAME_HEADER_BYTES]))
    assert header.length == MAX_RECORD_BYTES + 1
    assert not check_frame(bytes(raw[:FRAME_HEADER_BYTES]), b"{}")


def test_spool_t01_scan_reads_a_clean_segment(tmp_path: Path) -> None:
    """SPOOL-T01: a scan returns every record, the range, and the tail it was asked to keep.

    Args:
        tmp_path: Directory for the segment.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "seg"
    data = _segment(path, [b"{}", b'{"a":1}', b'{"b":2}'], first=10)
    result = scan_segment(path, keep_tail=2)
    assert (result.records, result.first_seq, result.last_seq) == (3, 10, 12)
    assert (result.valid_end, result.size, result.problem) == (len(data), len(data), None)
    assert [f.header.seq for f in result.tail] == [11, 12]


def test_spool_t01_scan_stops_at_a_sequence_jump(tmp_path: Path) -> None:
    """SPOOL-T01: a verified frame with the wrong sequence number counts as corrupt.

    Args:
        tmp_path: Directory for the segment.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "seg"
    data = encode_segment_header(1, 0) + encode_record(1, 0, BATCH, b"{}")
    path.write_bytes(data + encode_record(5, 0, BATCH, b"{}"))
    result = scan_segment(path)
    assert (result.records, result.problem, result.valid_end) == (1, ReadStatus.CORRUPT, len(data))
    path.write_bytes(b"garbage")
    assert scan_segment(path).header is None


@pytest.mark.parametrize(
    ("tail", "verdict"),
    [
        (b"\x00" * 300, TailVerdict.TORN),  # zero-filled by the filesystem after power loss
        (encode_record(4, 0, BATCH, b'{"x":1}')[:20], TailVerdict.TORN),  # cut inside a frame
        (b"\xff" * 50_000, TailVerdict.DAMAGED),  # far more than one record of garbage
    ],
)
def test_spool_t02_tail_classification(tmp_path: Path, tail: bytes, verdict: TailVerdict) -> None:
    """SPOOL-T02: only a plausibly torn final write may be truncated; anything else is kept.

    Args:
        tmp_path: Directory for the segment.
        tail: Bytes after three good records.
        verdict: Expected classification.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "seg"
    good = _segment(path, [b"{}", b"{}", b"{}"])
    path.write_bytes(good + tail)
    result = scan_segment(path)
    assert result.problem is not None
    assert result.valid_end == len(good)
    assert classify_tail(path, result.valid_end, result.problem) is verdict


def test_spool_t02_complete_final_frame_with_bad_crc_is_torn(tmp_path: Path) -> None:
    """SPOOL-T02: a complete last frame whose bytes were half-persisted is a torn write.

    Args:
        tmp_path: Directory for the segment.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "seg"
    good = _segment(path, [b"{}"])
    last = bytearray(encode_record(2, 0, BATCH, b'{"half":"written"}'))
    last[-3:] = b"\x00\x00\x00"
    path.write_bytes(good + bytes(last))
    result = scan_segment(path)
    assert result.problem is ReadStatus.CORRUPT
    assert classify_tail(path, result.valid_end, result.problem) is TailVerdict.TORN
    path.write_bytes(good + bytes(last) + encode_record(3, 0, BATCH, b"{}"))
    assert classify_tail(path, len(good), ReadStatus.CORRUPT) is TailVerdict.DAMAGED


def test_store007_loss_estimates() -> None:
    """STORE-007: lost bytes become at least one estimated record; nothing lost is zero.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert estimate_records(0, 1000.0) == 0
    assert estimate_records(1, 1000.0) == 1
    assert estimate_records(10_640, 0) == 10  # unknown average: about 1 KB envelopes
    assert estimate_records(5_000, 1_000.0) == 5
