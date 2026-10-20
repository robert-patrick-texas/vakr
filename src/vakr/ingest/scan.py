"""Reading frames back and judging what a damaged end of a segment means (SPOOL-T01/T02).

After a power cut the last segment can end in a *torn* record: a write the
fsync never covered, cut short or zero-filled by the filesystem. That is the
expected price of group commit (E.8), so recovery truncates it and records
the loss. A checksum failure anywhere else means the disk or something
outside vakr changed bytes that were durable; those bytes are kept, the
segment is marked damaged, and the loss is reported with its range. When
the evidence is ambiguous, the scan chooses *damaged*, which keeps bytes.

Dependencies:
    collections.deque: Bounded memory of a segment's last records.
    vakr.ingest.framing: Header and frame decoding.

Safety:
    Reads are bounded by ``MAX_RECORD_BYTES`` per frame; a scan keeps at
    most ``keep_tail`` frames in memory.

Example:
    >>> import io
    >>> read_frame(io.BytesIO(b""), 0)
    (<ReadStatus.END: 'end'>, None)
"""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from vakr.ingest.framing import (
    FRAME_HEADER_BYTES,
    SEGMENT_HEADER_BYTES,
    FrameHeader,
    SegmentHeader,
    check_frame,
    decode_frame_header,
    decode_segment_header,
    plausible_length,
)

_ZERO_CHUNK = 1 << 16


class ReadStatus(StrEnum):
    """Outcome of reading one frame position."""

    RECORD = "record"
    END = "end"
    TORN = "torn"
    CORRUPT = "corrupt"


class TailVerdict(StrEnum):
    """What a bad frame at some offset means for its segment."""

    TORN = "torn"
    DAMAGED = "damaged"


@dataclass(frozen=True, slots=True)
class Frame:
    """One verified record read from a segment. Immutable.

    Attributes:
        header: The frame's fixed fields.
        payload: Envelope bytes.
        offset: Byte offset of the frame in its segment.
    """

    header: FrameHeader
    payload: bytes
    offset: int

    @property
    def end(self) -> int:
        """Byte offset just past this frame.

        Args:
            None

        Returns:
            int: ``offset`` plus header and payload size.

        Raises:
            None
        """
        return self.offset + FRAME_HEADER_BYTES + len(self.payload)


@dataclass(frozen=True, slots=True)
class SegmentScan:
    """Result of reading a whole segment. Immutable.

    Attributes:
        header: The verified header, or ``None`` if it is unreadable.
        records: Verified records read.
        first_seq: First record's sequence number, or ``None`` if none.
        last_seq: Last verified record's sequence number, or ``None``.
        valid_end: Byte offset just past the last verified record.
        size: File size in bytes.
        problem: ``None`` for a clean end, else the first bad frame's status.
        tail: Up to ``keep_tail`` last verified frames, oldest first.
    """

    header: SegmentHeader | None
    records: int
    first_seq: int | None
    last_seq: int | None
    valid_end: int
    size: int
    problem: ReadStatus | None
    tail: tuple[Frame, ...]


def read_frame(f: BinaryIO, offset: int) -> tuple[ReadStatus, Frame | None]:
    """Read and verify the frame at the file's current position.

    Args:
        f: Binary file positioned at ``offset``.
        offset: The position, recorded in the frame.

    Returns:
        tuple[ReadStatus, Frame | None]: ``RECORD`` with the frame;
        ``END`` at a clean end; ``TORN`` when the file ends inside the
        frame; ``CORRUPT`` for an impossible length or a CRC mismatch.

    Raises:
        OSError: If the file cannot be read.
    """
    head = f.read(FRAME_HEADER_BYTES)
    if not head:
        return ReadStatus.END, None
    if len(head) < FRAME_HEADER_BYTES:
        return ReadStatus.TORN, None
    header = decode_frame_header(head)
    if not plausible_length(header.length):
        return ReadStatus.CORRUPT, None
    payload = f.read(header.length)
    if len(payload) < header.length:
        return ReadStatus.TORN, None
    if not check_frame(head, payload):
        return ReadStatus.CORRUPT, None
    return ReadStatus.RECORD, Frame(header, payload, offset)


def scan_segment(path: Path, *, keep_tail: int = 0) -> SegmentScan:
    """Read every frame of a segment and stop at the first bad one.

    Sequence numbers must rise by one from the header's ``first_seq``; a
    verified frame out of order counts as corrupt.

    Args:
        path: The segment file.
        keep_tail: How many of the last verified frames to return.

    Returns:
        SegmentScan: What was found.

    Raises:
        OSError: If the file cannot be read.
    """
    size = path.stat().st_size
    tail: deque[Frame] = deque(maxlen=max(keep_tail, 1))
    with path.open("rb") as f:
        header = decode_segment_header(f.read(SEGMENT_HEADER_BYTES))
        if header is None:
            return SegmentScan(None, 0, None, None, 0, size, ReadStatus.CORRUPT, ())
        offset, count, problem = SEGMENT_HEADER_BYTES, 0, None
        while True:
            status, frame = read_frame(f, offset)
            if status is ReadStatus.RECORD and frame is not None:
                if frame.header.seq != header.first_seq + count:
                    status = ReadStatus.CORRUPT
                else:
                    count, offset = count + 1, frame.end
                    tail.append(frame)
                    continue
            problem = None if status is ReadStatus.END else status
            break
    first = header.first_seq if count else None
    last = header.first_seq + count - 1 if count else None
    kept = tuple(tail)[-keep_tail:] if keep_tail and count else ()
    return SegmentScan(header, count, first, last, offset, size, problem, kept)


def classify_tail(path: Path, offset: int, status: ReadStatus) -> TailVerdict:
    """Decide whether the bad bytes from ``offset`` to the end are a torn final write.

    Torn: the file ends inside the frame, everything left is zero, or one
    complete frame fails its CRC and ends exactly at the end of the file.
    Anything else, such as more than one record's worth of bad bytes, is
    damage.

    Args:
        path: The last segment of a spool.
        offset: Byte offset of the first bad frame.
        status: How reading that frame failed.

    Returns:
        TailVerdict: ``TORN`` (safe to truncate) or ``DAMAGED`` (keep).

    Raises:
        OSError: If the file cannot be read.
    """
    if status is ReadStatus.TORN:
        return TailVerdict.TORN
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(offset)
        if _all_zero(f):
            return TailVerdict.TORN
        f.seek(offset)
        head = f.read(FRAME_HEADER_BYTES)
    if len(head) == FRAME_HEADER_BYTES:
        length = decode_frame_header(head).length
        if plausible_length(length) and offset + FRAME_HEADER_BYTES + length == size:
            return TailVerdict.TORN
    return TailVerdict.DAMAGED


def _all_zero(f: BinaryIO) -> bool:
    """Report whether the rest of a file is zero bytes, reading in bounded chunks.

    Args:
        f: Binary file at the position to check from.

    Returns:
        bool: ``True`` if every remaining byte is zero.

    Raises:
        OSError: If the file cannot be read.
    """
    while chunk := f.read(_ZERO_CHUNK):
        if chunk.count(0) != len(chunk):
            return False
    return True
