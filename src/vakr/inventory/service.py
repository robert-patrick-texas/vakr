"""The collector-facing inventory service: devices, poll plans, and discovery results.

A collector asks four things of inventory (it calls them through
``asyncio.to_thread``, because each one is a blocking metadata read or
write):

- ``devices()``: what to poll. Every position polls every enabled or
  maintenance device in the MVP (27.5); disabled and retired ones are never
  polled (7.5).
- ``change_counter()``: whether anything that affects polling changed, so a
  collector reloads plans only when needed (6.2). It is read from the
  metadata store, so a ``vakr inventory apply`` in another process is seen.
- ``poll_plan(device)``: the device's interfaces by current ``ifIndex``, or
  ``None`` when it was never discovered (DISC-001).
- ``record_discovery(device, result, now)``: reconcile, store, classify,
  and return the new plan (IFID-001, DISC-007, META-012).
  ``record_discovery_report`` does the same and also says what changed,
  for a manual rediscovery's result (DISC-T03).

Dependencies:
    vakr.discovery.reconcile.persist: Reconciliation and its writes.
    vakr.inventory.discovery_report: Capability memory and the discovery report.
    vakr.inventory.planning, reads: Plans and bounded lists.
    vakr.policy: Rules and keywords; vakr.ports: clock and metadata.

Safety:
    Synchronous and thread-safe: the only shared state is a capability
    cache guarded by a lock. Concurrent discoveries that collide on a new
    tag are retried a bounded number of times.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.domain.ids import CollectorId, new_id
    >>> from vakr.policy.rules import Policy
    >>> from vakr.ports.clock import SystemClock
    >>> svc = InventoryService(memory_uow_factory(), new_id(CollectorId), Policy(), SystemClock())
    >>> svc.devices(), svc.change_counter()
    ((), 0)
"""

import threading
from collections.abc import Sequence
from datetime import datetime, timedelta

from vakr.collectors.snmp.plan import PollPlan
from vakr.discovery.model import DiscoveryResult
from vakr.discovery.reconcile.persist import DiscoveryWrite, Persisted, persist_discovery
from vakr.domain.control import AuditEvent
from vakr.domain.enums import AdminState, AuditOutcome
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError
from vakr.domain.ids import AuditEventId, CollectorId, DeviceId, new_id
from vakr.domain.inventory import Device
from vakr.inventory.discovery_report import CapabilityMemory, DiscoveryReport
from vakr.inventory.planning import PlanInputs, build_plan
from vakr.inventory.reads import all_devices
from vakr.inventory.stage1_lifecycle import StatusEvidence, apply_stage1_status
from vakr.logic.lifecycle_model import STALE_AFTER
from vakr.policy.keywords import KeywordSet, packaged_keywords
from vakr.policy.rules import Policy
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory

POLLED_STATES = frozenset({AdminState.ENABLED, AdminState.MAINTENANCE})
"""Admin states a collector polls (7.5); maintenance keeps collecting with a label."""

WRITE_ATTEMPTS = 3
"""Attempts for a discovery write that collides with another (E.26)."""


class InventoryService:
    """Devices, poll plans, and discovery persistence for one collector."""

    def __init__(
        self,
        uow_factory: MetadataUnitOfWorkFactory,
        collector_id: CollectorId,
        policy: Policy,
        clock: Clock,
        *,
        keywords: KeywordSet | None = None,
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        """Bind the service to a metadata store and a policy.

        Args:
            uow_factory: Opens units of work on the metadata store.
            collector_id: The collector this service serves. Every position
                polls every device in the MVP; kept for assignment (SCHED-014).
            policy: Collection rules, from ``policies.toml``.
            clock: Time source for audit records.
            keywords: Effective description keywords (``keywords_from_config``);
                ``None`` means the packaged ones (E.15).
            stale_after: ``lifecycle.stale_after`` (LIFE-003).

        Returns:
            None

        Raises:
            None
        """
        self.collector_id = collector_id
        self._factory = uow_factory
        self._clock = clock
        self._inputs = PlanInputs(policy, packaged_keywords() if keywords is None else keywords)
        self._stale_after = stale_after
        self._lock = threading.Lock()
        self._epoch = 0
        self._caps = CapabilityMemory()

    def devices(self) -> tuple[Device, ...]:
        """Return the devices this collector polls, ordered by ``(name, id)``.

        Args:
            None

        Returns:
            tuple[Device, ...]: Enabled and maintenance devices.

        Raises:
            StorageError: If the store cannot be read.
        """
        with self._factory() as uow:
            stored = all_devices(uow, include_retired=False)
        return tuple(d for d in stored if d.admin_state in POLLED_STATES)

    def change_counter(self) -> int:
        """Return a number that grows whenever devices, interfaces, reviews, or policy change.

        Args:
            None

        Returns:
            int: The stored metadata counter plus this process's policy epoch.

        Raises:
            StorageError: If the store cannot be read.
        """
        with self._factory() as uow:
            stored = uow.change_counter()
        with self._lock:
            return stored + self._epoch

    def replace_policy(self, policy: Policy, keywords: KeywordSet | None = None) -> None:
        """Use new rules and keywords after a reload; the change counter grows.

        Args:
            policy: The new rules.
            keywords: The new keywords; ``None`` keeps the current ones.

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._inputs = PlanInputs(policy, keywords or self._inputs.keywords)
            self._epoch += 1

    def poll_plan(self, device_id: DeviceId) -> PollPlan | None:
        """Return a device's poll plan from stored inventory.

        Args:
            device_id: The device.

        Returns:
            PollPlan | None: The plan, or ``None`` when the device has never
            been discovered and needs discovery first (DISC-001).

        Raises:
            NotFoundError: If the device does not exist.
            StorageError: If the store cannot be read.
        """
        with self._lock:
            inputs = self._inputs
        caps = self._caps.of(device_id)
        with self._factory() as uow:
            device = uow.devices.get(device_id)
            if device is None:
                raise NotFoundError("device", device_id)
            return build_plan(uow, device, inputs, caps)

    def record_discovery(
        self, device_id: DeviceId, result: DiscoveryResult, now: datetime
    ) -> PollPlan:
        """Reconcile a discovery, store it, and return the device's new plan.

        Args:
            device_id: The device discovered.
            result: What discovery found.
            now: When discovery ran (UTC); stamped on every change.

        Returns:
            PollPlan: The new plan; empty when the agent reported no interfaces.

        Raises:
            NotFoundError, ConflictError, DuplicateError, StorageError: As
            ``record_discovery_report`` documents.
        """
        return self.record_discovery_report(device_id, result, now).plan

    def record_stage1_status(self, items: Sequence[StatusEvidence]) -> int:
        """Follow Stage 1 status changes in interface lifecycles (E.6); see ``stage1_lifecycle``.

        Args:
            items: At most one batch of status changes.

        Returns:
            int: Interfaces whose lifecycle changed.

        Raises:
            ConflictError, StorageError: The collector keeps the batch and retries.
        """
        return apply_stage1_status(self._factory, items, self._stale_after)

    def record_discovery_report(
        self, device_id: DeviceId, result: DiscoveryResult, now: datetime
    ) -> DiscoveryReport:
        """Record a discovery like ``record_discovery`` and say what it changed (DISC-T03).

        Args:
            device_id: The device discovered.
            result: What discovery found.
            now: When discovery ran (UTC).

        Returns:
            DiscoveryReport: The new plan, identity changes, and capability changes.

        Raises:
            NotFoundError: If the device does not exist.
            ConflictError, DuplicateError: If concurrent writers collided
                ``WRITE_ATTEMPTS`` times.
            StorageError: If the store fails.
        """
        with self._lock:
            keywords = self._inputs.keywords
        write = DiscoveryWrite(device_id, now, keywords, self._stale_after)
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            try:
                persisted = self._write(write, result)
                break
            except ConflictError, DuplicateError:
                if attempt == WRITE_ATTEMPTS:
                    raise
        moved = self._caps.remember(device_id, result)
        plan = self.poll_plan(device_id) or PollPlan(())
        return DiscoveryReport(plan, persisted.changes, moved)

    def _write(self, write: DiscoveryWrite, result: DiscoveryResult) -> Persisted:
        """Run one discovery write in its own unit of work.

        Args:
            write: Device, time, keywords, and stale threshold.
            result: What discovery found.

        Returns:
            Persisted: What the write decided and changed.

        Raises:
            NotFoundError, ConflictError, DuplicateError, StorageError: As
            ``record_discovery`` documents.
        """
        with self._factory() as uow:
            if uow.devices.get(write.device_id) is None:
                raise NotFoundError("device", write.device_id)
            persisted = persist_discovery(uow, write, result)
            self._audit(uow, write.device_id, persisted)
            uow.commit()
        return persisted

    def _audit(self, uow: MetadataUnitOfWork, device_id: DeviceId, persisted: Persisted) -> None:
        """Audit a discovery that changed identities (SEC-018); quiet rediscoveries are not.

        Args:
            uow: The open unit of work.
            device_id: The device.
            persisted: What the write decided and changed.

        Returns:
            None

        Raises:
            None
        """
        if not persisted.changes:
            return
        summary = ", ".join(f"{name} {n}" for name, n in persisted.changes)
        version = persisted.reconciliation.rules_version
        uow.audit.append(
            AuditEvent(
                new_id(AuditEventId),
                self._clock.now_utc(),
                "system",
                "discovery.reconcile",
                "device",
                device_id,
                AuditOutcome.SUCCESS,
                f"identity decisions ({version}): {summary}",
            )
        )
