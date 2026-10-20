"""SNMP Stage 1: ``sysUpTime.0`` and exact interface status OIDs (9.5, SNMP-005).

Stage 1 runs in every SNMP cycle. It GETs ``sysUpTime.0``, which feeds the
uptime-continuity rules of Appendix E.5, and ``ifAdminStatus.i`` /
``ifOperStatus.i`` for each interface whose lifecycle or policy requires a
current check, by exact OID from the known inventory. It never walks
IF-MIB (SNMP-004). Varbinds that arrive are kept even when others time out;
an interface whose status did not arrive has an unknown state for this
cycle, never a carried-forward one (9.5).

Only interfaces that are admin up *and* oper up now go on to Stage 2
(SNMP-006, ADR-005).

Dependencies:
    vakr.collectors.snmp.plan: The poll plan.
    vakr.discovery.mib: The OIDs.
    vakr.ports.clock, vakr.ports.snmp: Time and the session.

Safety:
    Read-only GETs, batched under the session's size limit.

Example:
    >>> InterfaceStatus(3, IfAdminStatus.UP, IfOperStatus.UP).up_up
    True
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from vakr.collectors.snmp.plan import PollPlan
from vakr.discovery.mib import IF_COLUMNS, SYS_UPTIME, instance
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.quality import Quality
from vakr.ports.clock import Clock
from vakr.ports.snmp import Oid, SnmpError, SnmpSession, SnmpStats, ValueKind, VarBind

_ADMIN = IF_COLUMNS["ifAdminStatus"]
_OPER = IF_COLUMNS["ifOperStatus"]


@dataclass(frozen=True, slots=True)
class InterfaceStatus:
    """One interface's current state from Stage 1.

    Attributes:
        if_index: The interface.
        admin_status: ``ifAdminStatus``, or ``None`` when not returned.
        oper_status: ``ifOperStatus``, or ``None`` when not returned.
        quality: ``MISSING_FIELDS`` when either status is unknown.
    """

    if_index: int
    admin_status: IfAdminStatus | None
    oper_status: IfOperStatus | None
    quality: Quality = Quality.NONE

    @property
    def up_up(self) -> bool:
        """Whether the interface is admin up and oper up now (SNMP-006).

        Args:
            None

        Returns:
            bool: False when either status is unknown.

        Raises:
            None
        """
        return self.admin_status is IfAdminStatus.UP and self.oper_status is IfOperStatus.UP


@dataclass(frozen=True, slots=True)
class Stage1Result:
    """The outcome of Stage 1.

    Attributes:
        sampled_at: When the exchange started (UTC).
        sampled_monotonic: The same moment on the monotonic clock.
        sys_uptime_ticks: ``sysUpTime.0``, or ``None`` when not returned.
        statuses: One entry per interface checked, in plan order.
        stats: SNMP effort spent.
        error: The first failure, if any.
        quality: ``PARTIAL_RESPONSE`` when OIDs went unanswered,
            ``MISSING_FIELDS`` when values are missing, plus retry and
            request-size flags.
    """

    sampled_at: datetime
    sampled_monotonic: float
    sys_uptime_ticks: int | None
    statuses: tuple[InterfaceStatus, ...]
    stats: SnmpStats = field(default_factory=SnmpStats)
    error: SnmpError | None = None
    quality: Quality = Quality.NONE

    @property
    def answered(self) -> bool:
        """Whether the agent answered at all, which proves SNMP reachability (SNMP-013).

        Args:
            None

        Returns:
            bool: True when any response arrived.

        Raises:
            None
        """
        return self.stats.responses > 0

    def by_index(self) -> dict[int, InterfaceStatus]:
        """Index the statuses by ifIndex.

        Args:
            None

        Returns:
            dict[int, InterfaceStatus]: Status per interface checked.

        Raises:
            None
        """
        return {status.if_index: status for status in self.statuses}


def stage1_oids(plan: PollPlan) -> list[Oid]:
    """List Stage 1's exact OIDs: ``sysUpTime.0`` then each status pair.

    Args:
        plan: The poll plan.

    Returns:
        list[Oid]: The OIDs, in request order.

    Raises:
        None
    """
    oids = [SYS_UPTIME]
    for entry in plan.interfaces:
        if entry.check_status:
            oids += [instance(_ADMIN, entry.if_index), instance(_OPER, entry.if_index)]
    return oids


def _status[T: (IfAdminStatus, IfOperStatus)](
    got: Mapping[Oid, VarBind], oid: Oid, kind: type[T]
) -> T | None:
    """Read one status from the GET result.

    Args:
        got: The Stage 1 varbinds by OID.
        oid: The status OID.
        kind: The status enum.

    Returns:
        T | None: The status, or ``None`` when absent or undefined.

    Raises:
        None
    """
    vb = got.get(oid)
    value = None if vb is None or vb.kind is not ValueKind.INTEGER else vb.integer()
    return kind(value) if value in kind._value2member_map_ else None


async def run_stage1(session: SnmpSession, plan: PollPlan, clock: Clock) -> Stage1Result:
    """Run Stage 1 for one device.

    Args:
        session: The open session.
        plan: The poll plan.
        clock: Timestamps for the sample.

    Returns:
        Stage1Result: Uptime, statuses, effort, and quality.

    Raises:
        None
    """
    sampled_at, sampled_monotonic = clock.now_utc(), clock.monotonic()
    result = await session.get(stage1_oids(plan))
    got = result.by_oid()
    uptime_vb = got.get(SYS_UPTIME)
    uptime = None
    if uptime_vb is not None and uptime_vb.kind is ValueKind.TIMETICKS:
        uptime = uptime_vb.integer()
    statuses = []
    for entry in (e for e in plan.interfaces if e.check_status):
        admin = _status(got, instance(_ADMIN, entry.if_index), IfAdminStatus)
        oper = _status(got, instance(_OPER, entry.if_index), IfOperStatus)
        flag = Quality.MISSING_FIELDS if admin is None or oper is None else Quality.NONE
        statuses.append(InterfaceStatus(entry.if_index, admin, oper, flag))
    quality = result.stats.quality
    if not result.complete:
        quality |= Quality.PARTIAL_RESPONSE
    if uptime is None or any(s.quality for s in statuses):
        quality |= Quality.MISSING_FIELDS
    return Stage1Result(
        sampled_at, sampled_monotonic, uptime, tuple(statuses), result.stats, result.error, quality
    )
