"""Operator-language reports for ``vakr snmp discover`` and ``vakr snmp poll``.

The reports follow blueprint 18.3: they say whether the *agent* answered
(SNMP availability, not reachability), call ``sysUpTime`` the management
agent's uptime rather than the chassis's, show missing values as ``?``
instead of ``0``, and say when a failure is the collector's rather than
the device's.

Dependencies:
    vakr.discovery, vakr.collectors.snmp: The results being reported.

Safety:
    Results never contain a community, so nothing here can print one.

Example:
    >>> uptime_text(8_640_000)
    '1 d 00:00:00'
    >>> speed_text(None, 1000)
    '1 Gb/s'
"""

from collections.abc import Iterable, Sequence
from enum import IntEnum

from vakr.discovery.model import DiscoveredInterface, DiscoveryResult, SubtableStatus
from vakr.ports.snmp import SnmpStats

UNKNOWN = "?"
UPTIME_NOTE = (
    "management-agent uptime; an agent restart resets it, so it does not prove chassis uptime"
)
_SKIP_REASON = {"ipAddrTable": "not needed: ipAddressTable listed the IPv4 addresses"}


def uptime_text(ticks: int | None) -> str:
    """Render TimeTicks as days and hours.

    Args:
        ticks: Hundredths of a second, or ``None``.

    Returns:
        str: Such as ``11 d 16:00:00``, or ``?``.

    Raises:
        None
    """
    if ticks is None:
        return UNKNOWN
    seconds = ticks // 100
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    return f"{days} d {hours:02d}:{rest // 60:02d}:{rest % 60:02d}"


def speed_text(if_speed: int | None, if_high_speed: int | None) -> str:
    """Render an interface speed, preferring ``ifHighSpeed`` (RATE-008).

    Args:
        if_speed: ``ifSpeed`` in b/s.
        if_high_speed: ``ifHighSpeed`` in Mb/s.

    Returns:
        str: Such as ``10 Gb/s`` or ``100 kb/s``, or ``?``.

    Raises:
        None
    """
    if if_high_speed:
        mbps = if_high_speed
        return f"{mbps // 1000} Gb/s" if mbps % 1000 == 0 else f"{mbps} Mb/s"
    if if_speed and if_speed < 4_294_967_295:
        return f"{if_speed // 1000} kb/s" if if_speed < 1_000_000 else f"{if_speed // 10**6} Mb/s"
    return UNKNOWN


def effort_text(stats: SnmpStats) -> str:
    """Summarise SNMP effort.

    Args:
        stats: The counts.

    Returns:
        str: Requests, answers, timeouts, retries, and duration.

    Raises:
        None
    """
    text = (
        f"{stats.requests} requests, {stats.responses} answered, {stats.timeouts} timed out, "
        f"{stats.retries} retries, {stats.elapsed_s:.2f} s waiting"
    )
    if stats.size_reductions:
        text += f"; request size reduced {stats.size_reductions} time(s) after tooBig"
    return text


def table(rows: Sequence[Sequence[str]]) -> list[str]:
    """Align rows into columns.

    Args:
        rows: Header row first.

    Returns:
        list[str]: One line per row.

    Raises:
        None
    """
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows
    ]


def _value(value: object) -> str:
    """Render a possibly missing value; missing is ``?``, never ``0``.

    Args:
        value: The value or ``None``.

    Returns:
        str: The text.

    Raises:
        None
    """
    if value is None:
        return UNKNOWN
    return value.name.lower() if isinstance(value, IntEnum) else str(value)


def interface_rows(interfaces: Iterable[DiscoveredInterface]) -> list[str]:
    """Render discovered interfaces as a table.

    Args:
        interfaces: The interfaces.

    Returns:
        list[str]: Table lines.

    Raises:
        None
    """
    rows = [
        (
            "IFINDEX",
            "NAME",
            "TYPE",
            "ADMIN",
            "OPER",
            "SPEED",
            "COUNTERS",
            "DESCRIPTION",
            "ADDRESSES",
        )
    ]
    for i in interfaces:
        family = UNKNOWN if i.hc_octets is None else "HC 64-bit" if i.hc_octets else "32-bit"
        rows.append((
            str(i.if_index), _value(i.if_name), _value(i.if_type), _value(i.admin_status),
            _value(i.oper_status), speed_text(i.if_speed, i.if_high_speed), family,
            _value(i.if_alias), ", ".join(i.addresses) or "-",
        ))  # fmt: skip
    return table(rows)


def discovery_report(result: DiscoveryResult, credential: str) -> list[str]:
    """Render a discovery result.

    Args:
        result: The result.
        credential: The credential name used.

    Returns:
        list[str]: Report lines.

    Raises:
        None
    """
    s = result.system
    up = sum(i.up_up for i in result.interfaces)
    lines = [
        f"SNMP discovery of {result.target} with credential {credential!r} (SNMPv2c)",
        f"The agent answered: {effort_text(result.stats)}.",
        "",
        f"  sysName      {_value(s.sys_name)}",
        f"  sysDescr     {_value((s.sys_descr or '').splitlines()[0] if s.sys_descr else None)}",
        f"  sysObjectID  {_value(s.sys_object_id)}",
        f"  sysLocation  {_value(s.sys_location)}",
        f"  sysContact   {_value(s.sys_contact)}",
        f"  sysUpTime    {uptime_text(s.sys_uptime_ticks)} ({UPTIME_NOTE})",
        "",
    ]
    for t in result.subtables:
        detail = (
            _SKIP_REASON.get(t.name, "")
            if t.status is SubtableStatus.SKIPPED
            else f"{t.rows} values"
        )
        if t.error is not None:
            detail += f"; stopped early: {t.error.message}"
        lines.append(f"  {t.name:<15} {t.status.value:<9} {detail}")
    lines += ["", f"{len(result.interfaces)} interfaces discovered, {up} admin up and oper up:"]
    lines += interface_rows(result.interfaces)
    return lines + discovery_notes(result)


def discovery_notes(result: DiscoveryResult) -> list[str]:
    """Explain incomplete data in operator language.

    Args:
        result: The result.

    Returns:
        list[str]: Note lines; empty when everything arrived.

    Raises:
        None
    """
    notes = []
    if not result.complete:
        notes.append("Some data did not arrive. Values shown as ? are missing, not zero.")
    if not result.addresses_complete:
        notes.append("Interface addresses are incomplete because no IP-MIB table answered fully.")
    missing = [i for i in result.interfaces if i.missing]
    if missing:
        names = ", ".join(f"{i.if_index} ({', '.join(i.missing)})" for i in missing[:5])
        notes.append(f"Interfaces with missing fields: {names}.")
    notes.append(
        "ifIndex is the number the device uses today. vakr tracks interfaces by its own "
        "identity, so a reload that renumbers them does not split their history."
    )
    return ["", *notes]
