"""Requesting and following admin operations: the API's and the CLI's side (DISC-003, API-011).

``OperationService.request_rediscovery`` is what ``POST
/api/v1/devices/{uuid}/rediscovery`` and ``vakr inventory rediscover`` both
call. It checks that a collector would discover the device at all, returns
the device's operation that is already queued or running instead of a
second one (coalescing), refuses beyond ``MAX_ACTIVE``, and otherwise stores
a ``queued`` operation with its audit record in one transaction (SEC-018).
It never waits for the discovery (DISC-010): a collector runs it.

Reads give stale queued operations up first (``expired`` after
``PICKUP_LIMIT``), so a stopped collector shows as such instead of as an
operation queued forever.

Dependencies:
    vakr.domain: Operations, devices, audit events, errors.
    vakr.operations.limits, rules: The E.32 bounds and who may be rediscovered.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Synchronous and blocking; the API calls it in a worker thread. The
    audit summary holds the device name and the operation ID only.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.ports.clock import SystemClock
    >>> OperationService(memory_uow_factory(), SystemClock()).expire_stale()
    0
"""

from vakr.domain.control import AuditEvent
from vakr.domain.enums import AuditOutcome, ReachabilityStrategy
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError
from vakr.domain.ids import AuditEventId, DeviceId, OperationId, new_id
from vakr.domain.inventory import Device
from vakr.domain.operations import Operation, OperationKind, OperationState
from vakr.domain.paging import Page, PageRequest
from vakr.operations import limits
from vakr.operations.rules import RefusedError, Requested, clean_actor, refusal
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory, OperationFilter


class OperationService:
    """Admin operations as the API and the CLI see them."""

    def __init__(
        self,
        uow_factory: MetadataUnitOfWorkFactory,
        clock: Clock,
        *,
        default_strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED,
    ) -> None:
        """Bind the service to the metadata store.

        Args:
            uow_factory: Opens units of work.
            clock: Time source.
            default_strategy: ``polling.reachability_strategy``.

        Returns:
            None

        Raises:
            None
        """
        self._factory, self._clock, self._default = uow_factory, clock, default_strategy

    def request_rediscovery(
        self, device_id: DeviceId, *, actor: str, request_id: str | None = None
    ) -> Requested:
        """Queue a manual rediscovery, or return the one already active (DISC-003, DISC-010).

        Args:
            device_id: The device.
            actor: Who asks (SEC-018).
            request_id: API request ID for the audit record, if any.

        Returns:
            Requested: The operation and whether it is new.

        Raises:
            NotFoundError: If the device does not exist.
            RefusedError: If no collector would discover it, or ``MAX_ACTIVE`` are active.
            StorageError: If the metadata store fails.
        """
        try:
            return self._request(device_id, clean_actor(actor), request_id)
        except DuplicateError, ConflictError:  # another writer won the race: follow it
            with self._factory() as uow:
                active = uow.operations.active_for_device(device_id, OperationKind.REDISCOVERY)
            if active is None:
                raise
            return Requested(active, created=False)

    def _request(self, device_id: DeviceId, actor: str, request_id: str | None) -> Requested:
        """Run one request in one unit of work.

        Args:
            device_id: The device.
            actor: The cleaned requester name.
            request_id: API request ID, if any.

        Returns:
            Requested: The operation and whether it is new.

        Raises:
            NotFoundError, RefusedError, StorageError: See ``request_rediscovery``.
            DuplicateError, ConflictError: If another request or a collector wrote first.
        """
        now = self._clock.now_utc()
        with self._factory() as uow:
            device = uow.devices.get(device_id)
            if device is None:
                raise NotFoundError("device", device_id)
            if (why := refusal(device, self._default)) is not None:
                raise RefusedError(why)
            kind = OperationKind.REDISCOVERY
            active = self._live(uow, uow.operations.active_for_device(device_id, kind))
            if active is not None:
                self._audit(uow, device, actor, request_id, f"joined operation {active.id}")
                uow.commit()
                return Requested(active, created=False)
            if uow.operations.count_active() >= limits.MAX_ACTIVE:
                raise RefusedError(f"{limits.MAX_ACTIVE} operations are already queued or "
                                   "running; wait for some to finish (Appendix E.32)")  # fmt: skip
            op = Operation(new_id(OperationId), OperationKind.REDISCOVERY, device_id,
                           OperationState.QUEUED, actor, now)  # fmt: skip
            uow.operations.add(op)
            self._audit(uow, device, actor, request_id, f"queued operation {op.id}")
            old = now - limits.KEEP_FINISHED
            uow.operations.purge_finished(before=old, keep=limits.MAX_FINISHED,
                                          limit=limits.PURGE_BATCH)  # fmt: skip
            uow.commit()
        return Requested(op, created=True)

    def _live(self, uow: MetadataUnitOfWork, op: Operation | None) -> Operation | None:
        """Return an active operation, or expire it first if no collector took it in time.

        Args:
            uow: The open unit of work; the caller commits.
            op: The device's active operation, if any.

        Returns:
            Operation | None: The operation, or ``None`` when it just expired.

        Raises:
            StorageError: If the write fails.
        """
        now = self._clock.now_utc()
        if op is None or op.state is not OperationState.QUEUED:
            return op
        if now - op.requested_at < limits.PICKUP_LIMIT:
            return op
        uow.operations.update(op.expired(now, limits.EXPIRED_REASON))
        return None

    def _audit(
        self, uow: MetadataUnitOfWork, device: Device, actor: str, rid: str | None, what: str
    ) -> None:
        """Append the request's audit record (SEC-018).

        Args:
            uow: The open unit of work.
            device: The device.
            actor: The requester.
            rid: API request ID, if any.
            what: What became of the request.

        Returns:
            None

        Raises:
            None
        """
        summary = f"manual rediscovery of {device.name}: {what}"
        uow.audit.append(AuditEvent(new_id(AuditEventId), self._clock.now_utc(), actor,
                                    "device.rediscover", "device", device.id,
                                    AuditOutcome.SUCCESS, summary, rid))  # fmt: skip

    def get(self, operation_id: OperationId) -> Operation | None:
        """Return one operation, after giving up stale queued ones.

        Args:
            operation_id: Its UUID.

        Returns:
            Operation | None: The operation, or ``None``.

        Raises:
            StorageError: If the metadata store fails.
        """
        self.expire_stale()
        with self._factory() as uow:
            return uow.operations.get(operation_id)

    def page(self, page: PageRequest, where: OperationFilter | None = None) -> Page[Operation]:
        """List operations newest first, after giving up stale queued ones.

        Args:
            page: Limit and cursor.
            where: Device and state filter.

        Returns:
            Page[Operation]: The page.

        Raises:
            ValidationError: If the cursor is invalid.
            StorageError: If the metadata store fails.
        """
        self.expire_stale()
        with self._factory() as uow:
            return uow.operations.list_page(page, where)

    def expire_stale(self) -> int:
        """Give up queued operations no collector claimed within ``PICKUP_LIMIT``.

        Args:
            None

        Returns:
            int: Operations that expired now.

        Raises:
            StorageError: If the metadata store fails.
        """
        cutoff = self._clock.now_utc() - limits.PICKUP_LIMIT
        with self._factory() as uow:
            stale = [o for o in uow.operations.queued(limit=limits.MAX_ACTIVE)
                     if o.requested_at <= cutoff]  # fmt: skip
            if not stale:
                return 0
            expired = 0
            for op in stale:
                try:
                    self._live(uow, op)
                    expired += 1
                except ConflictError:  # a collector claimed it a moment ago
                    continue
            try:
                uow.commit()
            except ConflictError:  # claimed meanwhile; the next read tries again
                return 0
        return expired
