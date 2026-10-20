"""Appending framed records to the active segment, with fsync and rotation (E.8).

Only the spool's writer thread uses a ``SegmentWriter``. Each append writes
one batch's frames with a single ``write`` call, so a batch never spans two
segments and a crash cuts at most the end of the batch being written. The
writer does not fsync on every append; the writer thread decides when (group
commit), and ``durable_seq`` advances only after an fsync returns.

Space is checked before writing. Above the size limit an append is refused
with ``SpoolFullError``; if the filesystem itself is full (``ENOSPC``), the
partial write is truncated away so the segment stays well formed, and the
same error is raised. Either way the caller learns the batch is not durable.

Dependencies:
    errno, os: Write errors and truncation.
    vakr.ingest: Framing, segment creation, and usage accounting.

Safety:
    Not thread-safe by design; ``durable_seq`` and ``active_first_seq`` are
    plain integers that other threads may read.

Example:
    >>> SegmentWriter.__name__
    'SegmentWriter'
"""

import errno
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from vakr.domain.timestamps import to_epoch_us
from vakr.ingest.capacity import SpoolUsage
from vakr.ingest.errors import SpoolFullError, SpoolWriteError
from vakr.ingest.framing import SEGMENT_HEADER_BYTES, encode_record
from vakr.ingest.segments import FileOps, create_segment, write_all
from vakr.ingest.settings import MIB, SpoolSettings
from vakr.ports.clock import Clock

_SPACE_ERRORS = {errno.ENOSPC, errno.EDQUOT}
_LOG = logging.getLogger(__name__)


class SegmentWriter:
    """The active segment of one spool and the sequence counter.

    Attributes:
        next_seq: Sequence number the next record will get.
        durable_seq: Highest sequence number covered by a completed fsync.
        unsynced: Records written since the last fsync.
        active_first_seq: First sequence number of the open segment, or ``None``.
        rotate_requested: Set by the reader after damage in the open segment;
            the next append starts a new segment.
    """

    def __init__(
        self,
        directory: Path,
        settings: SpoolSettings,
        ops: FileOps,
        usage: SpoolUsage,
        next_seq: int,
        clock: Clock,
    ) -> None:
        """Prepare to append; the first append creates a segment.

        Args:
            directory: The collector's spool directory.
            settings: Segment size and size limit.
            ops: File operations.
            usage: Shared byte accounting.
            next_seq: First sequence number to assign.
            clock: Time source for segment creation stamps.

        Returns:
            None

        Raises:
            None
        """
        self._dir, self._settings, self._ops, self._usage = directory, settings, ops, usage
        self._clock = clock
        self._fd: int | None = None
        self._size = 0
        self._segment_records = 0
        self.next_seq = next_seq
        self.durable_seq = next_seq - 1
        self.unsynced = 0
        self.active_first_seq: int | None = None
        self.rotate_requested = False

    def append(self, payloads: Sequence[bytes], batch_id: UUID, append_us: int) -> tuple[int, int]:
        """Write one batch's records; they are durable only after ``sync``.

        Args:
            payloads: Envelope bytes, one per record.
            batch_id: The batch UUID shared by these records.
            append_us: Append time in epoch microseconds.

        Returns:
            tuple[int, int]: First and last sequence numbers assigned.

        Raises:
            SpoolFullError: The size limit would be exceeded or the disk is full.
            SpoolWriteError: The write failed for another reason.
            ValueError: If a payload is empty or too large.
        """
        first = self.next_seq
        data = b"".join(
            encode_record(first + i, append_us, batch_id, p) for i, p in enumerate(payloads)
        )
        if not self._usage.fits(len(data) + SEGMENT_HEADER_BYTES):
            limit = self._settings.max_bytes // MIB
            raise SpoolFullError(f"spool {self._dir} reached spool.max_size_mib ({limit} MiB)")
        full = self._segment_records and self._size + len(data) > self._settings.segment_bytes
        if self._fd is None or full or self.rotate_requested:
            self.rotate_requested = False
            self._rotate()
        self._write(data)
        self.next_seq += len(payloads)
        self.unsynced += len(payloads)
        self._segment_records += len(payloads)
        return first, self.next_seq - 1

    def prepare(self) -> None:
        """Create the first segment ahead of the first append, so it does not pay for it.

        Best effort: if creation fails (a full disk, say), the first append
        tries again and reports the error to its caller.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self._fd is None:
            try:
                self._rotate()
            except (SpoolFullError, SpoolWriteError) as exc:
                _LOG.warning("cannot create a spool segment yet: %s", exc,
                             extra={"event_id": "spool.segment_deferred"})  # fmt: skip

    def sync(self) -> int:
        """Fsync the active segment and advance ``durable_seq``.

        Args:
            None

        Returns:
            int: The new ``durable_seq``.

        Raises:
            SpoolWriteError: If the fsync fails; the records since the last
                fsync must then be treated as not durable.
        """
        if self.unsynced and self._fd is not None:
            try:
                self._ops.fsync(self._fd)
            except OSError as exc:
                code = errno.errorcode.get(exc.errno or 0, "unknown error")
                raise SpoolWriteError(f"fsync failed in spool {self._dir} ({code})") from None
        self.durable_seq, self.unsynced = self.next_seq - 1, 0
        return self.durable_seq

    def abandon(self) -> None:
        """Stop using the active segment after a failed fsync; the next append rotates.

        The kernel may have discarded the dirty pages, so the file is never
        trusted for further appends. Its bytes stay for the reader, whose
        CRC check decides record by record.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self._fd is not None:
            os.close(self._fd)
        self._fd, self.unsynced, self.active_first_seq = None, 0, None

    def close(self) -> None:
        """Fsync and close the active segment.

        Args:
            None

        Returns:
            None

        Raises:
            SpoolWriteError: If the final fsync fails.
        """
        try:
            self.sync()
        finally:
            self.abandon()

    def _rotate(self) -> None:
        """Seal the active segment (fsync, close) and create the next one.

        Args:
            None

        Returns:
            None

        Raises:
            SpoolFullError: If the disk is full.
            SpoolWriteError: If the old segment cannot be synced or the new one created.
        """
        if self._fd is not None:
            self.sync()
            self.abandon()
        try:
            created = to_epoch_us(self._clock.now_utc())
            self._fd = create_segment(self._dir, self.next_seq, created, self._ops)
        except OSError as exc:
            raise _write_error(exc, self._dir) from None
        self._size, self._segment_records = SEGMENT_HEADER_BYTES, 0
        self._usage.add(SEGMENT_HEADER_BYTES)
        self.active_first_seq = self.next_seq

    def _write(self, data: bytes) -> None:
        """Write a batch's frames, undoing a partial write on failure.

        Args:
            data: Framed records.

        Returns:
            None

        Raises:
            SpoolFullError: On ``ENOSPC`` or ``EDQUOT``.
            SpoolWriteError: On any other write error.
        """
        fd = self._fd
        if fd is None:  # pragma: no cover - _rotate always opens a segment first
            raise SpoolWriteError(f"no open segment in spool {self._dir}")
        try:
            write_all(fd, data, self._ops)
        except OSError as exc:
            self._undo(fd)
            raise _write_error(exc, self._dir) from None
        self._size += len(data)
        self._usage.add(len(data))

    def _undo(self, fd: int) -> None:
        """Truncate a partial write so the segment ends on a record boundary.

        Args:
            fd: The active segment.

        Returns:
            None

        Raises:
            None
        """
        try:
            os.ftruncate(fd, self._size)
        except OSError as exc:
            # The tail is torn until recovery truncates it; stop appending here.
            _LOG.error("cannot undo a partial spool write in %s (%s)", self._dir,
                       type(exc).__name__, extra={"event_id": "spool.undo_failed"})  # fmt: skip
            self.abandon()


def _write_error(exc: OSError, directory: Path) -> SpoolFullError | SpoolWriteError:
    """Translate an OS write error into the spool's error type.

    Args:
        exc: The OS error.
        directory: The spool directory, for the message.

    Returns:
        SpoolFullError | SpoolWriteError: Full for space errors, else a write error.

    Raises:
        None
    """
    code = errno.errorcode.get(exc.errno or 0, "unknown error")
    if exc.errno in _SPACE_ERRORS:
        return SpoolFullError(f"filesystem full writing spool {directory} ({code})")
    return SpoolWriteError(f"cannot write spool {directory} ({code})")
