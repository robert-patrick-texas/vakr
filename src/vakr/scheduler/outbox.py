"""The bounded hand-off from the scheduler to spool and ingestion (6.4, STORE-004, QUEUE-T01).

Finished poll records leave the scheduler through a ``PollSink``. The
default ``Outbox`` is an ``asyncio.Queue`` with a fixed capacity
(``scheduler.queue_capacity``). When it is full, ``put`` waits: the worker
holding the result stays busy, so admission slows and further occurrences
are recorded as not attempted instead of being polled into a queue that
cannot take them. Records are never dropped (invariant 8).

That wait is backpressure, and it is reported, not hidden: the outbox counts
how often a producer found it full, how long producers waited in total, and
its high-water mark, and the scheduler's health snapshot reports the
collector as overloaded while it happens (OBS-003).

Dependencies:
    asyncio: The queue.
    vakr.ports.clock: Monotonic time for wait accounting.

Safety:
    Memory is bounded by ``capacity`` plus one record per busy worker.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.ports.clock import ManualClock
    >>> box = Outbox(4, ManualClock(datetime(2026, 10, 20, tzinfo=UTC)))
    >>> box.capacity, box.depth, box.full_waits
    (4, 0, 0)
"""

import asyncio
from typing import Protocol

from vakr.ports.clock import Clock
from vakr.scheduler.records import PollResult


class PollSink(Protocol):
    """Where the scheduler hands finished poll records."""

    async def put(self, result: PollResult) -> None:
        """Accept one record, waiting while the sink is full.

        Args:
            result: The record.

        Returns:
            None

        Raises:
            None
        """
        ...


class Outbox:
    """A bounded queue of poll records with backpressure accounting.

    Attributes:
        capacity: Maximum records held.
        full_waits: Times a producer found the queue full.
        high_water: Largest depth seen.
    """

    def __init__(self, capacity: int, clock: Clock) -> None:
        """Create the queue.

        Args:
            capacity: Maximum records held, at least 1.
            clock: Monotonic time for wait accounting.

        Returns:
            None

        Raises:
            ValueError: If ``capacity`` is below 1.
        """
        if capacity < 1:
            raise ValueError("the outbox needs a capacity of at least 1")
        self.capacity = capacity
        self._queue: asyncio.Queue[PollResult] = asyncio.Queue(capacity)
        self._clock = clock
        self._waiting = 0
        self._blocked_s = 0.0
        self.full_waits = 0
        self.high_water = 0

    @property
    def depth(self) -> int:
        """Return how many records wait for the consumer.

        Args:
            None

        Returns:
            int: Current depth.

        Raises:
            None
        """
        return self._queue.qsize()

    @property
    def producers_waiting(self) -> int:
        """Return how many producers are blocked on a full queue right now.

        Args:
            None

        Returns:
            int: Blocked producers.

        Raises:
            None
        """
        return self._waiting

    @property
    def blocked_s(self) -> float:
        """Return the total time producers have spent waiting.

        Args:
            None

        Returns:
            float: Seconds, summed over producers.

        Raises:
            None
        """
        return self._blocked_s

    async def put(self, result: PollResult) -> None:
        """Enqueue a record, waiting while the queue is full.

        Args:
            result: The record.

        Returns:
            None

        Raises:
            None
        """
        if self._queue.full():
            self.full_waits += 1
            self._waiting += 1
            began = self._clock.monotonic()
            try:
                await self._queue.put(result)
            finally:
                self._waiting -= 1
                self._blocked_s += self._clock.monotonic() - began
        else:
            self._queue.put_nowait(result)
        self.high_water = max(self.high_water, self._queue.qsize())

    async def get(self) -> PollResult:
        """Take the oldest record, waiting while the queue is empty.

        Args:
            None

        Returns:
            PollResult: The record.

        Raises:
            None
        """
        return await self._queue.get()

    def get_nowait(self) -> PollResult | None:
        """Take the oldest record without waiting.

        Args:
            None

        Returns:
            PollResult | None: The record, or ``None`` when empty.

        Raises:
            None
        """
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
