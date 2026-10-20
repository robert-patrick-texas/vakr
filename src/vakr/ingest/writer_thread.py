"""The spool's writer thread: sealing, writing, and group commit off the event loop (E.8).

The asyncio collector must never wait on disk. Appends become requests in a
queue (bounded by the admission gate); this thread seals them into
envelopes, writes their frames, and fsyncs on the ``GroupCommit`` schedule.
Only after the fsync that covers a request returns is its future resolved on
the event loop: an observation counts as durably handed off only then.

A refused request (spool full, write or fsync failure) is resolved with the
error and recorded as a telemetry-loss incident with its exact count,
classes, and time range (STORE-007). An object that is not an observation
is refused without an incident: that is a caller bug, not lost telemetry.

Dependencies:
    threading: The thread itself.
    vakr.domain.envelope: Sealing observations into envelopes.
    vakr.ingest: The segment writer, the commit schedule, and incidents.

Safety:
    The thread touches asyncio only through ``loop.call_soon_threadsafe``.

Example:
    >>> WriterThread.__mro__[1].__name__
    'Thread'
"""

import asyncio
import contextlib
import threading
from collections.abc import Callable, Sequence
from typing import Any

from vakr.domain.envelope import envelope_to_bytes, seal
from vakr.domain.errors import ValidationError
from vakr.domain.timestamps import to_epoch_us
from vakr.ingest.commit import GroupCommit
from vakr.ingest.errors import SpoolClosedError, SpoolError
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.losses import refused_incident
from vakr.ingest.requests import AppendReceipt, AppendRequest, RequestQueue
from vakr.ingest.segment_writer import SegmentWriter
from vakr.ports.clock import Clock

type Settle = Callable[[AppendRequest, AppendReceipt | BaseException], None]


class WriterThread(threading.Thread):
    """Drains append requests to the active segment with group commit.

    Attributes:
        writer: The segment writer, used only by this thread.
        commit: The fsync schedule and its statistics.
        closed_cleanly: Set once everything was written and the final fsync succeeded.
    """

    def __init__(
        self, writer: SegmentWriter, commit: GroupCommit, clock: Clock, log: IncidentLog,
        loop: asyncio.AbstractEventLoop, settle: Settle, on_durable: Callable[[int], None],
    ) -> None:  # fmt: skip
        """Prepare the thread; ``start()`` runs it.

        Args:
            writer: The segment writer.
            commit: The group-commit schedule.
            clock: Time source for append stamps and deadlines.
            log: Incident log for refused requests.
            loop: The event loop that owns the futures.
            settle: Called on ``loop`` with each request's outcome.
            on_durable: Called on ``loop`` with each new ``durable_seq``.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(name="vakr-spool-writer", daemon=True)
        self.writer, self.commit, self._clock, self._log = writer, commit, clock, log
        self._loop, self._settle, self._on_durable = loop, settle, on_durable
        self._requests = RequestQueue()
        self._pending: list[AppendRequest] = []
        self.closed_cleanly = False
        self.prepared = threading.Event()  # set once the first segment has its header

    def submit(self, request: AppendRequest) -> None:
        """Queue a request; called on the event loop.

        Args:
            request: The append.

        Returns:
            None

        Raises:
            SpoolClosedError: If the thread is stopping or stopped.
        """
        if not self.is_alive():
            raise SpoolClosedError()
        self._requests.put(request)

    def stop(self) -> None:
        """Ask the thread to write what is queued, fsync, and exit.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._requests.stop()

    def run(self) -> None:
        """Write and fsync until stopped, then flush and close the segment.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        try:
            try:
                self.writer.prepare()
            finally:
                self.prepared.set()
            while (batch := self._requests.take(self._wait_s)) is not None:
                for request in batch:
                    self._write(request)
                if self.commit.due(self.writer.unsynced, self._clock.monotonic()):
                    self._sync()
            self._sync()
            self.writer.close()
            self.closed_cleanly = not self._pending
        except SpoolError as exc:
            self._fail_all(exc)
        finally:
            self._fail_all(SpoolClosedError())

    def _wait_s(self) -> float | None:
        """Seconds until the next timed fsync, for the queue's wait.

        Args:
            None

        Returns:
            float | None: Seconds (may be negative), or ``None`` when nothing
            waits for an fsync.

        Raises:
            None
        """
        if not self.writer.unsynced:
            return None
        now = self._clock.monotonic()
        return self.commit.deadline(now) - now

    def _write(self, request: AppendRequest) -> None:
        """Seal and write one request; fsync early at the record threshold.

        Args:
            request: The append.

        Returns:
            None

        Raises:
            None
        """
        try:
            payloads = [envelope_to_bytes(seal(o, request.batch_id)) for o in request.observations]
        except (ValidationError, TypeError) as exc:
            self._call(self._settle, request, exc)
            return
        stamp, durable_before = to_epoch_us(self._clock.now_utc()), self.writer.durable_seq
        try:
            request.first_seq, request.last_seq = self.writer.append(
                payloads, request.batch_id, stamp
            )
        except (SpoolError, ValueError) as exc:
            self._refuse([request], exc)
            return
        if self.writer.durable_seq != durable_before:
            # A rotation fsynced the previous segment: settle what it covered;
            # only this request now waits for an fsync.
            self._settle_durable()
            self.commit.reset()
        self._pending.append(request)
        self.commit.written(request.accepted)
        if self.writer.unsynced >= self.commit.records:
            self._sync()

    def _sync(self) -> None:
        """Fsync, then resolve every request the fsync covers.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if not self.writer.unsynced:
            self.commit.reset()
            return
        started = self._clock.monotonic()
        try:
            self.writer.sync()
        except SpoolError as exc:
            self.writer.abandon()
            self.commit.reset()
            refused, self._pending = self._pending, []
            self._refuse(refused, exc)
            return
        self.commit.synced(self._clock.monotonic() - started)
        self._settle_durable()

    def _settle_durable(self) -> None:
        """Resolve requests at or below ``durable_seq`` and announce it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        durable, now = self.writer.durable_seq, self._clock.monotonic()
        done = [r for r in self._pending if r.last_seq <= durable]
        self._pending = [r for r in self._pending if r.last_seq > durable]
        for request in done:
            self._call(self._settle, request, request.receipt(now))
        self._call(self._on_durable, durable)

    def _refuse(self, requests: Sequence[AppendRequest], exc: BaseException) -> None:
        """Resolve refused requests with ``exc`` and record their loss.

        Args:
            requests: Requests that will not become durable.
            exc: Why.

        Returns:
            None

        Raises:
            None
        """
        observations = [o for r in requests for o in r.observations]
        if observations:
            self._log.record(refused_incident(observations, exc, self._clock))
        for request in requests:
            self._call(self._settle, request, exc)

    def _fail_all(self, exc: BaseException) -> None:
        """Refuse everything still queued or unsynced.

        Args:
            exc: The reason.

        Returns:
            None

        Raises:
            None
        """
        leftover, self._pending = [*self._pending, *self._requests.drain()], []
        if leftover:
            self._refuse(leftover, exc)

    def _call(self, func: Callable[..., None], *args: Any) -> None:
        """Schedule ``func(*args)`` on the event loop, ignoring a closed loop.

        Args:
            func: Callback.
            *args: Its arguments.

        Returns:
            None

        Raises:
            None
        """
        # A closed loop means nobody is waiting for the outcome any more.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(func, *args)
