"""A cursor over a spool's segments that turns every gap or bad record into a reported loss.

Sequence numbers are contiguous across a spool, so the cursor always knows
which record must come next. It never reads past the writer's
``durable_seq``, so ingestion only ever stores records that would survive a
power cut. Moving between segments it checks that the next one starts where
the last one ended; a later start is a *missing range* with an exact count.
A record that fails its checksum makes its segment *damaged*: the records
before it are kept, the rest are reported lost, and the file is renamed (or,
if the writer still appends to it, the writer is asked to rotate first).

Dependencies:
    vakr.ingest: Framing, scanning, segment files, and incidents.

Safety:
    Used by one ingestion worker at a time; it shares nothing mutable with
    the writer except reading ``durable_seq`` and ``active_first_seq``.

Example:
    >>> SegmentCursor.__name__
    'SegmentCursor'
"""

import logging
from collections.abc import Callable
from typing import BinaryIO

from vakr.ingest.framing import SEGMENT_HEADER_BYTES, decode_segment_header
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.incidents import LossIncident, LossKind
from vakr.ingest.losses import estimate_records
from vakr.ingest.scan import Frame, ReadStatus, read_frame
from vakr.ingest.segments import SegmentRef, list_segments, mark_damaged, segment_name
from vakr.ingest.view import SpoolView

_LOG = logging.getLogger(__name__)


class SegmentCursor:
    """Position in a spool: segment, byte offset, and next sequence number. Mutable.

    Attributes:
        next_seq: Sequence number of the next record to return.
        finished: Segments read to their end, with their last sequence number.
        known_position: Whether ``next_seq`` came from a consumer offset, so a
            later-starting segment proves records are missing.
    """

    def __init__(self, spool: SpoolView, log: IncidentLog, now_us: Callable[[], int]) -> None:
        """Bind the cursor; ``enter`` or ``open`` positions it.

        Args:
            spool: The spool.
            log: Its incident log.
            now_us: Returns the current time in epoch microseconds.

        Returns:
            None

        Raises:
            None
        """
        self._spool, self._log, self._now_us = spool, log, now_us
        self._current: SegmentRef | None = None
        self._file: BinaryIO | None = None
        self._offset = 0
        self._gap_explained = False
        self.next_seq = 1
        self.finished: list[tuple[SegmentRef, int]] = []
        self.known_position = True

    def take(self) -> Frame | None:
        """Return the next committed record, crossing segments and reporting loss.

        Args:
            None

        Returns:
            Frame | None: The record numbered ``next_seq``, or ``None`` if it
            is not committed yet.

        Raises:
            OSError: If a segment cannot be read.
        """
        while self.next_seq <= self._spool.durable_seq:
            if self._file is None and not self._advance():
                return None
            if self._file is None:
                continue
            status, frame = read_frame(self._file, self._offset)
            if status is ReadStatus.RECORD and frame and frame.header.seq == self.next_seq:
                self._offset, self.next_seq = frame.end, self.next_seq + 1
                return frame
            if status is ReadStatus.END:
                self._finish()
            else:
                self._damaged()
        return None

    def enter(self, ref: SegmentRef) -> None:
        """Start reading ``ref`` at its first record, reporting a gap before it.

        Args:
            ref: The next segment.

        Returns:
            None

        Raises:
            OSError: If it cannot be opened.
        """
        if ref.first_seq > self.next_seq and self.known_position and not self._gap_explained:
            self._record(LossKind.MISSING_RANGE, ref.first_seq, ref.first_seq - self.next_seq,
                         exact=True, last=ref.first_seq - 1,
                         detail="spool records missing before this segment")  # fmt: skip
        elif ref.first_seq < self.next_seq:
            _LOG.error("spool segment %s starts before sequence %d", ref.name, self.next_seq,
                       extra={"event_id": "spool.sequence_overlap"})  # fmt: skip
        self._gap_explained, self.known_position = False, True
        self.next_seq = ref.first_seq
        self.open(ref)

    def open(self, ref: SegmentRef) -> None:
        """Open a segment after its header, keeping ``next_seq``; a bad header is damage.

        Args:
            ref: The segment.

        Returns:
            None

        Raises:
            OSError: If it cannot be opened.
        """
        self.close()
        self._current = ref
        self._file = ref.path.open("rb")
        header = decode_segment_header(self._file.read(SEGMENT_HEADER_BYTES))
        self._offset = SEGMENT_HEADER_BYTES
        if header is None or header.first_seq != ref.first_seq:
            self._damaged()

    def close(self) -> None:
        """Close the open segment file.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self._file is not None:
            self._file.close()
            self._file = None

    def _advance(self) -> bool:
        """Move to the segment after the current one.

        Args:
            None

        Returns:
            bool: ``False`` if there is no later segment yet.

        Raises:
            OSError: If the directory cannot be read.
        """
        floor = -1 if self._current is None else self._current.first_seq
        later = [s for s in list_segments(self._spool.directory) if s.first_seq > floor]
        if not later:
            return False
        self.enter(later[0])
        return True

    def _finish(self) -> None:
        """Mark the current segment read to its end.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self._current is not None:
            self.finished.append((self._current, self.next_seq - 1))
        self.close()

    def _damaged(self) -> None:
        """Stop reading a segment at a bad record and report what follows as lost.

        Args:
            None

        Returns:
            None

        Raises:
            OSError: If the segment cannot be renamed.
        """
        ref = self._current
        if ref is None:  # pragma: no cover - only called with a segment open
            return
        lost = ref.path.stat().st_size - self._offset
        self.close()
        if ref.first_seq == self._spool.active_first_seq:
            self._spool.request_rotation()
            ref = SegmentRef(ref.path, ref.first_seq, damaged=True)  # quarantined when released
        else:
            ref = mark_damaged(ref, self._spool.ops)
        self._current = ref
        self._record(
            LossKind.DAMAGED_SEGMENT,
            ref.first_seq,
            estimate_records(lost, 0),
            detail=f"checksum failure; {lost} bytes after the last good record kept",
        )
        self._gap_explained = True
        self.finished.append((ref, self.next_seq - 1))

    def _record(self, kind: LossKind, segment: int, count: int, *, exact: bool = False,
                last: int | None = None, detail: str) -> None:  # fmt: skip
        """Record a loss found while reading, starting at ``next_seq``.

        Args:
            kind: Why.
            segment: First sequence number of the segment involved; incidents
                use its original ``.spool`` name, so a later rename cannot make
                one finding look like two.
            count: Records lost, exact or estimated.
            exact: Whether ``count`` is exact.
            last: Last lost sequence number, if known.
            detail: Operator-readable sentence.

        Returns:
            None

        Raises:
            None
        """
        self._log.record(LossIncident.new(
            kind, self._now_us(), first_seq=self.next_seq, last_seq=last, estimated=count,
            exact=exact, segment=segment_name(segment), detail=detail,
        ))  # fmt: skip
