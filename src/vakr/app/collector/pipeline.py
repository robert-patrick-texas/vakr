"""From finished polls to the durable spool, losing nothing (6.1, STORE-004).

The scheduler puts each finished or missed occurrence into its bounded
``Outbox``. ``SpoolPump`` takes records out, flattens each into its
``DevicePoll`` plus the observations its steps produced, and appends them
to the spool, which returns only once they are fsynced (E.8). While one
append waits for its fsync, later records collect in the outbox and go in
the next append, so throughput follows the group commit rather than one
fsync per poll. A full outbox makes the scheduler's workers wait, which the
scheduler records as capacity trouble, never as targets down (6.4).

If the spool refuses an append (full disk, write error), the spool itself
records a telemetry-loss incident with the exact count (STORE-007); the
pump logs it and carries on, so later polls are not stuck behind it.

Dependencies:
    vakr.scheduler.outbox: The bounded queue.
    vakr.ingest: The spool and its errors.
    vakr.domain.observations: The observation type check.

Safety:
    Append sizes are bounded by ``SPOOL_BATCH`` and ``spool.queue_capacity``.

Example:
    >>> SpoolPump.__name__
    'SpoolPump'
"""

import asyncio
import logging
from collections.abc import Sequence

from vakr.app.collector.timings import SPOOL_BATCH
from vakr.domain.errors import ValidationError
from vakr.domain.observations import Observation, observation_type
from vakr.ingest import Spool, SpoolError
from vakr.scheduler.outbox import Outbox
from vakr.scheduler.records import PollResult

_LOG = logging.getLogger(__name__)


def flatten(result: PollResult) -> list[Observation]:
    """Return a record's poll and observations, dropping anything that is not an observation.

    Args:
        result: One scheduled occurrence.

    Returns:
        list[Observation]: The ``DevicePoll`` first, then step observations.

    Raises:
        None
    """
    found: list[Observation] = [result.poll]
    for item in result.observations:
        try:
            observation_type(item)  # ty: ignore[invalid-argument-type]
        except ValidationError:
            # A step bug must not block the spool; the poll record still goes in.
            _LOG.error(
                "dropped a %s that is not an observation",
                type(item).__name__,
                extra={"event_id": "collector.bad_observation"},
            )
            continue
        found.append(item)  # ty: ignore[invalid-argument-type]
    return found


class SpoolPump:
    """Drains the outbox into the spool.

    Attributes:
        appended: Observations made durable.
        refused: Observations the spool refused (recorded as loss incidents).
    """

    def __init__(self, outbox: Outbox, spool: Spool) -> None:
        """Bind the queue and the spool.

        Args:
            outbox: The scheduler's output queue.
            spool: The open spool.

        Returns:
            None

        Raises:
            None
        """
        self._outbox, self._spool = outbox, spool
        self._limit = max(1, min(SPOOL_BATCH, spool.settings.queue_capacity))
        self.appended = self.refused = 0
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Append records as they arrive until ``stop``; an append in progress always finishes.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            while not self._stop.is_set():
                getter = asyncio.ensure_future(self._outbox.get())
                await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()  # a cancelled get leaves its item in the queue
                    break
                await self._append(self._gather(getter.result()))
        finally:
            stopper.cancel()

    def stop(self) -> None:
        """Ask ``run`` to return once its current append is durable.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._stop.set()

    async def drain(self) -> int:
        """Append everything still queued, after the scheduler finished (16.4 step 6).

        Args:
            None

        Returns:
            int: Observations appended.

        Raises:
            None
        """
        before = self.appended
        while (first := self._outbox.get_nowait()) is not None:
            await self._append(self._gather(first))
        return self.appended - before

    def _gather(self, first: PollResult) -> list[Observation]:
        """Collect the first record and whatever else is already queued.

        Args:
            first: A record taken from the outbox.

        Returns:
            list[Observation]: Up to about ``SPOOL_BATCH`` observations.

        Raises:
            None
        """
        batch = flatten(first)
        while len(batch) < self._limit and (more := self._outbox.get_nowait()) is not None:
            batch.extend(flatten(more))
        return batch

    async def _append(self, batch: Sequence[Observation]) -> None:
        """Append a batch in spool-sized chunks.

        Args:
            batch: Observations in order.

        Returns:
            None

        Raises:
            None
        """
        for start in range(0, len(batch), self._limit):
            chunk = batch[start : start + self._limit]
            try:
                await self._spool.append(chunk)
            except SpoolError as exc:
                self.refused += len(chunk)
                _LOG.error(
                    "the spool refused %d observations (%s); the loss is recorded as a "
                    "telemetry-loss incident",
                    len(chunk),
                    type(exc).__name__,
                    extra={"event_id": "collector.spool_refused"},
                )
            else:
                self.appended += len(chunk)
