"""Operator-facing number formatting for the status CLI and TUI (18.3, 12.3).

Rates use SI prefixes on bits per second, as interface speeds are quoted
(``1 Gb/s`` is 10^9 b/s). Ages and uptimes are short and coarse, because an
operator scanning a list needs "3 min", not "187.4 s". Every formatter takes
a real number; missing values never reach them, so a missing rate can never
print as ``0 b/s`` (invariant 1). A measured zero still prints as zero.

Dependencies:
    datetime, zoneinfo: Display timezone (TIME-002, TIME-003).

Safety:
    Pure functions.

Example:
    >>> bits_per_second(1_234_000), age_text(187.4), percent(37.25)
    ('1.23 Mb/s', '3 min', '37.2%')
    >>> uptime_text(123_456_789)
    '14 d 6 h'
"""

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_PREFIXES = ("", "k", "M", "G", "T")


def _si(value: float, unit: str) -> str:
    """Format a non-negative quantity with an SI prefix and three significant digits.

    Args:
        value: The quantity.
        unit: Unit suffix, such as ``b/s``.

    Returns:
        str: For example ``"12.3 Mb/s"``; ``"0 b/s"`` for a measured zero.

    Raises:
        None
    """
    scaled, index = float(value), 0
    while abs(scaled) >= 999.5 and index < len(_PREFIXES) - 1:
        scaled, index = scaled / 1000, index + 1
    if index == 0:
        return f"{scaled:.0f} {unit}"
    digits = 2 if abs(scaled) < 10 else 1 if abs(scaled) < 100 else 0
    return f"{scaled:.{digits}f} {_PREFIXES[index]}{unit}"


def bits_per_second(bps: float) -> str:
    """Format a bit rate.

    Args:
        bps: Bits per second.

    Returns:
        str: Such as ``"950 b/s"`` or ``"10.0 Gb/s"``.

    Raises:
        None
    """
    return _si(bps, "b/s")


def packets_per_second(pps: float) -> str:
    """Format a packet rate.

    Args:
        pps: Packets per second.

    Returns:
        str: Such as ``"12.3 kpps"``.

    Raises:
        None
    """
    return _si(pps, "pps")


def percent(value: float) -> str:
    """Format a percentage with one decimal, rounded half to even.

    Args:
        value: Percent, such as ``37.25``.

    Returns:
        str: Such as ``"37.2%"``.

    Raises:
        None
    """
    return f"{round(value, 1):.1f}%"


def milliseconds(microseconds: int) -> str:
    """Format an RTT stored in microseconds as milliseconds.

    Args:
        microseconds: RTT in integer microseconds (ICMP-003).

    Returns:
        str: Such as ``"1.23 ms"``, ``"45.6 ms"``, or ``"812 ms"``.

    Raises:
        None
    """
    ms = microseconds / 1000
    digits = 2 if ms < 10 else 1 if ms < 100 else 0
    return f"{ms:.{digits}f} ms"


def age_text(seconds: float) -> str:
    """Format an observation age coarsely.

    A negative age means the observation is stamped after ``now``: the
    collector's clock is ahead. It is shown, not hidden (E.17 #14).

    Args:
        seconds: Age in seconds.

    Returns:
        str: ``"42 s"``, ``"3 min"``, ``"2 h 5 min"``, ``"3 d 4 h"``, or
        ``"5 s ahead"`` for a future timestamp.

    Raises:
        None
    """
    if seconds < 0:
        return f"{age_text(-seconds)} ahead"
    s = int(seconds)
    if s < 120:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86_400:
        hours, minutes = divmod(s // 60, 60)
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(s // 3600, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def uptime_text(ticks: int) -> str:
    """Format ``sysUpTime`` (hundredths of a second) as days and hours.

    Args:
        ticks: ``sysUpTime`` ticks.

    Returns:
        str: Such as ``"14 d 6 h"`` or ``"42 s"``.

    Raises:
        None
    """
    return age_text(ticks / 100)


def interval_text(seconds: float) -> str:
    """Format a poll interval.

    Args:
        seconds: Interval in seconds.

    Returns:
        str: ``"60 s"``, ``"5 min"``, or ``"1 h"``.

    Raises:
        None
    """
    return age_text(seconds)


def display_zone(name: str) -> tzinfo:
    """Return the display timezone: the configured IANA zone, else the host's (TIME-002/003).

    Args:
        name: ``time.timezone``; empty means the host timezone.

    Returns:
        tzinfo: The zone; UTC if the host zone cannot be determined.

    Raises:
        None
    """
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            return UTC
    return datetime.now(UTC).astimezone().tzinfo or UTC


def local_time(when: datetime, zone: tzinfo) -> str:
    """Format an aware time in the display timezone.

    Args:
        when: Aware time (stored as UTC).
        zone: Display timezone.

    Returns:
        str: Such as ``"2026-10-20 14:03:22 CDT"``.

    Raises:
        None
    """
    return when.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def utc_iso(when: datetime | None) -> str | None:
    """Format an aware time as ISO 8601 UTC with ``Z`` (TIME-004).

    Args:
        when: Aware time, or ``None``.

    Returns:
        str | None: Such as ``"2026-10-20T19:03:22Z"``; ``None`` for ``None``.

    Raises:
        None
    """
    if when is None:
        return None
    return when.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
