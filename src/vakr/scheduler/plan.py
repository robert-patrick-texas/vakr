"""The poll plan: a due-time priority queue with anchored cadence (9.9, SCHED-010, SCHED-013).

Every scheduled device has one entry: its target, its offset within the
interval (``vakr.scheduler.phase``), and the plan-timeline time of its next
slot. One heap orders all devices by due time, whatever their interval, so
60-, 120- and 300-second devices interleave in one worker pool
(SCHED-013). Ties break on the device's order key, then its UUID, so the
order is deterministic.

Each popped slot advances its device by exactly one interval from the
*intended* slot, never from when a poll finished (SCHED-010, invariant 10).
If the scheduler falls behind, every past slot is still popped one by one,
so the driver records each missed occurrence instead of skipping it.

Re-planning (inventory or policy reload) keeps unchanged devices exactly
where they are: same phase, same next slot. A new device, or one whose
interval changed, takes the first slot of its own grid that is not earlier
than it would otherwise have polled, so a reload never bursts every device
at once.

Dependencies:
    heapq: The priority queue.
    vakr.scheduler.phase, targets: Offsets and targets.

Safety:
    Pure, bounded by the number of devices: stale heap entries are compacted
    once they outnumber live ones.

Example:
    >>> plan = Plan(PhaseRule("vakr-east"))
    >>> len(plan), plan.next_due_us()
    (0, None)
"""

import heapq
from collections.abc import Iterable
from dataclasses import dataclass

from vakr.domain.ids import DeviceId
from vakr.scheduler.phase import US_PER_S, PhaseRule, first_slot_us
from vakr.scheduler.targets import PollTarget

_COMPACT_SLACK = 64  # stale heap entries tolerated before a rebuild


@dataclass(frozen=True, slots=True)
class Slot:
    """One intended occurrence of a device poll.

    Attributes:
        target: The device and its settings at the time the slot came due.
        due_us: Intended start on the plan timeline (microseconds).
    """

    target: PollTarget
    due_us: int


@dataclass(frozen=True, slots=True)
class ReplanSummary:
    """What a re-plan changed.

    Attributes:
        added: Devices newly scheduled.
        removed: Devices no longer scheduled.
        rescheduled: Devices whose interval changed.
        kept: Devices left on their existing phase and next slot.
    """

    added: int
    removed: tuple[DeviceId, ...]
    rescheduled: int
    kept: int


@dataclass(slots=True)
class _Entry:
    """Mutable plan state of one device.

    Attributes:
        target: Current target.
        offset_us: Offset within the interval.
        order_key: Tie-break key from the order mode.
        next_us: Next slot on the plan timeline.
        generation: Heap entries carrying another generation are stale.
    """

    target: PollTarget
    offset_us: int
    order_key: int
    next_us: int
    generation: int


class Plan:
    """The set of scheduled devices and their next slots."""

    def __init__(self, rule: PhaseRule) -> None:
        """Create an empty plan.

        Args:
            rule: Phase and order controls of this collector.

        Returns:
            None

        Raises:
            None
        """
        self._rule = rule
        self._entries: dict[DeviceId, _Entry] = {}
        self._heap: list[tuple[int, int, int, int, DeviceId]] = []
        self._generation = 0

    def __len__(self) -> int:
        """Return the number of scheduled devices.

        Args:
            None

        Returns:
            int: Device count.

        Raises:
            None
        """
        return len(self._entries)

    def intervals_s(self) -> tuple[int, int] | None:
        """Return the shortest and longest effective interval planned (OBS-006, E.33).

        Args:
            None

        Returns:
            tuple[int, int] | None: Seconds, or ``None`` with nothing planned.

        Raises:
            None
        """
        found = [e.target.effective_interval_s for e in self._entries.values()]
        return (min(found), max(found)) if found else None

    def next_slot_us(self, device_id: DeviceId) -> int | None:
        """Return a device's next slot, for diagnostics and tests.

        Args:
            device_id: The device.

        Returns:
            int | None: Plan-timeline microseconds, or ``None`` if unscheduled.

        Raises:
            None
        """
        entry = self._entries.get(device_id)
        return None if entry is None else entry.next_us

    def replan(self, targets: Iterable[PollTarget], now_us: int) -> ReplanSummary:
        """Replace the scheduled set, keeping unchanged devices in place.

        Args:
            targets: Every device that should be scheduled; for duplicate
                devices the last target wins.
            now_us: Current plan-timeline time.

        Returns:
            ReplanSummary: Counts of what changed.

        Raises:
            None
        """
        incoming = {target.device_id: target for target in targets}
        removed = tuple(d for d in self._entries if d not in incoming)
        for device_id in removed:
            del self._entries[device_id]
        added = rescheduled = 0
        for device_id, target in incoming.items():
            entry = self._entries.get(device_id)
            if entry is None:
                self._schedule(target, now_us)
                added += 1
            elif entry.target.effective_interval_s == target.effective_interval_s:
                entry.target = target  # same interval, so same phase and next slot
            else:
                self._schedule(target, max(now_us, entry.next_us))
                rescheduled += 1
        if len(self._heap) > 2 * len(self._entries) + _COMPACT_SLACK:
            self._rebuild()
        kept = len(incoming) - added - rescheduled
        return ReplanSummary(added, removed, rescheduled, kept)

    def next_due_us(self) -> int | None:
        """Return the earliest due slot, discarding stale heap entries.

        Args:
            None

        Returns:
            int | None: Plan-timeline microseconds, or ``None`` when empty.

        Raises:
            None
        """
        while self._heap and not self._live(self._heap[0]):
            heapq.heappop(self._heap)
        return self._heap[0][0] if self._heap else None

    def pop_due(self, now_us: int) -> Slot | None:
        """Take the earliest slot that is due and queue that device's next one.

        Args:
            now_us: Current plan-timeline time.

        Returns:
            Slot | None: The due slot, or ``None`` when nothing is due.

        Raises:
            None
        """
        due = self.next_due_us()
        if due is None or due > now_us:
            return None
        _, _, _, _, device_id = heapq.heappop(self._heap)
        entry = self._entries[device_id]
        entry.next_us = due + entry.target.effective_interval_s * US_PER_S
        self._push(device_id, entry)
        return Slot(entry.target, due)

    def _schedule(self, target: PollTarget, not_before_us: int) -> None:
        """(Re)place a device on its grid at the first slot not before a time.

        Args:
            target: The device and its settings.
            not_before_us: Earliest acceptable slot.

        Returns:
            None

        Raises:
            None
        """
        interval_s = target.effective_interval_s
        offset = self._rule.offset_us(target.device_id, interval_s)
        first = first_slot_us(offset, interval_s * US_PER_S, not_before_us)
        self._generation += 1
        key = self._rule.order_key(target.device_id)
        entry = _Entry(target, offset, key, first, self._generation)
        self._entries[target.device_id] = entry
        self._push(target.device_id, entry)

    def _push(self, device_id: DeviceId, entry: _Entry) -> None:
        """Add a device's next slot to the heap.

        Args:
            device_id: The device.
            entry: Its plan state.

        Returns:
            None

        Raises:
            None
        """
        item = (entry.next_us, entry.order_key, device_id.int, entry.generation, device_id)
        heapq.heappush(self._heap, item)

    def _live(self, item: tuple[int, int, int, int, DeviceId]) -> bool:
        """Report whether a heap entry still describes its device's next slot.

        Args:
            item: Heap entry.

        Returns:
            bool: False for removed devices and superseded generations.

        Raises:
            None
        """
        entry = self._entries.get(item[4])
        return entry is not None and entry.generation == item[3]

    def _rebuild(self) -> None:
        """Drop stale heap entries.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._heap = [item for item in self._heap if self._live(item)]
        heapq.heapify(self._heap)
