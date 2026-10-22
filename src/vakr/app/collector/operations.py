"""The collector's operation watcher: pick up manual rediscoveries and record results (DISC-010).

``OperationWatcher.watch`` runs beside the inventory watch, at the same
``change_poll_s`` (5 s) cadence. At start it fails the operations this
collector left ``running`` (it restarted during them). Each pass, while
discovery has a free slot, it reads the oldest queued operations (one
bounded indexed query, which also gives up those no collector took within
an hour), claims each with a compare-and-set so no other collector can
run it too, and hands the device to ``ManualDiscoveryRunner.run_now``. The
runner's hook records the result or the reason. Finished operations are
purged hourly in bounded batches (Appendix E.32).

The API and the CLI never wait for any of this: they store the request
and answer with its ID (DISC-010).

Dependencies:
    asyncio: Worker threads and the stop event.
    vakr.operations: Claims, results, and purges.
    vakr.app.collector.discovery_manual, discovery_attempt: Manual runs and outcomes.

Safety:
    Every metadata call runs in a worker thread; a failing metadata store
    only delays pickups (logged once) and never stops collection.

Example:
    >>> OperationWatcher.__name__
    'OperationWatcher'
"""

import asyncio
import contextlib
import logging
from functools import partial

from vakr.app.collector.discovery_attempt import Attempt
from vakr.app.collector.discovery_manual import ManualDiscoveryRunner
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.operations import Operation
from vakr.operations import CollectorOperations, result_of
from vakr.operations.limits import COLLECTOR_PURGE_INTERVAL_S, NOT_HERE_REASON
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

_LOG = logging.getLogger(__name__)


class OperationWatcher:
    """Moves queued manual rediscoveries into discovery and records how they end.

    Attributes:
        started: Manual rediscoveries handed to discovery since start.
    """

    def __init__(
        self, runner: ManualDiscoveryRunner, uow_factory: MetadataUnitOfWorkFactory,
        collector_id: CollectorId, clock: Clock,
    ) -> None:  # fmt: skip
        """Bind the watcher to discovery and this collector's operations.

        Args:
            runner: The discovery runner.
            uow_factory: Opens metadata units of work.
            collector_id: This collector, which claims and runs.
            clock: UTC time for records; monotonic time for the purge cadence.

        Returns:
            None

        Raises:
            None
        """
        self._runner, self._clock = runner, clock
        self._ops = CollectorOperations(uow_factory, clock, collector_id)
        self._claimed: set[DeviceId] = set()
        self.started = 0

    async def watch(self, stop: asyncio.Event, interval_s: float) -> None:
        """Recover, then pick up queued operations every ``interval_s`` until ``stop``.

        Args:
            stop: Set at shutdown.
            interval_s: Seconds between passes (``change_poll_s``).

        Returns:
            None

        Raises:
            None
        """
        failing, purged_at = False, None
        while not stop.is_set():
            try:
                if purged_at is None:
                    await self._recover()
                    purged_at = self._clock.monotonic() - COLLECTOR_PURGE_INTERVAL_S
                await self.pass_once()
                if self._clock.monotonic() - purged_at >= COLLECTOR_PURGE_INTERVAL_S:
                    await asyncio.to_thread(self._ops.purge)
                    purged_at = self._clock.monotonic()
                failing = False
            except Exception as exc:  # noqa: BLE001 - pickups wait; collection goes on
                if not failing:
                    _LOG.warning("cannot read admin operations (%s); manual rediscoveries wait",
                                 type(exc).__name__,
                                 extra={"event_id": "operations.unavailable"})  # fmt: skip
                failing = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), interval_s)

    async def _recover(self) -> None:
        """Fail what this collector was running when it stopped (restart recovery).

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the metadata store fails; the next pass retries.
        """
        failed = await asyncio.to_thread(self._ops.recover)
        if failed:
            _LOG.warning("%d manual rediscoveries were running when the collector stopped; "
                         "marked failed", failed,
                         extra={"event_id": "operations.recovered"})  # fmt: skip

    async def pass_once(self) -> int:
        """Claim and start as many queued operations as discovery has free slots for.

        Args:
            None

        Returns:
            int: Operations started in this pass.

        Raises:
            StorageError: If the metadata store fails.
        """
        free = self._runner.free_slots()
        if free == 0:
            return 0
        started = 0
        for op in await asyncio.to_thread(self._ops.pending):
            if started >= free:
                break
            if op.device_id in self._claimed:
                continue
            here = self._runner.discovers(op.device_id)
            if not here and not self._runner.planned:
                continue  # the first plan is not loaded yet: decide on a later pass
            claimed = await asyncio.to_thread(self._ops.claim, op)
            if claimed is None:
                continue
            if here and self._runner.run_now(op.device_id, partial(self._done, claimed)):
                self._claimed.add(op.device_id)
                started += 1
                self.started += 1
                _LOG.info("manual rediscovery of device %s started (operation %s)", op.device_id,
                          op.id, extra={"event_id": "operations.started"})  # fmt: skip
            else:
                await asyncio.to_thread(self._ops.finish, claimed, None, NOT_HERE_REASON)
        return started

    async def _done(self, op: Operation, done: Attempt) -> None:
        """Record a manual rediscovery's outcome.

        Args:
            op: The running operation.
            done: How the discovery ended.

        Returns:
            None

        Raises:
            None
        """
        self._claimed.discard(op.device_id)
        result, error = None, done.why or "the rediscovery did not finish"
        if done.ok and done.found is not None and done.report is not None:
            report = done.report
            result = result_of(done.found, report.changes, report.capability_changes)
        try:
            await asyncio.to_thread(self._ops.finish, op, result, error)
        except Exception as exc:  # noqa: BLE001 - restart recovery or expiry settles it
            _LOG.warning("cannot record operation %s (%s)", op.id, type(exc).__name__,
                         extra={"event_id": "operations.unrecorded"})  # fmt: skip
            return
        _LOG.info("manual rediscovery of device %s %s (operation %s)", op.device_id,
                  "succeeded" if result else f"failed: {error}", op.id,
                  extra={"event_id": "operations.finished"})  # fmt: skip
