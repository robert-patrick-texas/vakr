"""Admission control: bounded workers, no overlap, recorded misses (SCHED-012, SCHED-016, -017).

A due slot either starts on a free worker, waits in a FIFO for one, or is
recorded as missed with a reason. Nothing is dropped silently:

* ``OVERRUN``: the device's previous poll is still talking to it, so a
  second session never starts (SCHED-012). Recorded as ``MISSED_OVERRUN``.
* ``NO_WORKER``: every worker stayed busy for ``max_start_delay``.
* ``LATE_DISPATCH``: the scheduler itself reached the slot more than
  ``max_start_delay`` late (a stalled event loop or a full output queue).
* ``SUPERSEDED``: the device's next slot came due while this one waited.
* ``DRAINING``: shutdown began before the slot could start (16.4).
* ``STORAGE_FULL``: the spool stopped admitting new work (EXHAUSTED, 6.4).

All but ``OVERRUN`` are recorded as ``NOT_ATTEMPTED``: unknown, never a
target failure (SCHED-017, OBS-003). The wait queue holds at most one slot
per device, so it is bounded by the inventory size.

A worker stays busy until its result has been handed to the output queue,
so a full queue slows admission instead of piling up finished results
(6.4). The device's own session ends as soon as its steps finish.

Dependencies:
    collections.OrderedDict: FIFO with removal by device.
    vakr.scheduler.plan: ``Slot``.

Safety:
    Pure bookkeeping; no clock reads, no I/O.

Example:
    >>> gate = Admission(workers=2)
    >>> gate.free_workers, gate.waiting
    (2, 0)
"""

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.ids import DeviceId
from vakr.scheduler.plan import Slot


class MissReason(StrEnum):
    """Why an occurrence was not polled (SCHED-012, SCHED-017, 16.4)."""

    OVERRUN = "overrun"
    NO_WORKER = "no_worker"
    LATE_DISPATCH = "late_dispatch"
    SUPERSEDED = "superseded"
    DRAINING = "draining"
    STORAGE_FULL = "storage_full"


CAPACITY_REASONS = frozenset(
    {MissReason.NO_WORKER, MissReason.LATE_DISPATCH, MissReason.SUPERSEDED, MissReason.STORAGE_FULL}
)
"""Misses caused by collector capacity; they also carry ``COLLECTOR_DEGRADED``."""


@dataclass(frozen=True, slots=True)
class Miss:
    """An occurrence that will not be polled.

    Attributes:
        slot: The occurrence.
        reason: Why.
    """

    slot: Slot
    reason: MissReason


class Admission:
    """Worker-slot and per-device session bookkeeping."""

    def __init__(self, workers: int) -> None:
        """Create the gate.

        Args:
            workers: Worker pool size, at least 1.

        Returns:
            None

        Raises:
            ValueError: If ``workers`` is below 1.
        """
        if workers < 1:
            raise ValueError("the worker pool needs at least one worker")
        self.capacity = workers
        self._busy = 0
        self._sessions: set[DeviceId] = set()
        self._waiting: OrderedDict[DeviceId, Slot] = OrderedDict()

    @property
    def free_workers(self) -> int:
        """Return how many workers are idle.

        Args:
            None

        Returns:
            int: ``capacity`` minus busy workers.

        Raises:
            None
        """
        return self.capacity - self._busy

    @property
    def busy_workers(self) -> int:
        """Return how many workers hold a poll or its unsent result.

        Args:
            None

        Returns:
            int: Busy workers.

        Raises:
            None
        """
        return self._busy

    @property
    def waiting(self) -> int:
        """Return how many slots wait for a worker.

        Args:
            None

        Returns:
            int: Waiting slots, at most one per device.

        Raises:
            None
        """
        return len(self._waiting)

    def offer(self, slot: Slot, now_us: int, max_delay_us: int) -> Miss | None:
        """Accept a due slot into the wait queue, or decide it is missed.

        Args:
            slot: The due occurrence.
            now_us: Current plan-timeline time.
            max_delay_us: ``max_start_delay`` in microseconds.

        Returns:
            Miss | None: The occurrence that will not run, which is either
            ``slot`` itself or the older slot it superseded; ``None`` when
            ``slot`` simply waits.

        Raises:
            None
        """
        device_id = slot.target.device_id
        if device_id in self._sessions:
            return Miss(slot, MissReason.OVERRUN)
        if now_us - slot.due_us > max_delay_us:
            return Miss(slot, MissReason.LATE_DISPATCH)
        older = self._waiting.pop(device_id, None)
        self._waiting[device_id] = slot
        return None if older is None else Miss(older, MissReason.SUPERSEDED)

    def expire(self, now_us: int, max_delay_us: int) -> list[Miss]:
        """Give up on slots that waited too long for a worker.

        Args:
            now_us: Current plan-timeline time.
            max_delay_us: ``max_start_delay`` in microseconds.

        Returns:
            list[Miss]: ``NO_WORKER`` misses, oldest first.

        Raises:
            None
        """
        expired = [s for s in self._waiting.values() if now_us - s.due_us > max_delay_us]
        for slot in expired:
            del self._waiting[slot.target.device_id]
        return [Miss(slot, MissReason.NO_WORKER) for slot in expired]

    def next_expiry_us(self, max_delay_us: int) -> int | None:
        """Return when the oldest waiting slot expires.

        Args:
            max_delay_us: ``max_start_delay`` in microseconds.

        Returns:
            int | None: Plan-timeline microseconds, or ``None`` when none waits.

        Raises:
            None
        """
        if not self._waiting:
            return None
        oldest = min(slot.due_us for slot in self._waiting.values())
        return oldest + max_delay_us + 1

    def start_next(self) -> Slot | None:
        """Take the oldest waiting slot onto a free worker.

        Args:
            None

        Returns:
            Slot | None: The slot to start, or ``None`` when no worker is free
            or nothing waits.

        Raises:
            None
        """
        if self._busy >= self.capacity or not self._waiting:
            return None
        device_id, slot = self._waiting.popitem(last=False)
        self._busy += 1
        self._sessions.add(device_id)
        return slot

    def end_session(self, device_id: DeviceId) -> None:
        """Record that a device's poll stopped talking to it.

        Args:
            device_id: The device.

        Returns:
            None

        Raises:
            None
        """
        self._sessions.discard(device_id)

    def release_worker(self) -> None:
        """Free the worker of a poll whose result has been handed off.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._busy = max(0, self._busy - 1)

    def forget(self, device_ids: tuple[DeviceId, ...]) -> None:
        """Drop waiting slots of devices that are no longer scheduled.

        Their occurrences are not misses: the device was unscheduled (9.2).

        Args:
            device_ids: Removed devices.

        Returns:
            None

        Raises:
            None
        """
        for device_id in device_ids:
            self._waiting.pop(device_id, None)

    def drain(self) -> list[Miss]:
        """Empty the wait queue at shutdown (16.4).

        Args:
            None

        Returns:
            list[Miss]: ``DRAINING`` misses, oldest first.

        Raises:
            None
        """
        slots = list(self._waiting.values())
        self._waiting.clear()
        return [Miss(slot, MissReason.DRAINING) for slot in slots]
