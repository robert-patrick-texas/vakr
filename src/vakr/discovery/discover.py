"""Discover one device over SNMP: system group, interfaces, and addresses (9.4).

``discover`` reads, in order:

1. the system group with one GET (DISC-004);
2. the ifTable and ifXTable columns discovery needs, one column walk each,
   never the counters (SNMP-004, DISC-005);
3. ``ipAddressTable`` for IPv4 and IPv6 addresses, falling back to the
   deprecated ``ipAddrTable`` for IPv4 when ``ipAddressTable`` gave none
   (DISC-006).

One failed table never erases another's data (DISC-008): each column walk
keeps the rows it received and each table records its own completeness.
After ``abandon_after`` consecutive column walks of one table time out
without a single row, one GET of ``sysUpTime.0`` asks whether the agent is
still there. If it answers, only the rest of that table is skipped: some
agents hang on one MIB, and the next table may still answer. If it does not,
every remaining walk is skipped, so a device that stopped answering costs a
few timeouts rather than one per column.

Dependencies:
    vakr.discovery: The catalog, parsing, and result model.
    vakr.ports.clock, vakr.ports.snmp: Time and the SNMP session.

Safety:
    Read-only GET and GETBULK requests, bounded by the session's budget.

Example:
    >>> table_status(rows=0, failed=False).value
    'absent'
"""

from collections.abc import Mapping

from vakr.discovery import mib
from vakr.discovery.interfaces import build_interfaces
from vakr.discovery.model import DiscoveryResult, SubtableResult, SubtableStatus, SystemFacts
from vakr.discovery.parse import legacy_addresses, modern_addresses
from vakr.domain.quality import Quality
from vakr.ports.clock import Clock
from vakr.ports.snmp import (
    Oid,
    SnmpError,
    SnmpErrorKind,
    SnmpSession,
    VarBind,
    WalkResult,
    format_oid,
)
from vakr.ports.snmp.settings import DEFAULT_ABANDON_AFTER

_SYS_TEXT = ("sysDescr", "sysContact", "sysName", "sysLocation")
_ABANDONED = SnmpError(
    SnmpErrorKind.ABANDONED, "not walked: the agent stopped answering earlier in this discovery"
)
_SKIPPED = SnmpError(
    SnmpErrorKind.ABANDONED, "not walked: the agent answers, but not for this table"
)


def table_status(rows: int, failed: bool) -> SubtableStatus:
    """Classify a table from its row count and whether any column failed.

    Args:
        rows: Varbinds received.
        failed: Whether any column walk stopped early.

    Returns:
        SubtableStatus: ``COMPLETE``, ``ABSENT``, ``PARTIAL``, or ``FAILED``.

    Raises:
        None
    """
    if not failed:
        return SubtableStatus.COMPLETE if rows else SubtableStatus.ABSENT
    return SubtableStatus.PARTIAL if rows else SubtableStatus.FAILED


class _Walker:
    """Walks columns for one discovery, skipping silent tables and dead agents.

    Attributes:
        walks: Every column walk so far, by column name.
        dead: Whether the agent stopped answering altogether.
    """

    def __init__(self, session: SnmpSession, abandon_after: int, dead: bool) -> None:
        """Bind to the session.

        Args:
            session: The open session.
            abandon_after: Consecutive empty timeouts in one table before
                the agent's liveness is checked.
            dead: Start abandoned, when the agent ignored the system GET.

        Returns:
            None

        Raises:
            None
        """
        self._session = session
        self._abandon_after = abandon_after
        self.dead = dead
        self.walks: dict[str, WalkResult] = {}

    async def table(self, name: str, columns: Mapping[str, Oid]) -> SubtableResult:
        """Walk every column of one table and summarise its completeness.

        Args:
            name: Table name for the result.
            columns: Column name to OID.

        Returns:
            SubtableResult: The table's completeness.

        Raises:
            None
        """
        rows, failed, error, silent = 0, [], None, 0
        for column, oid in columns.items():
            if self.dead or silent >= self._abandon_after:
                walk = WalkResult(oid, (), False, _ABANDONED if self.dead else _SKIPPED)
            else:
                walk = await self._session.walk(oid)
                timed_out = walk.error is not None and walk.error.kind is SnmpErrorKind.TIMEOUT
                silent = silent + 1 if timed_out and not walk.varbinds else 0
                if silent >= self._abandon_after:
                    self.dead = not await self._alive()
            self.walks[column] = walk
            rows += len(walk.varbinds)
            if not walk.complete:
                failed.append(column)
                error = error or walk.error
        return SubtableResult(name, table_status(rows, bool(failed)), rows, tuple(failed), error)

    async def _alive(self) -> bool:
        """Ask whether the agent still answers anything.

        Args:
            None

        Returns:
            bool: True when ``sysUpTime.0`` got any answer.

        Raises:
            None
        """
        result = await self._session.get([mib.SYS_UPTIME])
        return result.stats.responses > 0


def _answer(got: Mapping[Oid, VarBind], name: str) -> VarBind | None:
    """Return a system scalar's varbind if the agent sent a value.

    Args:
        got: Varbinds by OID.
        name: Scalar name, such as ``sysName``.

    Returns:
        VarBind | None: The varbind, or ``None`` when absent.

    Raises:
        None
    """
    vb = got.get(mib.SYS_SCALARS[name])
    return vb if vb is not None and vb.present else None


async def _system(session: SnmpSession) -> tuple[SystemFacts, SubtableResult]:
    """Read the system group with one GET.

    Args:
        session: The open session.

    Returns:
        tuple[SystemFacts, SubtableResult]: The facts and their completeness.

    Raises:
        None
    """
    result = await session.get(list(mib.SYS_SCALARS.values()))
    got = result.by_oid()
    texts = {n: None if (vb := _answer(got, n)) is None else vb.text() for n in _SYS_TEXT}
    uptime_vb, oid_vb = _answer(got, "sysUpTime"), _answer(got, "sysObjectID")
    uptime = None if uptime_vb is None else uptime_vb.integer()
    object_id = None if oid_vb is None else oid_vb.object_id()
    object_text = None if object_id is None else format_oid(object_id)
    values = {**texts, "sysUpTime": uptime, "sysObjectID": object_text}
    missing = tuple(name for name in mib.SYS_SCALARS if values[name] is None)
    facts = SystemFacts(
        texts["sysDescr"], object_text, uptime,
        texts["sysContact"], texts["sysName"], texts["sysLocation"], missing,
    )  # fmt: skip
    present = len(mib.SYS_SCALARS) - len(missing)
    status = table_status(present, not result.complete)
    if status is SubtableStatus.COMPLETE and missing:
        status = SubtableStatus.PARTIAL
    return facts, SubtableResult("system", status, present, (), result.error)


async def discover(
    session: SnmpSession, clock: Clock, abandon_after: int = DEFAULT_ABANDON_AFTER
) -> DiscoveryResult:
    """Discover the device behind an open session.

    Args:
        session: The open session.
        clock: UTC time for the result and monotonic time for its duration.
        abandon_after: Consecutive empty walk timeouts before the remaining
            walks are skipped.

    Returns:
        DiscoveryResult: Facts, interfaces, and per-table completeness. A
        device that never answered gives a result whose ``answered`` is False.

    Raises:
        None
    """
    started_at, started, before = clock.now_utc(), clock.monotonic(), session.stats
    system, system_table = await _system(session)
    # An agent that ignored the system GET would cost a timeout per column.
    answered = (session.stats - before).responses > 0
    walker = _Walker(session, abandon_after, dead=not answered)
    if_columns = {n: mib.IF_COLUMNS[n] for n in mib.DISCOVERY_IF_COLUMNS}
    ifx_columns = {n: mib.IFX_COLUMNS[n] for n in mib.DISCOVERY_IFX_COLUMNS}
    tables = [system_table]
    tables.append(await walker.table("ifTable", if_columns))
    tables.append(await walker.table("ifXTable", ifx_columns))
    tables.append(await walker.table("ipAddressTable", mib.IP_ADDRESS_COLUMNS))
    addresses, unknown, saw_ipv4 = modern_addresses(walker.walks, mib.IP_ADDRESS_COLUMNS)
    if saw_ipv4:
        tables.append(SubtableResult("ipAddrTable", SubtableStatus.SKIPPED))
    else:
        tables.append(await walker.table("ipAddrTable", mib.IP_ADDR_COLUMNS))
        legacy, unknown_legacy = legacy_addresses(walker.walks, mib.IP_ADDR_COLUMNS)
        for if_index, found in legacy.items():
            addresses.setdefault(if_index, []).extend(found)
        unknown |= unknown_legacy
    interfaces = build_interfaces(walker.walks, addresses, unknown)
    stats = session.stats - before
    quality = stats.quality
    if not all(t.status.usable for t in tables):
        quality |= Quality.PARTIAL_RESPONSE
    if system.missing or any(i.missing for i in interfaces):
        quality |= Quality.MISSING_FIELDS
    return DiscoveryResult(
        session.target.label, started_at, clock.now_utc(), clock.monotonic() - started,
        system, interfaces, tuple(tables), stats, quality,
    )  # fmt: skip
