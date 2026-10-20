"""Reading committed spool records for ingestion, and freeing what is stored (SPOOL-T04, E.8).

``SpoolReader`` is the spool's only consumer (6.2). It resumes after the
durable consumer offset, reads committed records in sequence order, and
reports for each read how far *whole batches* are complete: an append's
records are acknowledged together or not at all (E.8). ``release`` deletes a
segment only when every record in it is acknowledged and the writer is no
longer appending to it; a damaged segment goes to ``quarantine/`` instead.

Dependencies:
    vakr.ingest.cursor: Segment traversal, gap and damage detection.

Safety:
    One consumer per spool; calls must not overlap.

Example:
    >>> ReadBatch((), None, None).frames
    ()
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from vakr.ingest.cursor import SegmentCursor
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.scan import Frame
from vakr.ingest.segments import list_segments, remove_segment
from vakr.ingest.view import SpoolView


@dataclass(frozen=True, slots=True)
class ReadBatch:
    """Records read in one call, with the point up to which whole batches are complete.

    Attributes:
        frames: Records in sequence order.
        ack_seq: Last sequence number whose batch ends inside ``frames``, or ``None``.
        ack_batch: That record's batch UUID, or ``None``.
    """

    frames: tuple[Frame, ...]
    ack_seq: int | None
    ack_batch: UUID | None


class SpoolReader:
    """Consumer of one spool's committed records. Mutable; one caller at a time."""

    def __init__(self, spool: SpoolView, log: IncidentLog, now_us: Callable[[], int]) -> None:
        """Bind the reader; ``start`` positions it.

        Args:
            spool: The spool to consume.
            log: Its incident log.
            now_us: Returns the current time in epoch microseconds.

        Returns:
            None

        Raises:
            None
        """
        self._spool = spool
        self._cursor = SegmentCursor(spool, log, now_us)
        self._peeked: Frame | None = None

    @property
    def next_seq(self) -> int:
        """Sequence number of the next record ``read`` would return.

        Args:
            None

        Returns:
            int: The sequence number.

        Raises:
            None
        """
        return self._cursor.next_seq - (1 if self._peeked is not None else 0)

    def start(self, acked: int | None) -> None:
        """Position the reader just after the last acknowledged record.

        Args:
            acked: The consumer offset, or ``None`` to start at the oldest record.

        Returns:
            None

        Raises:
            OSError: If the spool cannot be read.
        """
        cursor = self._cursor
        segments = list_segments(self._spool.directory)
        cursor.known_position = acked is not None
        cursor.next_seq = (acked or 0) + 1
        if not segments:
            return
        if acked is None:
            cursor.next_seq = segments[0].first_seq
        before = [s for s in segments if s.first_seq <= cursor.next_seq]
        if not before:
            cursor.enter(segments[0])
            return
        for ref in before[:-1]:  # wholly older than the offset: already acknowledged
            cursor.finished.append((ref, before[-1].first_seq - 1))
        target, cursor.next_seq = cursor.next_seq, before[-1].first_seq
        cursor.open(before[-1])
        while cursor.next_seq < target and cursor.take() is not None:
            pass  # records up to the offset are already stored

    def read(self, limit: int) -> ReadBatch:
        """Read up to ``limit`` committed records.

        Args:
            limit: Most records to return.

        Returns:
            ReadBatch: The records and their whole-batch acknowledgement point.

        Raises:
            OSError: If a segment cannot be read.
        """
        frames: list[Frame] = []
        while len(frames) < limit and (frame := self._take()) is not None:
            frames.append(frame)
        following = self._peek()
        ack: Frame | None = None
        for index, frame in enumerate(frames):
            after = frames[index + 1] if index + 1 < len(frames) else following
            if after is None or after.header.batch_id != frame.header.batch_id:
                ack = frame
        if ack is None:
            return ReadBatch(tuple(frames), None, None)
        return ReadBatch(tuple(frames), ack.header.seq, ack.header.batch_id)

    def oldest_append_us(self) -> int | None:
        """Return the append time of the next unread committed record.

        Args:
            None

        Returns:
            int | None: Epoch microseconds, or ``None`` if none is waiting.

        Raises:
            OSError: If a segment cannot be read.
        """
        frame = self._peek()
        return None if frame is None else frame.header.append_us

    def release(self, acked: int) -> int:
        """Delete (or quarantine, if damaged) segments whose records are all acknowledged.

        Args:
            acked: The durable consumer offset.

        Returns:
            int: Bytes removed from the spool's segment total.

        Raises:
            OSError: If a file cannot be removed.
        """
        freed, keep = 0, []
        for ref, last in self._cursor.finished:
            if last <= acked and ref.first_seq != self._spool.active_first_seq:
                freed += remove_segment(ref, self._spool.ops)
            else:
                keep.append((ref, last))
        self._cursor.finished = keep
        self._spool.usage.remove(freed)
        return freed

    def close(self) -> None:
        """Close the open segment file.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._cursor.close()

    def _peek(self) -> Frame | None:
        """Read the next record without consuming it.

        Args:
            None

        Returns:
            Frame | None: The record, or ``None`` if none is committed.

        Raises:
            OSError: If a segment cannot be read.
        """
        if self._peeked is None:
            self._peeked = self._cursor.take()
        return self._peeked

    def _take(self) -> Frame | None:
        """Consume the next record, the peeked one first.

        Args:
            None

        Returns:
            Frame | None: The record, or ``None`` if none is committed.

        Raises:
            OSError: If a segment cannot be read.
        """
        frame, self._peeked = self._peeked, None
        return frame if frame is not None else self._cursor.take()
