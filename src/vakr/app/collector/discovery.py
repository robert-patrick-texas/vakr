"""Discovery inside the collector: first discovery and rediscovery, bounded (DISC-001/002).

``DiscoveryRunner`` runs at most ``discovery_concurrency`` discoveries at
once, outside the scheduler's workers, so a slow walk never delays a poll.
Answered ones go to ``InventorySource.record_discovery`` in a worker thread.

Due times follow ``vakr.app.collector.discovery_due``: a success waits a
whole rediscovery interval, even if inventory still has no plan for the
device; a failure backs off from 60 s to at most 1 h. Polling never waits
for discovery: a device without a plan still gets ICMP and ``sysUpTime.0``.
A device inventory no longer knows (``NotFoundError``) was removed
meanwhile; the next re-plan drops it, and it is not logged as a failure.

Dependencies:
    asyncio: Tasks and the wake event.
    vakr.discovery: The discovery run.
    vakr.app.collector: Plans, due times, the inventory contract.

Safety:
    Bounded by ``discovery_concurrency``; ``stop`` cancels running walks,
    whose sessions close on cancellation.

Example:
    >>> DiscoveryRunner.__name__
    'DiscoveryRunner'
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable

from vakr.app.collector import discovery_due as due_rules
from vakr.app.collector.discovery_due import Outcome, discoverable
from vakr.app.collector.inventory import InventorySource
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.snmp_parts import snmp_target
from vakr.app.collector.timings import ServiceTimings
from vakr.collectors.snmp.config import SnmpConfig
from vakr.discovery import discover
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import DeviceId
from vakr.ports.clock import Clock
from vakr.ports.snmp import SnmpSessionError, SnmpSessionFactory
from vakr.scheduler.targets import PollTarget

_LOG = logging.getLogger(__name__)
_OK, _FAILED, _GONE = "ok", "failed", "gone"


class DiscoveryRunner:
    """Runs due discoveries, a bounded few at a time.

    Attributes:
        config: Current ``[snmp]`` settings; replaced on reload.
        on_plan: Awaited with a device ID after its plan changed.
        completed: Discoveries recorded since start.
        failed: Discoveries that got no answer or could not run.
    """

    def __init__(
        self, inventory: InventorySource, factory: SnmpSessionFactory, plans: PlanBook,
        clock: Clock, timings: ServiceTimings, collector_name: str,
    ) -> None:  # fmt: skip
        """Create an idle runner; ``replan`` gives it devices.

        Args:
            inventory: Where results are recorded.
            factory: Opens SNMP sessions.
            plans: Working plans, updated with each new plan.
            clock: Monotonic and UTC time.
            timings: Concurrency, spread, and backoff (E.27).
            collector_name: This collector, for the E.11 placement hash.

        Returns:
            None

        Raises:
            None
        """
        self._inventory, self._factory, self._plans = inventory, factory, plans
        self._clock, self._timings, self._name = clock, timings, collector_name
        self.config: SnmpConfig | None = None
        self.on_plan: Callable[[DeviceId], Awaitable[None]] | None = None
        self.completed = self.failed = 0
        self._targets: dict[DeviceId, PollTarget] = {}
        self._due: dict[DeviceId, float] = {}
        self._outcomes: dict[DeviceId, Outcome] = {}
        self._running: dict[DeviceId, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._stopping = False

    def due_in(self, device_id: DeviceId) -> float | None:
        """Return seconds until a device's next discovery.

        Args:
            device_id: The device.

        Returns:
            float | None: Seconds (negative when overdue), or ``None`` when
            not scheduled for discovery.

        Raises:
            None
        """
        due = self._due.get(device_id)
        return None if due is None else due - self._clock.monotonic()

    def replan(self, targets: Iterable[PollTarget]) -> None:
        """Follow an inventory change: new devices get due times, removed ones are dropped.

        Args:
            targets: Every scheduled device.

        Returns:
            None

        Raises:
            None
        """
        found = {t.device_id: t for t in targets if discoverable(t)}
        due: dict[DeviceId, float] = {}
        for device_id, target in found.items():
            old = self._targets.get(device_id)
            interval = target.device.discovery_interval_s
            same = old is not None and old.device.discovery_interval_s == interval
            keep = same and device_id in self._due
            due[device_id] = self._due[device_id] if keep else self._schedule(target)
        self._targets, self._due = found, due
        self._outcomes = {d: o for d, o in self._outcomes.items() if d in found}
        self._wake.set()

    def _schedule(self, target: PollTarget) -> float:
        """Return a device's next due time on the monotonic clock (``discovery_due`` rules).

        Args:
            target: The device.

        Returns:
            float: Monotonic seconds.

        Raises:
            None
        """
        device_id, mono, t = target.device_id, self._clock.monotonic(), self._timings
        outcome = self._outcomes.get(device_id, Outcome())
        if outcome.failures:
            return mono + due_rules.retry_delay_s(
                outcome.failures, t.discovery_retry_min_s, t.discovery_retry_max_s
            )
        config = self.config
        default = due_rules.DEFAULT_REDISCOVERY_S
        if config is not None:
            default = config.rediscovery_interval.total_seconds()
        interval = due_rules.discovery_interval_s(target.device, default)
        if outcome.succeeded:
            return mono + interval  # even when inventory still has no plan (rule 2)
        if not self._plans.has(device_id):
            return mono + due_rules.first_delay_s(self._name, device_id, t.discovery_spread_s)
        wall = self._clock.now_utc().timestamp()
        return mono + (due_rules.rediscovery_at_s(wall, interval, self._name, device_id) - wall)

    async def run(self) -> None:
        """Start due discoveries until ``stop``.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        limit = self._timings.discovery_concurrency
        while not self._stopping:
            now = self._clock.monotonic()
            waiting = sorted((t, d) for d, t in self._due.items() if d not in self._running)
            for due, device_id in waiting:
                if due > now or len(self._running) >= limit:
                    break
                task = asyncio.create_task(self._discover(self._targets[device_id]))
                self._running[device_id] = task
            pending = [t for t, d in waiting if d not in self._running]
            # When every slot is busy, a finishing discovery sets the event.
            full = len(self._running) >= limit
            delay = None if full or not pending else max(0.0, min(pending) - now)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)

    async def stop(self) -> None:
        """Stop starting discoveries and cancel the ones running (16.4 step 2).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._stopping = True
        self._wake.set()
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _discover(self, target: PollTarget) -> None:
        """Discover one device, then record the outcome and the next due time.

        Args:
            target: The device.

        Returns:
            None

        Raises:
            asyncio.CancelledError: When ``stop`` cancels it.
        """
        status, why = _FAILED, "an error inside the collector"
        try:
            status, why = await self._attempt(target)
        except Exception as exc:  # noqa: BLE001 - one device's failure must not stop discovery
            why = f"an error inside the collector ({type(exc).__name__})"
        finally:
            self._running.pop(target.device_id, None)
            self._settle(target, status, why)
            self._wake.set()

    def _settle(self, target: PollTarget, status: str, why: str) -> None:
        """Record an outcome, log a change of state once, and schedule the next attempt.

        Args:
            target: The device.
            status: ``ok``, ``failed``, or ``gone``.
            why: Operator-language reason for a failure; never a secret.

        Returns:
            None

        Raises:
            None
        """
        device_id, name = target.device_id, target.device.name
        if status == _GONE:  # removed meanwhile: never retried, and the re-plan agrees
            self._due.pop(device_id, None)
            self._targets.pop(device_id, None)
            return
        outcome = self._outcomes.setdefault(device_id, Outcome())
        if status == _OK:
            if outcome.failures:
                _LOG.info("discovery of %s works again", name,
                          extra={"event_id": "discovery.recovered"})  # fmt: skip
            outcome.failures, outcome.succeeded = 0, True
            self.completed += 1
        elif status == _FAILED:
            outcome.failures += 1
            self.failed += 1
            if outcome.failures == 1:
                _LOG.warning("discovery of %s got no result: %s; retrying with backoff up to "
                             "%g s", name, why, self._timings.discovery_retry_max_s,
                             extra={"event_id": "discovery.no_result"})  # fmt: skip
        if device_id in self._targets:
            self._due[device_id] = self._schedule(target)

    async def _attempt(self, target: PollTarget) -> tuple[str, str]:
        """Walk the device and hand an answered result to inventory.

        Args:
            target: The device.

        Returns:
            tuple[str, str]: ``ok``, ``failed``, or ``gone`` (inventory no
            longer knows the device), and why it failed.

        Raises:
            StorageError: If inventory cannot store the result.
        """
        config, device = self.config, target.device
        if config is None:
            return _FAILED, "no SNMP settings loaded"
        try:
            async with self._factory.open(snmp_target(device, config.port), config.settings) as s:
                result = await discover(s, self._clock, config.settings.abandon_after)
        except SnmpSessionError as exc:
            return _FAILED, exc.error.message
        if not result.answered:
            return _FAILED, "the agent did not answer"
        now = self._clock.now_utc()
        try:
            plan = await asyncio.to_thread(self._inventory.record_discovery, device.id, result, now)
        except NotFoundError:
            return _GONE, "removed from inventory"
        self._plans.adopt(device.id, plan, now)
        if self.on_plan is not None:
            await self.on_plan(device.id)
        return _OK, ""
