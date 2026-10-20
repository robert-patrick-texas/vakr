"""An append on its way to disk, and the receipt that proves it arrived (E.8).

``Spool.append`` returns an ``AppendReceipt`` only after the fsync that
covers every record of the append has returned, so a collector holding a
receipt knows its observations survive a power cut. The receipt's sequence
numbers let tests and diagnostics follow those records through ingestion.

Dependencies:
    asyncio: The future the writer thread resolves on the event loop.
    threading: The condition variable of the request queue.
    vakr.domain: Observations and batch IDs.

Safety:
    Requests hold observations in memory until written; the admission gate
    bounds how many exist at once.

Example:
    >>> from uuid import UUID
    >>> AppendReceipt(UUID(int=1), 5, 9, 5, 0.25).count
    5
"""

import asyncio
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from vakr.domain.ids import BatchId
from vakr.domain.observations import Observation
from vakr.ingest.errors import SpoolClosedError


@dataclass(frozen=True, slots=True)
class AppendReceipt:
    """Proof that an append is durable. Immutable.

    Attributes:
        batch_id: The batch UUID its records carry.
        first_seq: First record's sequence number (0 for an empty append).
        last_seq: Last record's sequence number (0 for an empty append).
        count: Records appended.
        latency_s: Seconds from acceptance to the covering fsync's return.
    """

    batch_id: BatchId
    first_seq: int
    last_seq: int
    count: int
    latency_s: float


@dataclass(slots=True)
class AppendRequest:
    """One append on its way to disk. Mutable; owned by the writer thread once submitted.

    Attributes:
        observations: What to append.
        batch_id: Their batch UUID.
        future: Resolved on the event loop with a receipt or an error.
        accepted: Monotonic time the spool accepted the append.
        first_seq: First sequence number assigned, once written.
        last_seq: Last sequence number assigned, once written.
    """

    observations: Sequence[Observation]
    batch_id: BatchId
    future: asyncio.Future[AppendReceipt]
    accepted: float
    first_seq: int = 0
    last_seq: int = 0

    def resolve(self, outcome: AppendReceipt | BaseException) -> None:
        """Complete the future, unless the caller already stopped waiting.

        Args:
            outcome: The receipt, or the error that refused the append.

        Returns:
            None

        Raises:
            None
        """
        if self.future.done():
            return  # cancelled: the outcome is still counted by the spool
        if isinstance(outcome, BaseException):
            self.future.set_exception(outcome)
        else:
            self.future.set_result(outcome)

    def receipt(self, now: float) -> AppendReceipt:
        """Build the receipt once the request is durable.

        Args:
            now: Monotonic time of the covering fsync's return.

        Returns:
            AppendReceipt: The receipt.

        Raises:
            None
        """
        return AppendReceipt(self.batch_id, self.first_seq, self.last_seq,
                             len(self.observations), now - self.accepted)  # fmt: skip


class RequestQueue:
    """Appends waiting for the writer thread. Thread-safe; its size is bounded by the gate."""

    def __init__(self) -> None:
        """Create an empty, open queue.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._cond = threading.Condition()
        self._items: deque[AppendRequest] = deque()
        self._stopping = False

    def put(self, request: AppendRequest) -> None:
        """Queue a request and wake the writer.

        Args:
            request: The append.

        Returns:
            None

        Raises:
            SpoolClosedError: If the queue is stopping.
        """
        with self._cond:
            if self._stopping:
                raise SpoolClosedError()
            self._items.append(request)
            self._cond.notify()

    def stop(self) -> None:
        """Refuse new requests and wake the writer so it can finish.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self._cond:
            self._stopping = True
            self._cond.notify()

    def take(self, wait_s: Callable[[], float | None]) -> list[AppendRequest] | None:
        """Wait for requests, or until the writer's fsync deadline.

        Args:
            wait_s: Returns seconds until the fsync deadline, or ``None``
                when nothing waits for an fsync (wait for a request).

        Returns:
            list[AppendRequest] | None: Queued requests (empty when only the
            deadline passed), or ``None`` once stopping with nothing queued.

        Raises:
            None
        """
        with self._cond:
            while not self._items and not self._stopping:
                remaining = wait_s()
                if remaining is not None and remaining <= 0:
                    return []
                self._cond.wait(remaining)
            if not self._items:
                return None
            batch = list(self._items)
            self._items.clear()
            return batch

    def drain(self) -> list[AppendRequest]:
        """Stop the queue and return whatever is still in it.

        Args:
            None

        Returns:
            list[AppendRequest]: Requests that will not be written.

        Raises:
            None
        """
        with self._cond:
            self._stopping = True
            batch = list(self._items)
            self._items.clear()
            return batch
