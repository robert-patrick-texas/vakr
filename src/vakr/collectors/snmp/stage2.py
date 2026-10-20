"""SNMP Stage 2: counters for the interfaces that are up/up now (9.6, ADR-005).

Stage 2 GETs each eligible interface's counters by exact OID. Interfaces
are packed whole into batches under the session's varbind limit, so an
interface's counters share one request and one timestamp. Each batch is
stamped when it is sent, on both the UTC and the monotonic clock, so rates
later use the actual elapsed time (RATE-001).

Capabilities are learned as they are seen (SNMP-010). When an interface
planned as HC answers ``noSuchInstance`` for both HC octet counters, Stage 2
records ``hc_octets = False`` and fetches its 32-bit counters in the same
cycle, so the interface still gets a sample (SNMP-007, SNMP-T05). An absent
``ifCounterDiscontinuityTime`` or set of HC packet components is recorded
the same way and not requested again until a re-probe.

After ``abandon_after`` consecutive batches with no answer at all, the
remaining interfaces get explicit unanswered samples instead of requests,
so an agent that stops responding mid-poll cannot stretch the poll.

Dependencies:
    vakr.collectors.snmp: Plans, Stage 1 statuses, and counter OIDs.
    vakr.ports.clock, vakr.ports.snmp: Time and the session.

Safety:
    Read-only GETs; every unanswered value is ``None`` with a quality flag.

Example:
    >>> Stage2Result(()).quality.name
    'NONE'
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from vakr.collectors.snmp.batches import first_batch, learn
from vakr.collectors.snmp.counters import InterfaceSample, counter_oids, counter_values
from vakr.collectors.snmp.plan import CapabilityChange, InterfacePlan
from vakr.collectors.snmp.stage1 import InterfaceStatus
from vakr.domain.quality import Quality
from vakr.logic.counters import COUNTERS
from vakr.ports.clock import Clock
from vakr.ports.snmp import (
    SnmpError,
    SnmpSession,
    SnmpStats,
)
from vakr.ports.snmp.settings import DEFAULT_ABANDON_AFTER

_UNANSWERED = Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS


@dataclass(frozen=True, slots=True)
class Stage2Result:
    """The outcome of Stage 2.

    Attributes:
        samples: One sample per eligible interface, in plan order.
        changes: Capabilities learned this cycle (SNMP-010).
        stats: SNMP effort spent.
        error: The first failure, if any.
        quality: ``PARTIAL_RESPONSE`` and/or ``MISSING_FIELDS`` when any
            sample has them, plus retry and request-size flags.
    """

    samples: tuple[InterfaceSample, ...]
    changes: tuple[CapabilityChange, ...] = ()
    stats: SnmpStats = field(default_factory=SnmpStats)
    error: SnmpError | None = None
    quality: Quality = Quality.NONE


class _Stage2:
    """Accumulates one Stage 2 run.

    Attributes:
        samples: Samples so far.
        changes: Capabilities learned so far.
        error: The first failure.
        silent: Consecutive batches with no answer at all.
    """

    def __init__(
        self, session: SnmpSession, statuses: Mapping[int, InterfaceStatus], clock: Clock
    ) -> None:
        """Bind to the session.

        Args:
            session: The open session.
            statuses: Stage 1 statuses by ifIndex.
            clock: Timestamps for each batch.

        Returns:
            None

        Raises:
            None
        """
        self._session, self._statuses, self._clock = session, statuses, clock
        self.samples: list[InterfaceSample] = []
        self.changes: list[CapabilityChange] = []
        self.error: SnmpError | None = None
        self.silent = 0

    def _sample(
        self,
        entry: InterfacePlan,
        at: tuple[datetime, float],
        values: Mapping[str, int | None],
        missing: list[str],
        flags: Quality,
    ) -> None:
        """Record one sample.

        Args:
            entry: The interface plan.
            at: ``(sampled_at, sampled_monotonic)``.
            values: Counter values by field name.
            missing: Fields that did not arrive.
            flags: Quality flags for the sample.

        Returns:
            None

        Raises:
            None
        """
        status = self._statuses.get(entry.if_index)
        admin, oper = (None, None) if status is None else (status.admin_status, status.oper_status)
        self.samples.append(
            InterfaceSample(
                entry.if_index,
                entry.interface_id,
                at[0],
                at[1],
                entry.family,
                admin,
                oper,
                **values,
                missing=tuple(missing),
                quality=flags,
            )
        )

    def unanswered(self, batch: Sequence[InterfacePlan]) -> None:
        """Record explicit empty samples for interfaces not requested.

        Args:
            batch: The interfaces.

        Returns:
            None

        Raises:
            None
        """
        at = (self._clock.now_utc(), self._clock.monotonic())
        for entry in batch:
            self._sample(entry, at, {}, list(COUNTERS), _UNANSWERED)

    async def batch(self, batch: Sequence[InterfacePlan]) -> list[InterfacePlan]:
        """Sample one batch.

        Args:
            batch: The interfaces.

        Returns:
            list[InterfacePlan]: Interfaces to refetch with 32-bit counters.

        Raises:
            None
        """
        oids = {entry.if_index: counter_oids(entry) for entry in batch}
        at = (self._clock.now_utc(), self._clock.monotonic())
        result = await self._session.get([oid for o in oids.values() for oid in o.all()])
        self.silent = 0 if result.stats.responses else self.silent + 1
        self.error = self.error or result.error
        got, failed = result.by_oid(), set(result.failed)
        refetch = []
        for entry in batch:
            learned = learn(entry, oids[entry.if_index], got)
            self.changes += learned
            if any(change.capability == "hc_octets" for change in learned):
                refetch.append(entry.learned(learned[0]))
                continue
            values = counter_values(oids[entry.if_index], got)
            missing = [name for name in COUNTERS if values[name] is None]
            flags = Quality.MISSING_FIELDS if missing else Quality.NONE
            if any(oid in failed for oid in oids[entry.if_index].all()):
                flags |= Quality.PARTIAL_RESPONSE
            self._sample(entry, at, values, missing, flags)
        return refetch


async def run_stage2(
    session: SnmpSession,
    entries: Sequence[InterfacePlan],
    statuses: Mapping[int, InterfaceStatus],
    clock: Clock,
    abandon_after: int = DEFAULT_ABANDON_AFTER,
) -> Stage2Result:
    """Sample the counters of the given interfaces.

    Args:
        session: The open session.
        entries: Interfaces that were up/up in Stage 1 and that policy collects.
        statuses: Stage 1 statuses by ifIndex.
        clock: Timestamps for each batch.
        abandon_after: Consecutive unanswered batches before the rest is skipped.

    Returns:
        Stage2Result: Samples in plan order, learned capabilities, effort.

    Raises:
        None
    """
    before = session.stats
    run = _Stage2(session, statuses, clock)
    pending = list(entries)
    while pending:
        batch = first_batch(pending, session.max_varbinds)
        del pending[: len(batch)]
        if run.silent >= abandon_after:
            run.unanswered(batch)
            continue
        pending += await run.batch(batch)
    order = {entry.if_index: n for n, entry in enumerate(entries)}
    samples = tuple(sorted(run.samples, key=lambda s: order.get(s.if_index, len(order))))
    stats = session.stats - before
    quality = stats.quality
    for sample in samples:
        quality |= sample.quality
    return Stage2Result(samples, tuple(run.changes), stats, run.error, quality)
