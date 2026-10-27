"""Interface status records and lifecycle evidence from each Stage 1 poll (E.6, E.35).

Stage 1 reads every planned interface's admin and oper status each poll.
``StatusTracker`` remembers the last pair it recorded per interface (by
persistent UUID, never by ``ifIndex``) and, for each poll, returns an
``InterfaceStatusObservation`` for every interface whose pair changed, and
for every interface at its device's first Stage 1 after the collector
started (``baseline``). So a link that goes down and comes back while its
device answers leaves two small records, and the acceptance gap report can
say why the interface had no samples (MVP-ACC-10).

The same changes are lifecycle evidence (E.6: an interface is ``active``
while up/up at the last Stage 1). They are kept per interface, newest
wins, so the queue is bounded by the interfaces planned; ``lifecycle_loop``
hands at most ``LIFECYCLE_BATCH`` of them every few seconds to the
inventory in a worker thread, so the event loop never waits on metadata.

Dependencies:
    asyncio, contextlib, logging: The flush loop.
    vakr.collectors.snmp: Stage 1 results and plans.
    vakr.domain: Status records and IF-MIB statuses.

Safety:
    Holds UUIDs and IF-MIB integers only. Bounded: one entry per planned
    interface, and one flush batch at a time.

Example:
    >>> StatusTracker().pending
    0
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence

from vakr.collectors.snmp import Stage1Result
from vakr.collectors.snmp.plan import PollPlan
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId, new_id
from vakr.domain.quality import Quality
from vakr.domain.status_observations import InterfaceStatusObservation
from vakr.inventory.stage1_lifecycle import StatusEvidence

_LOG = logging.getLogger("vakr.collector")

LIFECYCLE_BATCH = 500
"""Lifecycle changes one flush hands to the inventory at most (E.6)."""

LIFECYCLE_FLUSH_S = 5.0
"""How often the flush runs: the inventory change poll's cadence (E.27)."""


class StatusTracker:
    """The last status recorded per interface, and lifecycle evidence waiting for the inventory."""

    def __init__(self) -> None:
        """Start with nothing recorded, so each interface's first Stage 1 is a baseline.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._last: dict[InterfaceId, tuple[int | None, int | None]] = {}
        self._waiting: dict[InterfaceId, StatusEvidence] = {}

    @property
    def pending(self) -> int:
        """Lifecycle evidence waiting for the inventory.

        Args:
            None

        Returns:
            int: Interfaces with evidence not yet handed over.

        Raises:
            None
        """
        return len(self._waiting)

    def observe(
        self, ids: tuple[PollId, CollectorId, DeviceId], plan: PollPlan, stage1: Stage1Result,
        quality: Quality = Quality.NONE,
    ) -> tuple[InterfaceStatusObservation, ...]:  # fmt: skip
        """Return the status records of one Stage 1 and queue their lifecycle evidence.

        Args:
            ids: The poll, collector, and device.
            plan: The plan Stage 1 ran.
            stage1: Its result.
            quality: Flags the records carry, such as ``MAINTENANCE``.

        Returns:
            tuple[InterfaceStatusObservation, ...]: Changed and first-seen interfaces.

        Raises:
            None
        """
        if not stage1.answered:
            return ()
        statuses, found = stage1.by_index(), []
        for entry in plan.interfaces:
            status = statuses.get(entry.if_index)
            iid = entry.interface_id
            if iid is None or status is None:
                continue  # no identity yet, or not read: ifIndex is never identity
            admin, oper = status.admin_status, status.oper_status
            if admin is None and oper is None:
                continue
            pair = (None if admin is None else int(admin), None if oper is None else int(oper))
            previous = self._last.get(iid)
            if previous == pair:
                continue
            self._last[iid] = pair
            found.append(InterfaceStatusObservation(
                new_id(ObservationId), ids[0], ids[1], ids[2], iid, stage1.sampled_at,
                entry.if_index, admin, oper, baseline=previous is None, quality=quality,
            ))  # fmt: skip
            self._waiting[iid] = StatusEvidence(iid, stage1.sampled_at, admin, oper)
        return tuple(found)

    def take(self, limit: int = LIFECYCLE_BATCH) -> list[StatusEvidence]:
        """Hand over the oldest-queued lifecycle evidence, at most ``limit``.

        Args:
            limit: Most items.

        Returns:
            list[StatusEvidence]: The items, removed from the queue.

        Raises:
            None
        """
        chosen = list(self._waiting)[:limit]
        return [self._waiting.pop(iid) for iid in chosen]

    def give_back(self, items: Sequence[StatusEvidence]) -> None:
        """Requeue evidence the inventory could not take, unless newer evidence arrived.

        Args:
            items: The items.

        Returns:
            None

        Raises:
            None
        """
        for item in items:
            self._waiting.setdefault(item.interface_id, item)


async def lifecycle_loop(
    stop: asyncio.Event, tracker: StatusTracker, inventory: object,
    interval_s: float = LIFECYCLE_FLUSH_S,
) -> None:  # fmt: skip
    """Hand lifecycle evidence to the inventory in a worker thread until ``stop`` is set.

    Args:
        stop: Set at shutdown.
        tracker: The tracker.
        inventory: The collector's inventory; its ``record_stage1_status`` takes the
            evidence. One without it (``EmptyInventory``) gets none, and nothing is kept.
        interval_s: How often to flush.

    Returns:
        None

    Raises:
        None
    """
    apply: Callable[[Sequence[StatusEvidence]], int] | None = getattr(
        inventory, "record_stage1_status", None
    )
    failing = False
    while not stop.is_set():
        batch = tracker.take()
        if batch and apply is not None:
            try:
                await asyncio.to_thread(apply, batch)
                failing = False
            except Exception as exc:  # noqa: BLE001 - keep the evidence for the next pass
                tracker.give_back(batch)
                if not failing:
                    _LOG.warning("cannot record interface lifecycle (%s); will retry",
                                 type(exc).__name__,
                                 extra={"event_id": "collector.lifecycle_unavailable"})  # fmt: skip
                failing = True
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval_s)
