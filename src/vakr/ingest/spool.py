"""A collector's durable spool: ``await append(...)`` returns once observations are fsynced (E.8).

The spool is the hand-off between polling and storage (STORE-004): once
``append`` returns, the observations survive a power cut. Polling never
waits for the telemetry database (STORE-014), only briefly for the fsync.
The event loop never touches the disk: opening and closing run in worker
threads, and appends reach a writer thread through a queue bounded by
``spool.queue_capacity``, where a producer waits when it is full. At
EXHAUSTED ``admits_new_work()`` turns false so the scheduler stops normal
polling (6.4); a completely full spool makes ``append`` raise
``SpoolFullError`` and records the loss (STORE-007).

Dependencies:
    asyncio: Worker threads, futures, and the durable-progress event.
    vakr.ingest: Directory lifecycle, writer thread, gate, and capacity.

Safety:
    Use from one event loop. The directory lock keeps a second process out.

Example:
    >>> from pathlib import Path
    >>> from vakr.domain.ids import CollectorId, new_id
    >>> from vakr.ingest.settings import SpoolSettings
    >>> spool = Spool(SpoolSettings.defaults(Path("/var/lib/vakr/spool")), new_id(CollectorId))
    >>> spool.is_open
    False
"""

import asyncio
import contextlib
from collections.abc import Sequence

from vakr.domain.ids import BatchId, CollectorId, new_id
from vakr.domain.observations import Observation, observation_type
from vakr.domain.timestamps import to_epoch_us
from vakr.ingest.capacity import CapacityState, SpoolUsage
from vakr.ingest.commit import GroupCommit
from vakr.ingest.errors import SpoolClosedError
from vakr.ingest.fileops import FileOps
from vakr.ingest.gate import AdmissionGate
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.recovery import Recovery
from vakr.ingest.requests import AppendReceipt, AppendRequest
from vakr.ingest.segment_writer import SegmentWriter
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.spool_dir import OpenedDirectory, close_directory, open_directory
from vakr.ingest.writer_thread import WriterThread
from vakr.ports.clock import Clock, SystemClock


class Spool:
    """One collector's spool, opened for appending. Mutable; use from one event loop.

    Attributes:
        settings, collector_id, clock, ops: As given.
        directory: ``settings.root / <collector UUID>``.
        usage: Byte accounting, shared with the reader.
        gate: The bounded in-memory queue (depth, waiting producers, waits).
        thread: The writer thread while open (its ``commit`` has fsync statistics).
        incidents, recovery: The incident log and recovery result, once open.
        appended, refused: Observations made durable or refused since opening.
    """

    def __init__(
        self, settings: SpoolSettings, collector_id: CollectorId, *,
        clock: Clock | None = None, ops: FileOps | None = None,
    ) -> None:  # fmt: skip
        """Describe the spool; ``open`` recovers and starts it.

        Args:
            settings: Spool settings.
            collector_id: The owning collector.
            clock: Time source; the system clocks by default.
            ops: File operations; the real ones by default.

        Returns:
            None

        Raises:
            None
        """
        self.settings, self.collector_id = settings, collector_id
        self.directory = settings.collector_dir(collector_id)
        self.clock, self.ops = clock or SystemClock(), ops or FileOps()
        self.usage = SpoolUsage(settings)
        self.gate = AdmissionGate(settings.queue_capacity)
        self.thread: WriterThread | None = None
        self.incidents: IncidentLog | None = None
        self.recovery: Recovery | None = None
        self.appended = self.refused = 0
        self._opened: OpenedDirectory | None = None
        self._durable = asyncio.Event()
        self._closing = False

    @property
    def is_open(self) -> bool:
        """Whether appends are accepted (between ``open`` and ``close``).

        Args:
            None

        Returns:
            bool: The state.

        Raises:
            None
        """
        return self.thread is not None and not self._closing

    @property
    def durable_seq(self) -> int:
        """Highest sequence number covered by a completed fsync (0 before any).

        Args:
            None

        Returns:
            int: The sequence number.

        Raises:
            None
        """
        if self.thread is not None:
            return self.thread.writer.durable_seq
        return 0 if self.recovery is None else self.recovery.next_seq - 1

    @property
    def active_first_seq(self) -> int | None:
        """First sequence number of the segment being appended to, or ``None``.

        Args:
            None

        Returns:
            int | None: The number.

        Raises:
            None
        """
        return None if self.thread is None else self.thread.writer.active_first_seq

    def admits_new_work(self) -> bool:
        """Whether the scheduler may start new normal polls (6.4: not at EXHAUSTED).

        Args:
            None

        Returns:
            bool: ``False`` when closed or EXHAUSTED.

        Raises:
            None
        """
        return self.is_open and self.usage.state() is not CapacityState.EXHAUSTED

    def request_rotation(self) -> None:
        """Ask the writer to start a new segment before its next append.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self.thread is not None:
            self.thread.writer.rotate_requested = True

    async def open(self) -> Recovery:
        """Lock and recover the directory, then start the writer thread.

        Args:
            None

        Returns:
            Recovery: What recovery found.

        Raises:
            SpoolClosedError: If already open.
            SpoolLockedError: If another process holds the spool.
            OSError: If the directory cannot be prepared.
        """
        if self.thread is not None:
            raise SpoolClosedError("the spool is already open")
        now = to_epoch_us(self.clock.now_utc())
        opened = await asyncio.to_thread(open_directory, self.directory, self.settings, self.ops,
                                         now)  # fmt: skip
        self._opened, self.incidents, self.recovery = opened, opened.incidents, opened.recovery
        self.usage.add(opened.recovery.segment_bytes)
        self.usage.set_quarantine(opened.quarantine_bytes)
        writer = SegmentWriter(self.directory, self.settings, self.ops, self.usage,
                               opened.recovery.next_seq, self.clock)  # fmt: skip
        commit = GroupCommit(self.settings.fsync_interval_s, self.settings.fsync_records)
        self.thread = WriterThread(writer, commit, self.clock, opened.incidents,
                                   asyncio.get_running_loop(), self._settle,
                                   lambda _seq: self._durable.set())  # fmt: skip
        self.thread.start()
        # A reader opening a header-less first segment would quarantine it (E.24).
        await asyncio.to_thread(self.thread.prepared.wait, 30.0)
        return opened.recovery

    async def append(
        self, observations: Sequence[Observation], *, batch_id: BatchId | None = None
    ) -> AppendReceipt:
        """Append observations as one batch and wait until they are durable.

        Args:
            observations: 0 to ``spool.queue_capacity`` observations.
            batch_id: Batch UUID for their envelopes; a new one by default.

        Returns:
            AppendReceipt: Sequence numbers and the wait, once fsynced.

        Raises:
            ValidationError: If an item is not an observation or the batch is too large.
            SpoolFullError: If the spool or the filesystem is full; the loss is recorded.
            SpoolWriteError: If the write or fsync failed; the loss is recorded.
            SpoolClosedError: If the spool is not open.
        """
        thread = self.thread
        if thread is None or self._closing:
            raise SpoolClosedError()
        items = tuple(observations)
        for item in items:
            observation_type(item)
        batch = batch_id if batch_id is not None else new_id(BatchId)
        if not items:
            return AppendReceipt(batch, 0, 0, 0, 0.0)
        await self.gate.acquire(len(items))
        request = AppendRequest(
            items, batch, asyncio.get_running_loop().create_future(), self.clock.monotonic()
        )
        try:
            thread.submit(request)
        except SpoolClosedError:
            self.gate.release(len(items))
            raise
        return await request.future

    async def close(self) -> None:
        """Write what is queued, fsync, mark the stop clean, and unlock (16.4 step 6).

        Args:
            None

        Returns:
            None

        Raises:
            OSError: If the clean-stop marker cannot be written.
        """
        thread, opened = self.thread, self._opened
        if thread is None or opened is None or self._closing:
            return
        self._closing = True
        thread.stop()
        await asyncio.to_thread(thread.join)
        await asyncio.sleep(0)  # let the thread's last callbacks run
        last = thread.writer.next_seq - 1 if thread.closed_cleanly else None
        now = to_epoch_us(self.clock.now_utc())
        await asyncio.to_thread(close_directory, self.directory, opened.lock_fd, last,
                                self.ops, now)  # fmt: skip

    async def wait_durable(self, timeout: float) -> None:
        """Wait until more records become durable, or ``timeout`` seconds pass.

        Args:
            timeout: Seconds to wait at most.

        Returns:
            None

        Raises:
            None
        """
        self._durable.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._durable.wait(), timeout)

    def _settle(self, request: AppendRequest, outcome: AppendReceipt | BaseException) -> None:
        """Resolve an append on the event loop and free its queue room.

        Args:
            request: The append.
            outcome: Its receipt or error.

        Returns:
            None

        Raises:
            None
        """
        self.gate.release(len(request.observations))
        if isinstance(outcome, AppendReceipt):
            self.appended += outcome.count
        else:
            self.refused += len(request.observations)
        request.resolve(outcome)
