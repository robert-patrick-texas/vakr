"""The blocking steps of ingestion and the counters they keep (STORE-009, STORE-010).

Each step here touches the disk and runs in a worker thread; ``Ingester``
only schedules them. *Acknowledging* a stored batch is the order that makes
replay safe: first set aside undecodable records (so nothing is skipped
silently), then durably advance the consumer offset by whole batches, and
only then delete the segments that offset fully covers. A crash between any
two steps replays at most that batch, which the backend deduplicates.

Dependencies:
    vakr.ingest: Reader, batching, offsets, quarantine, incidents.
    vakr.ports.telemetry: ``WriteResult``.

Safety:
    Error text is a ``StorageError`` message (operator-safe by the port
    contract) or only an exception type name.

Example:
    >>> backoff_s(1, 0.5), backoff_s(10, 0.5)
    (1.0, 60.0)
"""

from dataclasses import dataclass

from vakr.domain.errors import StorageError
from vakr.ingest.batching import DecodedBatch, decode_batch, quarantine_frames, set_aside_incident
from vakr.ingest.incidents import LossKind
from vakr.ingest.reader import SpoolReader
from vakr.ingest.segments import quarantine_bytes
from vakr.ingest.spool import Spool
from vakr.ingest.state import ConsumerOffset, save_offset
from vakr.ports.telemetry import WriteResult

RETRY_BASE_S = 1.0
RETRY_CAP_S = 60.0


@dataclass(slots=True)
class IngestStatus:
    """Counters the ingester keeps. Mutable; updated by one ingester at a time.

    Attributes:
        acked_seq: Last sequence number durably acknowledged.
        batches: Write batches acknowledged.
        inserted: Observations stored for the first time.
        duplicates: Observations the backend already had (replays, STORE-009).
        set_aside: Records moved to quarantine instead of stored.
        last_write_us: When the last batch was acknowledged.
        lag_s: Append-to-acknowledge time of the newest acknowledged record.
        oldest_unacked_us: Append time of the oldest unacknowledged record.
        last_error: Last storage error, operator-safe text.
        last_error_us: When it happened.
        consecutive_failures: Failed write attempts since the last success.
        storage_available: Result of the last write attempt; ``None`` before any.
    """

    acked_seq: int = 0
    batches: int = 0
    inserted: int = 0
    duplicates: int = 0
    set_aside: int = 0
    last_write_us: int | None = None
    lag_s: float | None = None
    oldest_unacked_us: int | None = None
    last_error: str | None = None
    last_error_us: int | None = None
    consecutive_failures: int = 0
    storage_available: bool | None = None

    def failed(self, exc: Exception, now_us: int) -> str:
        """Record a failed write attempt.

        Args:
            exc: The error.
            now_us: When it happened.

        Returns:
            str: The operator-safe error text recorded.

        Raises:
            None
        """
        text = (
            f"{type(exc).__name__}: {exc}" if isinstance(exc, StorageError) else type(exc).__name__
        )
        self.storage_available, self.last_error, self.last_error_us = False, text, now_us
        self.consecutive_failures += 1
        return text

    def succeeded(self, result: WriteResult) -> None:
        """Record an accepted write.

        Args:
            result: The backend's result.

        Returns:
            None

        Raises:
            None
        """
        self.storage_available, self.consecutive_failures = True, 0
        self.inserted += result.inserted
        self.duplicates += result.duplicates


def backoff_s(attempt: int, jitter: float) -> float:
    """Delay before retry ``attempt + 1``: doubling from 1 s, capped at 60 s, ±20% jitter (E.24).

    Args:
        attempt: Attempts made so far (1 or more).
        jitter: A number from 0 to 1.

    Returns:
        float: Seconds to wait.

    Raises:
        None
    """
    return min(RETRY_CAP_S, RETRY_BASE_S * 2 ** (attempt - 1) * (0.8 + 0.4 * jitter))


def read_step(reader: SpoolReader, max_rows: int) -> DecodedBatch | None:
    """Read and decode the next batch.

    Args:
        reader: The spool reader.
        max_rows: Most records to read.

    Returns:
        DecodedBatch | None: The batch, or ``None`` if nothing is committed.

    Raises:
        OSError: If the spool cannot be read.
    """
    read = reader.read(max_rows)
    return decode_batch(read) if read.frames else None


def set_aside_step(
    spool: Spool, status: IngestStatus, batch: DecodedBatch, reason: str, now_us: int
) -> DecodedBatch:
    """Quarantine a whole batch the backend rejected as invalid.

    Args:
        spool: The spool.
        status: Counters to update.
        batch: The rejected batch.
        reason: Operator-safe reason.
        now_us: Current time.

    Returns:
        DecodedBatch: The same records with nothing left to write, ready to acknowledge.

    Raises:
        OSError: If the quarantine file cannot be written.
    """
    path = quarantine_frames(spool.directory, batch.frames, "rejected", spool.ops)
    if spool.incidents is not None:
        spool.incidents.record(set_aside_incident(
            LossKind.REJECTED_BY_STORAGE, batch.frames, batch.observations, path, now_us, reason
        ))  # fmt: skip
    status.set_aside += len(batch.frames)
    return DecodedBatch(batch.batch_id, (), batch.frames, (), batch.ack_seq, batch.ack_batch)


def acknowledge_step(
    spool: Spool, reader: SpoolReader, status: IngestStatus, batch: DecodedBatch, now_us: int
) -> None:
    """Set aside undecodable records, advance the offset, then free covered segments.

    Args:
        spool: The spool.
        reader: Its reader.
        status: Counters to update.
        batch: A batch the backend accepted.
        now_us: Current time.

    Returns:
        None

    Raises:
        OSError: If the offset or a quarantine file cannot be written.
    """
    if batch.undecodable:
        path = quarantine_frames(spool.directory, batch.undecodable, "undecodable", spool.ops)
        if spool.incidents is not None:
            spool.incidents.record(set_aside_incident(
                LossKind.UNDECODABLE, batch.undecodable, (), path, now_us, "envelope not decodable"
            ))  # fmt: skip
        status.set_aside += len(batch.undecodable)
    if batch.ack_seq is not None and batch.ack_seq > status.acked_seq:
        offset = ConsumerOffset(batch.ack_seq, batch.ack_batch, now_us)
        save_offset(spool.directory, offset, spool.ops)
        acked = batch.frames[batch.ack_seq - batch.frames[0].header.seq]
        status.acked_seq, status.batches, status.last_write_us = (
            batch.ack_seq,
            status.batches + 1,
            now_us,
        )
        status.lag_s = max(0, now_us - acked.header.append_us) / 1e6
        reader.release(batch.ack_seq)
    spool.usage.set_quarantine(quarantine_bytes(spool.directory)[1])
    status.oldest_unacked_us = reader.oldest_append_us()
