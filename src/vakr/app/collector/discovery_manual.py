"""Manual rediscovery inside the discovery runner (DISC-003, DISC-010).

``ManualDiscoveryRunner`` is the collector's ``DiscoveryRunner`` plus
``run_now``: a device an administrator asked to rediscover gets a due time
before every scheduled one, so it starts as soon as a discovery slot is
free. If a discovery of the same device is already running, the manual one
waits for it and then runs, so the result the administrator sees is fresh.
The hook receives the ``Attempt``; a success restarts the device's
rediscovery interval like any success (DISC-002). A device that leaves the
runner before its turn gets a ``gone`` attempt, so its operation never
waits forever.

Dependencies:
    asyncio: Background hook tasks.
    vakr.app.collector.discovery, discovery_attempt: The runner and its outcomes.

Safety:
    Bounded: at most one waiting manual rediscovery per device, and the
    watcher claims no more than ``free_slots`` allows.

Example:
    >>> ManualDiscoveryRunner.__mro__[1].__name__
    'DiscoveryRunner'
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from vakr.app.collector.discovery import DiscoveryRunner
from vakr.app.collector.discovery_attempt import GONE, Attempt
from vakr.app.collector.inventory import InventorySource
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.timings import ServiceTimings
from vakr.domain.ids import DeviceId
from vakr.ports.clock import Clock
from vakr.ports.snmp import SnmpSessionFactory
from vakr.scheduler.targets import PollTarget

type Hook = Callable[[Attempt], Awaitable[None]]
"""Awaited with a manual rediscovery's outcome; not when discovery stops first."""
_FIRST = float("-inf")
"""The due time of a waiting manual rediscovery: before every scheduled one."""


class ManualDiscoveryRunner(DiscoveryRunner):
    """The discovery runner with manual rediscoveries.

    Attributes:
        planned: True once the first re-plan gave the runner its devices.
    """

    def __init__(
        self, inventory: InventorySource, factory: SnmpSessionFactory, plans: PlanBook,
        clock: Clock, timings: ServiceTimings, collector_name: str,
    ) -> None:  # fmt: skip
        """Create the runner with no manual rediscovery waiting.

        Args:
            inventory: Where results are recorded.
            factory: Opens SNMP sessions.
            plans: Working plans.
            clock: Monotonic and UTC time.
            timings: Concurrency, spread, and backoff (E.27).
            collector_name: This collector, for the E.11 placement hash.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(inventory, factory, plans, clock, timings, collector_name)
        self._manual: dict[DeviceId, Hook] = {}
        self._hooks: set[asyncio.Future[None]] = set()
        self.planned = False

    def run_now(self, device_id: DeviceId, hook: Hook) -> bool:
        """Rediscover a device as soon as a slot is free.

        Args:
            device_id: The device.
            hook: Awaited with the outcome.

        Returns:
            bool: False when this runner does not discover the device, or a
            manual rediscovery of it is already waiting.

        Raises:
            None
        """
        if device_id not in self._targets or device_id in self._manual:
            return False
        self._manual[device_id] = hook
        self._due[device_id] = _FIRST
        self._wake.set()
        return True

    def discovers(self, device_id: DeviceId) -> bool:
        """Report whether this runner discovers a device at all.

        Args:
            device_id: The device.

        Returns:
            bool: True when it is scheduled and polled over SNMP.

        Raises:
            None
        """
        return device_id in self._targets

    def free_slots(self) -> int:
        """Return how many more manual rediscoveries could start now.

        Args:
            None

        Returns:
            int: The concurrency left after running and waiting manual ones.

        Raises:
            None
        """
        busy = self._running.keys() | self._manual.keys()
        return max(self._timings.discovery_concurrency - len(busy), 0)

    def replan(self, targets: Iterable[PollTarget]) -> None:
        """Follow an inventory change, keeping waiting manual rediscoveries first.

        Args:
            targets: Every scheduled device.

        Returns:
            None

        Raises:
            None
        """
        super().replan(targets)
        self.planned = True
        for device_id in list(self._manual):
            if device_id in self._due:
                self._due[device_id] = _FIRST
            else:
                hook = self._manual.pop(device_id)
                self._spawn(hook(Attempt(GONE, "the device is no longer discovered here")))

    def _settle(self, target: PollTarget, status: str, why: str) -> None:
        """Schedule as usual, except that a manual rediscovery waiting for this one goes next.

        Args:
            target: The device.
            status: ``ok``, ``failed``, or ``gone``.
            why: Operator words for a failure.

        Returns:
            None

        Raises:
            None
        """
        super()._settle(target, status, why)
        device_id = target.device_id
        if device_id not in self._manual:
            return
        if device_id in self._due:
            self._due[device_id] = _FIRST
        else:  # removed from inventory meanwhile: the waiting request ends too
            self._spawn(self._manual.pop(device_id)(Attempt(GONE, "removed from inventory")))

    async def _discover(self, target: PollTarget) -> Attempt:
        """Discover one device; a manual rediscovery waiting at its start gets the outcome.

        Args:
            target: The device.

        Returns:
            Attempt: How it ended.

        Raises:
            asyncio.CancelledError: When ``stop`` cancels it.
        """
        hook = self._manual.pop(target.device_id, None)
        done = await super()._discover(target)
        if hook is not None:
            await hook(done)
        return done

    def _spawn(self, call: Awaitable[None]) -> None:
        """Run a hook in the background, referenced until it ends.

        Args:
            call: The awaitable.

        Returns:
            None

        Raises:
            None
        """
        task = asyncio.ensure_future(call)
        self._hooks.add(task)
        task.add_done_callback(self._hooks.discard)

    async def stop(self) -> None:
        """Stop discovery; waiting manual rediscoveries stay ``running`` for restart recovery.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._manual.clear()
        await super().stop()
        await asyncio.gather(*self._hooks, return_exceptions=True)
