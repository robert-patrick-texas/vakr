"""Running admin operations: the collector's side (DISC-010, API-011, Appendix E.32).

``CollectorOperations`` is what one collector does with the operation
records: at start, operations this collector left ``running`` become
``failed`` (it restarted during them); each pass reads the oldest queued
operations, giving up those no collector took within ``PICKUP_LIMIT``;
``claim`` is the compare-and-set from ``queued`` to ``running`` that only
one collector can win; ``finish`` records the result or the reason.
``result_of`` turns a discovery and its write into the counts the
requester sees (DISC-T03).

Dependencies:
    vakr.discovery: The discovery result.
    vakr.domain: Operations and errors.
    vakr.operations.limits: The E.32 bounds.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Blocking; the collector calls every method in a worker thread. Errors
    are operator words from the discovery runner, never a secret.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.domain.ids import CollectorId, new_id
    >>> from vakr.ports.clock import SystemClock
    >>> CollectorOperations(memory_uow_factory(), SystemClock(), new_id(CollectorId)).recover()
    0
"""

import logging
from collections.abc import Iterable

from vakr.discovery.model import DiscoveryResult
from vakr.discovery.reconcile.persist import inventory_complete
from vakr.domain.errors import ConflictError, NotFoundError, ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.operations import Operation, OperationResult
from vakr.domain.quality import Quality
from vakr.operations import limits
from vakr.operations.service import OperationService
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

_LOG = logging.getLogger(__name__)
_LOST = (ConflictError, NotFoundError, ValidationError)


def result_of(
    found: DiscoveryResult, changes: Iterable[tuple[str, int]], capability_changes: int
) -> OperationResult:
    """Summarise a recorded discovery for the requester (DISC-T03).

    Args:
        found: What discovery found.
        changes: Counts per identity change from the write, such as ``("new", 2)``.
        capability_changes: Interfaces whose counter capabilities changed.

    Returns:
        OperationResult: The counts.

    Raises:
        None
    """
    count = dict(changes)
    partial = not inventory_complete(found) or bool(found.quality & Quality.PARTIAL_RESPONSE)
    return OperationResult(
        interfaces=len(found.interfaces),
        new=count.get("new", 0),
        renumbered=count.get("confident_renumber", 0) + count.get("probable", 0),
        missing=count.get("missing", 0),
        reviews_opened=count.get("review_opened", 0),
        capability_changes=capability_changes,
        partial=partial,
        duration_ms=max(round(found.elapsed_s * 1000), 0),
    )


class CollectorOperations:
    """One collector's claims and results."""

    def __init__(
        self, uow_factory: MetadataUnitOfWorkFactory, clock: Clock, collector_id: CollectorId
    ) -> None:
        """Bind to the metadata store and this collector.

        Args:
            uow_factory: Opens units of work.
            clock: Time source.
            collector_id: This collector.

        Returns:
            None

        Raises:
            None
        """
        self._factory, self._clock, self.collector_id = uow_factory, clock, collector_id
        self._requests = OperationService(uow_factory, clock)

    def recover(self) -> int:
        """Fail the operations this collector was running when it last stopped.

        Args:
            None

        Returns:
            int: Operations marked ``failed``.

        Raises:
            StorageError: If the metadata store fails.
        """
        failed = 0
        for _ in range(limits.MAX_ACTIVE // limits.RECOVER_READ + 1):
            with self._factory() as uow:
                left = uow.operations.running_on(self.collector_id, limit=limits.RECOVER_READ)
                now = self._clock.now_utc()
                for op in left:
                    uow.operations.update(op.failed(now, limits.RESTART_REASON))
                uow.commit()
            failed += len(left)
            if len(left) < limits.RECOVER_READ:
                break
        return failed

    def pending(self) -> tuple[Operation, ...]:
        """Return the oldest queued operations, after giving up stale ones.

        Args:
            None

        Returns:
            tuple[Operation, ...]: At most ``PICKUP_READ``, oldest first.

        Raises:
            StorageError: If the metadata store fails.
        """
        self._requests.expire_stale()
        with self._factory() as uow:
            return uow.operations.queued(limit=limits.PICKUP_READ)

    def claim(self, op: Operation) -> Operation | None:
        """Take a queued operation for this collector, if no one else did first.

        Args:
            op: The queued operation as read.

        Returns:
            Operation | None: The running operation, or ``None`` when another
            collector claimed it, it expired, or it is gone.

        Raises:
            StorageError: If the metadata store fails.
        """
        claimed = op.claimed(self.collector_id, self._clock.now_utc())
        try:
            with self._factory() as uow:
                uow.operations.update(claimed)
                uow.commit()
        except _LOST:
            return None
        return claimed

    def finish(self, op: Operation, result: OperationResult | None, error: str | None) -> None:
        """Record how a claimed operation ended.

        Args:
            op: The running operation.
            result: What a successful rediscovery found, or ``None``.
            error: Why it failed, when ``result`` is ``None``.

        Returns:
            None

        Raises:
            StorageError: If the metadata store fails.
        """
        now = self._clock.now_utc()
        done = op.succeeded(now, result) if result is not None else op.failed(now, error or "")
        try:
            with self._factory() as uow:
                uow.operations.update(done)
                uow.commit()
        except _LOST:
            _LOG.warning("operation %s changed while it ran; its outcome was not recorded", op.id,
                         extra={"event_id": "operations.lost"})  # fmt: skip

    def purge(self) -> int:
        """Delete finished operations past ``KEEP_FINISHED`` or beyond ``MAX_FINISHED``.

        Args:
            None

        Returns:
            int: Operations deleted, at most ``PURGE_BATCH * PURGE_BATCHES``.

        Raises:
            StorageError: If the metadata store fails.
        """
        deleted = 0
        for _ in range(limits.PURGE_BATCHES):
            before = self._clock.now_utc() - limits.KEEP_FINISHED
            with self._factory() as uow:
                n = uow.operations.purge_finished(before=before, keep=limits.MAX_FINISHED,
                                                  limit=limits.PURGE_BATCH)  # fmt: skip
                uow.commit()
            deleted += n
            if n < limits.PURGE_BATCH:
                break
        return deleted
