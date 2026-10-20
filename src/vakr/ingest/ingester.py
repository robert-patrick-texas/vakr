"""Batched, idempotent ingestion from the spool into the telemetry store (STORE-009/010/011).

The ingester is the spool's only consumer (6.2). Every ``spool.ingest_interval``
(10 s, E.21), or as soon as ``max_rows`` records are waiting, it reads
committed records in order, decodes them, and calls
``ObservationWriter.write_batch``. Only after that returns, which the port
promises means the backend accepted the batch durably (STORE-010), does it
advance the consumer offset, by whole batches (E.8), and delete segments
whose records are all stored. A crash in between replays the batch on the
next start, and the backend skips what it already has (STORE-009).

When the backend fails, the batch stays in memory and in the spool, the
write is retried with capped exponential backoff and jitter (at most
``WRITE_ATTEMPTS`` per cycle), and health reports the backlog and the
error. Polling is unaffected while the spool has room (STORE-014), and
storage trouble is never reported as a target being down (invariant 2).

Dependencies:
    asyncio, random: Scheduling, worker threads, and backoff jitter.
    vakr.ports.telemetry: ``ObservationWriter`` and its capabilities.
    vakr.ingest: The spool, reader, ingestion steps, and status.

Safety:
    Disk and database calls run in worker threads; the event loop only
    schedules them.

Example:
    >>> WRITE_ATTEMPTS
    5
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from vakr.domain.errors import ValidationError
from vakr.domain.timestamps import to_epoch_us
from vakr.ingest.batching import DecodedBatch
from vakr.ingest.errors import SpoolClosedError
from vakr.ingest.ingest_steps import (
    IngestStatus,
    acknowledge_step,
    backoff_s,
    read_step,
    set_aside_step,
)
from vakr.ingest.reader import SpoolReader
from vakr.ingest.spool import Spool
from vakr.ingest.status import SpoolHealth, spool_health, write_status
from vakr.ports.telemetry import ObservationWriter, TelemetryCapabilities

WRITE_ATTEMPTS = 5
"""Write attempts per cycle (E.24); a failed batch is kept and retried next cycle."""

_WAKE_S = 0.5  # longest a waiting ingester goes without checking for stop
_LOG = logging.getLogger(__name__)


class Ingester:
    """Moves one spool's records into the telemetry store. Use from one event loop.

    Attributes:
        spool: The open spool.
        max_rows: Most observations per write: the smaller of
            ``spool.ingest_max_rows`` and ``capabilities.max_batch_rows``.
        status: Counters, last error, and acknowledged position.
    """

    def __init__(
        self, spool: Spool, writer: ObservationWriter, capabilities: TelemetryCapabilities, *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:  # fmt: skip
        """Bind the ingester; ``start`` positions it after the consumer offset.

        Args:
            spool: An open spool.
            writer: The telemetry backend's batch writer.
            capabilities: The backend's capabilities (``max_batch_rows``).
            sleep: Backoff sleep; ``asyncio.sleep`` by default.
            jitter: Source of numbers from 0 to 1 for backoff jitter.

        Returns:
            None

        Raises:
            None
        """
        self.spool, self._writer = spool, writer
        self.max_rows = min(spool.settings.ingest_max_rows, capabilities.max_batch_rows)
        self.status = IngestStatus()
        self._sleep, self._jitter = sleep or asyncio.sleep, jitter
        self._reader: SpoolReader | None = None
        self._pending: DecodedBatch | None = None
        self._task: asyncio.Task[None] | None = None
        self._cycling = asyncio.Lock()
        self._stopping = False

    async def start(self, *, run: bool = True) -> None:
        """Position after the durable offset, free stored segments, and start the loop.

        Args:
            run: Start the background loop; ``False`` lets tests call ``cycle``.

        Returns:
            None

        Raises:
            SpoolClosedError: If the spool is not open.
        """
        spool = self.spool
        if spool.incidents is None or spool.recovery is None:
            raise SpoolClosedError("open the spool before starting ingestion")
        reader = SpoolReader(spool, spool.incidents, self._now_us)
        offset = spool.recovery.offset
        await asyncio.to_thread(reader.start, None if offset is None else offset.seq)
        self.status.acked_seq = reader.next_seq - 1
        await asyncio.to_thread(reader.release, self.status.acked_seq)
        self._reader = reader
        if run:
            self._task = asyncio.create_task(self._run(), name="vakr-ingest")

    async def stop(self) -> None:
        """Stop the loop, make one last write attempt (16.4 step 7), and close.

        Close the spool first (16.4 step 6), so this last batch includes everything.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._stopping = True
        if self._task is not None:
            await self._task
        if self._reader is not None:
            await self.cycle(attempts=1)
            self._reader.close()

    def health(self) -> SpoolHealth:
        """Current spool and ingestion health.

        Args:
            None

        Returns:
            SpoolHealth: The snapshot.

        Raises:
            None
        """
        return spool_health(self.spool, self.status, self._now_us())

    async def cycle(self, attempts: int = WRITE_ATTEMPTS) -> int:
        """Read, write, and acknowledge one batch.

        Args:
            attempts: Write attempts before giving up until the next cycle.

        Returns:
            int: Records acknowledged or set aside (0 if none, or on failure).

        Raises:
            SpoolClosedError: If ``start`` was not called.
        """
        reader = self._reader
        if reader is None:
            raise SpoolClosedError("start ingestion before running a cycle")
        async with self._cycling:  # one consumer: cycles never overlap (6.2)
            if self._pending is None:
                self._pending = await asyncio.to_thread(read_step, reader, self.max_rows)
            batch = self._pending
            done = batch is None or await self._write(batch, attempts)
            if batch is not None and done:
                await asyncio.to_thread(acknowledge_step, self.spool, reader, self.status,
                                        self._pending or batch, self._now_us())  # fmt: skip
                self._pending = None
            elif batch is not None:
                self.status.oldest_unacked_us = batch.oldest_append_us
            await self._publish()
        return len(batch.frames) if batch is not None and done else 0

    async def _run(self) -> None:
        """Flush on the interval, or at once while ``max_rows`` records wait.

        A backlog is therefore written in full batches back to back, while a
        steady trickle is still batched by the interval (STORE-003).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        clock = self.spool.clock
        while not self._stopping:
            deadline = clock.monotonic() + self.spool.settings.ingest_interval_s
            while not self._stopping and self._waiting() < self.max_rows:
                remaining = deadline - clock.monotonic()
                if remaining <= 0:
                    break
                await self.spool.wait_durable(min(remaining, _WAKE_S))
            if not self._stopping:
                await self.cycle()

    def _waiting(self) -> int:
        """Committed records not yet read.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            None
        """
        reader = self._reader
        return 0 if reader is None else self.spool.durable_seq - reader.next_seq + 1

    async def _write(self, batch: DecodedBatch, attempts: int) -> bool:
        """Write a batch with bounded, backed-off retries.

        Args:
            batch: The decoded batch.
            attempts: Most attempts.

        Returns:
            bool: ``True`` once the backend accepted (or definitively rejected,
            and the batch was quarantined); ``False`` to retry next cycle.

        Raises:
            None
        """
        if not batch.observations:
            return True
        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.to_thread(
                    self._writer.write_batch, batch.batch_id, batch.observations
                )
            except ValidationError as exc:  # no retry can fix a batch the backend calls invalid
                self._pending = await asyncio.to_thread(
                    set_aside_step, self.spool, self.status, batch,
                    f"rejected by storage: {exc}", self._now_us(),
                )  # fmt: skip
                return True
            # The port promises StorageError, but no failure may stop ingestion
            # or lose the batch, so anything else is retried the same way.
            except Exception as exc:  # noqa: BLE001
                text = self.status.failed(exc, self._now_us())
                _LOG.warning("telemetry write failed (attempt %d); observations stay in the "
                             "spool: %s", attempt, text,
                             extra={"event_id": "ingest.write_failed"})  # fmt: skip
                if attempt < attempts and not self._stopping:
                    await self._sleep(backoff_s(attempt, self._jitter()))
                continue
            self.status.succeeded(result)
            return True
        return False

    async def _publish(self) -> None:
        """Write ``status.json`` for ``vakr check health``; failures only cost freshness.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        try:
            await asyncio.to_thread(write_status, self.spool.directory, self.health(),
                                    self.spool.ops)  # fmt: skip
        except OSError as exc:
            _LOG.debug("cannot write spool status (%s)", type(exc).__name__)

    def _now_us(self) -> int:
        """Current time in epoch microseconds.

        Args:
            None

        Returns:
            int: Microseconds since the Unix epoch (UTC).

        Raises:
            None
        """
        return to_epoch_us(self.spool.clock.now_utc())
