"""Bounded admission to the spool's memory queue: producers wait, memory stays bounded (QUEUE-T01).

Collectors hand observations to the spool from the asyncio event loop. Until
the writer thread fsyncs them they live in memory, so their number is capped
at ``spool.queue_capacity``. A producer whose append would exceed the cap
*waits* (controlled backpressure, 6.4) in arrival order; nothing is dropped
and the event loop is never blocked. How many producers wait, and how often,
is reported as queue saturation, which is a collector capacity condition,
never a target state (OBS-003).

Dependencies:
    asyncio, collections.deque: Futures for waiting producers, in FIFO order.

Safety:
    Use from one event loop only. ``release`` is called on that loop (the
    writer thread schedules it with ``call_soon_threadsafe``).

Example:
    >>> import asyncio
    >>> async def demo():
    ...     gate = AdmissionGate(10)
    ...     await gate.acquire(4)
    ...     gate.release(4)
    ...     return gate.depth
    >>> asyncio.run(demo())
    0
"""

import asyncio
from collections import deque

from vakr.domain.errors import ValidationError


class AdmissionGate:
    """Counts observations in memory and makes producers wait at the cap.

    Mutable; confined to one event loop.

    Attributes:
        capacity: Most observations admitted at once.
        waits: Appends that had to wait since the gate was created.
    """

    def __init__(self, capacity: int) -> None:
        """Create an empty gate.

        Args:
            capacity: Most observations admitted at once; at least 1.

        Returns:
            None

        Raises:
            ValidationError: If ``capacity`` is not positive.
        """
        if capacity < 1:
            raise ValidationError("queue capacity must be positive", field="queue_capacity")
        self.capacity = capacity
        self.waits = 0
        self._depth = 0
        self._waiters: deque[tuple[int, asyncio.Future[None]]] = deque()

    @property
    def depth(self) -> int:
        """Observations currently admitted and not yet released.

        Args:
            None

        Returns:
            int: 0 to ``capacity``.

        Raises:
            None
        """
        return self._depth

    @property
    def waiting(self) -> int:
        """Producers currently waiting for room.

        Args:
            None

        Returns:
            int: Waiting producers.

        Raises:
            None
        """
        return len(self._waiters)

    async def acquire(self, count: int) -> None:
        """Wait until ``count`` observations fit, then admit them.

        Producers are admitted in arrival order, so a large append is not
        starved by a stream of small ones.

        Args:
            count: Observations to admit, 1 to ``capacity``.

        Returns:
            None

        Raises:
            ValidationError: If ``count`` is out of range.
            asyncio.CancelledError: If the caller is cancelled while waiting;
                nothing is admitted.
        """
        if not 0 < count <= self.capacity:
            raise ValidationError(
                f"an append must hold 1 to {self.capacity} observations (spool.queue_capacity)",
                field="observations",
            )
        if not self._waiters and self._depth + count <= self.capacity:
            self._depth += count
            return
        self.waits += 1
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (count, waiter)
        self._waiters.append(entry)
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                self.release(count)  # admitted just as it was cancelled: give the room back
            else:
                if entry in self._waiters:
                    self._waiters.remove(entry)
                self._wake()
            raise

    def release(self, count: int) -> None:
        """Return room for ``count`` observations and admit waiting producers.

        Args:
            count: Observations leaving memory (made durable or refused).

        Returns:
            None

        Raises:
            None
        """
        self._depth = max(0, self._depth - count)
        self._wake()

    def _wake(self) -> None:
        """Admit waiting producers in order while the head of the line fits.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        while self._waiters and self._depth + self._waiters[0][0] <= self.capacity:
            count, waiter = self._waiters.popleft()
            if waiter.done():
                continue
            self._depth += count
            waiter.set_result(None)
