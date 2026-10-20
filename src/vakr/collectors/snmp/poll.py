"""One SNMP poll of one device: Stage 1, then Stage 2 for up/up interfaces (9.2).

``poll_device`` runs the SNMP half of the poll state machine: Stage 1
(``sysUpTime`` and exact statuses), then Stage 2 only for interfaces that
policy collects *and* that are admin up and oper up right now (SNMP-006,
ADR-005). A Stage 2 failure never discards Stage 1's evidence, and a
device that ignored Stage 1 gets no Stage 2 requests at all.

``snmp_state`` classifies the result for the SNMP device observation
(10.2, E.2): no answer is ``TIMEOUT``; a collector-side failure is
``UNKNOWN``, never a device problem (invariant 2); an answer with nothing
usable is ``ERROR``; an answer with unanswered requests is ``PARTIAL``,
which still proves SNMP reachability (SNMP-013); otherwise ``RESPONDING``.

Dependencies:
    vakr.collectors.snmp: Plans and both stages.
    vakr.ports.clock, vakr.ports.snmp: Time and the session.

Safety:
    Read-only; bounded by the session's budget.

Example:
    >>> SnmpState.PARTIAL in ANSWERED
    True
"""

from dataclasses import dataclass, field
from datetime import datetime

from vakr.collectors.snmp.plan import PollPlan
from vakr.collectors.snmp.stage1 import Stage1Result, run_stage1
from vakr.collectors.snmp.stage2 import Stage2Result, run_stage2
from vakr.domain.enums import SnmpState, SnmpVersion
from vakr.domain.quality import Quality
from vakr.ports.clock import Clock
from vakr.ports.snmp import SnmpError, SnmpSession, SnmpStats
from vakr.ports.snmp.settings import DEFAULT_ABANDON_AFTER

ANSWERED = frozenset({SnmpState.RESPONDING, SnmpState.PARTIAL})
"""States that prove the agent answered this cycle (SNMP-013)."""


@dataclass(frozen=True, slots=True)
class SnmpPollResult:
    """The SNMP half of one device poll.

    Attributes:
        target: The agent as ``address:port``.
        version: The SNMP version used.
        started_at: When the SNMP exchange started (UTC).
        started_monotonic: The same moment on the monotonic clock.
        elapsed_s: Duration on the monotonic clock.
        stage1: Stage 1's result.
        stage2: Stage 2's result; empty when nothing was eligible, ``None``
            when Stage 1 got no answer.
        stats: SNMP effort for the whole poll.
        quality: Union of both stages' flags.
    """

    target: str
    version: SnmpVersion
    started_at: datetime
    started_monotonic: float
    elapsed_s: float
    stage1: Stage1Result
    stage2: Stage2Result | None
    stats: SnmpStats = field(default_factory=SnmpStats)
    quality: Quality = Quality.NONE

    @property
    def error(self) -> SnmpError | None:
        """Return the first failure of either stage.

        Args:
            None

        Returns:
            SnmpError | None: Stage 1's error, else Stage 2's, else ``None``.

        Raises:
            None
        """
        if self.stage1.error is not None:
            return self.stage1.error
        return None if self.stage2 is None else self.stage2.error


def snmp_state(result: SnmpPollResult) -> SnmpState:
    """Classify a poll for the SNMP device observation (10.2, E.2).

    Args:
        result: The poll.

    Returns:
        SnmpState: ``TIMEOUT``, ``UNKNOWN``, ``ERROR``, ``PARTIAL``, or
        ``RESPONDING``.

    Raises:
        None
    """
    error = result.error
    if result.stats.responses == 0:
        collector = error is not None and error.collector_side
        return SnmpState.UNKNOWN if collector else SnmpState.TIMEOUT
    stage1 = result.stage1
    usable = stage1.sys_uptime_ticks is not None or any(
        s.admin_status is not None or s.oper_status is not None for s in stage1.statuses
    )
    if not usable:
        return SnmpState.ERROR  # it answered, but with nothing vakr can use
    if result.quality & Quality.PARTIAL_RESPONSE:
        return SnmpState.PARTIAL
    return SnmpState.RESPONDING


async def poll_device(
    session: SnmpSession,
    plan: PollPlan,
    clock: Clock,
    abandon_after: int = DEFAULT_ABANDON_AFTER,
) -> SnmpPollResult:
    """Run Stage 1 and, for up/up collected interfaces, Stage 2.

    Args:
        session: The open session.
        plan: Interfaces and learned capabilities.
        clock: Timestamps.
        abandon_after: Consecutive unanswered Stage 2 batches before the
            rest is skipped.

    Returns:
        SnmpPollResult: Both stages, effort, and quality.

    Raises:
        None
    """
    started_at, started, before = clock.now_utc(), clock.monotonic(), session.stats
    stage1 = await run_stage1(session, plan, clock)
    stage2: Stage2Result | None = None
    if stage1.answered:
        statuses = stage1.by_index()
        eligible = [
            entry
            for entry in plan.interfaces
            if entry.collect and entry.if_index in statuses and statuses[entry.if_index].up_up
        ]
        stage2 = Stage2Result(())
        if eligible:
            stage2 = await run_stage2(session, eligible, statuses, clock, abandon_after)
    quality = stage1.quality | (Quality.NONE if stage2 is None else stage2.quality)
    return SnmpPollResult(
        session.target.label,
        session.target.version,
        started_at,
        started,
        clock.monotonic() - started,
        stage1,
        stage2,
        session.stats - before,
        quality,
    )
