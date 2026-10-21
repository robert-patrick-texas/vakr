"""Text rendering for ``vakr status``: the device table and one device's detail (12.3, 12.4, 18.3).

Plain columns, no colour: the state column carries an icon and a label, so
the output reads the same in a log file, over ``ssh``, or piped to
``grep`` (12.8). Missing values print as "—"; the detail view prints the
reason beside each one, and the list footer says where to find reasons.

Dependencies:
    vakr.query: The views.

Safety:
    Pure string building; prints nothing itself.

Example:
    >>> table(["A", "BB"], [["x", "yyy"]], {})
    ['A  BB', 'x  yyy']
"""

from collections.abc import Sequence
from dataclasses import fields

from vakr.query import DeviceDetailView, DeviceListView, HealthView, InterfaceRow, Reading
from vakr.query.labels import health_line
from vakr.query.text import display_zone, local_time

LEGEND = "✖ down  ▲ degraded  ? unknown  ✔ ok  ○ ◐ offline/maintenance  — not available"
LIST_HEADERS = ["STATE", "DEVICE", "SITE", "ADDRESS", "ICMP", "LOSS", "RTT", "SNMP", "UPTIME"]
LIST_HEADERS += ["AGE", "COLLECTOR"]
IF_HEADERS = ["INTERFACE", "FLAGS", "ALIAS", "STATE", "IN", "OUT", "IN%", "OUT%", "IN PPS"]
IF_HEADERS += ["OUT PPS", "ERR I/O", "CAPACITY", "AGE"]
_MAX_WIDTH = {"STATE": 36, "ALIAS": 24, "CAPACITY": 28, "SITE": 20, "INTERFACE": 24}


def _clip(text: str, width: int | None) -> str:
    """Shorten text to a column width, marking the cut.

    Args:
        text: Cell text.
        width: Maximum width, or ``None`` for no limit.

    Returns:
        str: The text, ending in ``…`` when shortened.

    Raises:
        None
    """
    if width is None or len(text) <= width:
        return text
    return text[: width - 1] + "…"


def table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], widths: dict[str, int]
) -> list[str]:
    """Lay out a left-aligned text table with two spaces between columns.

    Args:
        headers: Column headings.
        rows: Cell text per row.
        widths: Maximum width per heading; others are unlimited.

    Returns:
        list[str]: The heading line, then one line per row, right-stripped.

    Raises:
        None
    """
    clipped = [[_clip(c, widths.get(h)) for h, c in zip(headers, row, strict=True)] for row in rows]
    size = [max([len(h)] + [len(r[i]) for r in clipped]) for i, h in enumerate(headers)]
    lines = [list(headers), *clipped]
    return ["  ".join(c.ljust(size[i]) for i, c in enumerate(line)).rstrip() for line in lines]


def _health(health: HealthView, source: str) -> list[str]:
    """Return the health line, then the data path when it is known (E.29 #10).

    Args:
        health: Storage and collector health.
        source: Where the view came from, such as ``"vakr-api socket"``; empty to omit.

    Returns:
        list[str]: One or two lines.

    Raises:
        None
    """
    return [health_line(health), *([f"data: {source}"] if source else [])]


def render_list(view: DeviceListView, zone_name: str, source: str = "") -> str:
    """Render the device list.

    Args:
        view: The device list.
        zone_name: Display timezone name (``time.timezone``; empty is the host's).
        source: The data path, shown under the health line; empty to omit.

    Returns:
        str: Summary line, health line, data path, table, and footer.

    Raises:
        None
    """
    c = view.counts
    when = local_time(view.generated_at, display_zone(zone_name))
    head = f"vakr status — {when} — {c.attention} attention, {c.active} active, {c.offline} offline"
    rows = [
        [
            f"{r.status.icon} {r.status.label}",
            r.name,
            r.site.text,
            r.address,
            r.icmp.text,
            r.loss.text,
            r.latency.text,
            r.snmp.text,
            r.uptime.text,
            r.age.text,
            r.collector.text,
        ]
        for r in view.rows
    ]
    lines = [head, *_health(view.health, source), ""]
    lines += table(LIST_HEADERS, rows, _MAX_WIDTH) if rows else ["no devices match"]
    end = view.offset + len(view.rows)
    lines += ["", f"showing {view.offset + 1 if rows else 0} to {end} of {view.total}"]
    if view.truncated:
        lines.append("more devices exist than one list reads (Appendix E.29); narrow the search")
    lines.append(f"{LEGEND}; vakr status DEVICE shows reasons")
    return "\n".join(lines) + "\n"


def _fact(label: str, reading: Reading) -> str:
    """Format one labelled fact, with the reason when the value is missing.

    Args:
        label: Fact label.
        reading: Its reading.

    Returns:
        str: Such as ``"  Jitter              — (needs at least two replies)"``.

    Raises:
        None
    """
    reason = f" ({reading.reason})" if reading.reason else ""
    return f"  {label:<27} {reading.text}{reason}"


def render_detail(view: DeviceDetailView, source: str = "") -> str:
    """Render one device: summary facts, warnings, interfaces, and notes.

    Args:
        view: The device detail.
        source: The data path, shown under the health line; empty to omit.

    Returns:
        str: The text.

    Raises:
        None
    """
    r = view.row
    lines = [f"{r.name} — {r.status.icon} {r.status.label}", *_health(view.health, source), ""]
    lines += [_fact(label, reading) for label, reading in view.facts]
    if r.positions and len(r.positions) > 1:
        lines += ["", "Positions:"]
        lines += [_fact(p.position, Reading(f"{p.status.icon} {p.status.label}, {p.age.text}"))
                  for p in r.positions]  # fmt: skip
    if view.warnings:
        lines += ["", "Warnings:", *(f"  - {w}" for w in view.warnings)]
    rows = [
        [
            i.name,
            ",".join(i.markers) or "—",
            i.alias or "—",
            i.state.text,
            i.in_rate.text,
            i.out_rate.text,
            i.in_util.text,
            i.out_util.text,
            i.in_pps.text,
            i.out_pps.text,
            i.errors.text,
            i.capacity.text,
            i.age.text,
        ]
        for i in view.interfaces
    ]
    lines += ["", f"Interfaces ({len(view.interfaces)} of {view.interfaces_total}):"]
    lines += table(IF_HEADERS, rows, _MAX_WIDTH) if rows else ["  none in inventory"]
    reasons = sorted({x.reason for i in view.interfaces for x in _cells(i) if x.reason})
    if reasons:
        lines += ["", "Why some values are —:", *(f"  - {why}" for why in reasons)]
    lines += ["", *view.notes]
    return "\n".join(lines) + "\n"


def _cells(row: InterfaceRow) -> list[Reading]:
    """List the readings of a row.

    Args:
        row: An interface row.

    Returns:
        list[Reading]: Every ``Reading`` field.

    Raises:
        None
    """
    values = (getattr(row, f.name) for f in fields(row))
    return [v for v in values if isinstance(v, Reading)]
