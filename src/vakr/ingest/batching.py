"""Turning spooled records into a telemetry write batch, and setting aside what cannot be stored.

Ingestion reads committed records, rebuilds each observation with the M2
envelope codec (checksum verified), and writes them in one
``ObservationWriter.write_batch`` call. A record whose envelope fails to
decode, for example one written by a newer vakr after a downgrade, is
never dropped: its frame is copied verbatim into a quarantine file in spool
format, so a later release can read it, and a loss incident counts it
(STORE-007). The same happens to a batch the backend rejects as invalid,
which no retry can fix.

Dependencies:
    vakr.domain.envelope: Decoding and verifying envelopes.
    vakr.ingest: Frames, segment format, and incidents.

Safety:
    Quarantine files keep the original bytes and mode 0640; decoding
    errors are summarised by type, never by payload.

Example:
    >>> from vakr.ingest.reader import ReadBatch
    >>> decode_batch(ReadBatch((), None, None)).observations
    ()
"""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from vakr.domain.envelope import envelope_from_bytes, open_envelope
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId
from vakr.domain.observations import Observation, observation_type
from vakr.ingest.fileops import FileOps, write_all
from vakr.ingest.framing import encode_record, encode_segment_header
from vakr.ingest.incidents import LossIncident, LossKind
from vakr.ingest.reader import ReadBatch
from vakr.ingest.scan import Frame
from vakr.ingest.segments import QUARANTINE_DIR, ensure_directory


@dataclass(frozen=True, slots=True)
class DecodedBatch:
    """One write batch ready for the telemetry backend. Immutable.

    Attributes:
        batch_id: The batch UUID passed to ``write_batch`` (the first record's).
        observations: Decoded observations, in sequence order.
        frames: Every record read, decodable or not.
        undecodable: Records whose envelope could not be decoded.
        ack_seq: Last sequence number whose whole batch is included, or ``None``.
        ack_batch: That record's batch UUID, or ``None``.
    """

    batch_id: BatchId
    observations: tuple[Observation, ...]
    frames: tuple[Frame, ...]
    undecodable: tuple[Frame, ...]
    ack_seq: int | None
    ack_batch: UUID | None

    @property
    def oldest_append_us(self) -> int | None:
        """Append time of the batch's oldest record.

        Args:
            None

        Returns:
            int | None: Epoch microseconds, or ``None`` for an empty batch.

        Raises:
            None
        """
        return self.frames[0].header.append_us if self.frames else None


def decode_batch(read: ReadBatch) -> DecodedBatch:
    """Decode every record of a read, separating the ones that cannot be decoded.

    Args:
        read: Records from ``SpoolReader.read``.

    Returns:
        DecodedBatch: Observations and undecodable frames.

    Raises:
        None
    """
    observations: list[Observation] = []
    bad: list[Frame] = []
    for frame in read.frames:
        try:
            observations.append(open_envelope(envelope_from_bytes(frame.payload)))
        except ValidationError, ValueError, KeyError, TypeError:
            bad.append(frame)
    first = BatchId(read.frames[0].header.batch_id) if read.frames else BatchId(UUID(int=1))
    return DecodedBatch(first, tuple(observations), read.frames, tuple(bad), read.ack_seq,
                        read.ack_batch)  # fmt: skip


def quarantine_frames(directory: Path, frames: tuple[Frame, ...], label: str,
                      ops: FileOps) -> Path:  # fmt: skip
    """Copy frames verbatim into a spool-format file under ``quarantine/``, durably.

    Args:
        directory: The collector's spool directory.
        frames: The records to keep, in sequence order.
        label: File name prefix, such as ``undecodable``.
        ops: File operations.

    Returns:
        Path: The quarantine file.

    Raises:
        OSError: If it cannot be written.
    """
    target_dir = directory / QUARANTINE_DIR
    ensure_directory(target_dir, ops)
    first, last = frames[0].header.seq, frames[-1].header.seq
    path = target_dir / f"{label}-{first:020d}-{last:020d}.spool"
    data = b"".join(_raw(f) for f in frames)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        write_all(fd, encode_segment_header(first, frames[0].header.append_us) + data, ops)
        ops.fsync(fd)
    finally:
        os.close(fd)
    ops.fsync_dir(target_dir)
    return path


def _raw(frame: Frame) -> bytes:
    """Re-encode a verified frame; its CRC matched, so the bytes equal the original.

    Args:
        frame: A verified frame.

    Returns:
        bytes: Header and payload.

    Raises:
        None
    """
    h = frame.header
    return encode_record(h.seq, h.append_us, h.batch_id, frame.payload)


def set_aside_incident(kind: LossKind, frames: tuple[Frame, ...],
                       observations: tuple[Observation, ...], path: Path, now_us: int,
                       reason: str) -> LossIncident:  # fmt: skip
    """Describe records ingestion could not store and moved to quarantine.

    Args:
        kind: ``undecodable`` or ``rejected_by_storage``.
        frames: The records.
        observations: Those that decoded, for their classes (may be empty).
        path: The quarantine file.
        now_us: Detection time.
        reason: Error type or short reason, without payload.

    Returns:
        LossIncident: An incident with the exact count and sequence range.

    Raises:
        None
    """
    stamps = [f.header.append_us for f in frames]
    return LossIncident.new(
        kind, now_us, first_seq=frames[0].header.seq, last_seq=frames[-1].header.seq,
        start_us=min(stamps), end_us=max(stamps), estimated=len(frames), exact=True,
        classes=[observation_type(o).value for o in observations], segment=path.name,
        detail=f"{len(frames)} record(s) moved to quarantine/{path.name}: {reason}",
    )  # fmt: skip
