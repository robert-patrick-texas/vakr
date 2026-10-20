"""Table columns for the TUI and how they shrink in a narrow terminal (TUI-005, TUI-008, 13.4).

Each column has a priority. Priority 0 columns always show: for devices the
state, name, and observation age; for interfaces the name, admin/oper
state, in/out rate, and age, as 13.4 requires. Other columns are added in
priority order while they fit the terminal width, then shown in their
natural order, so a narrower terminal drops the least important columns
first and never wraps or garbles a row. Widths follow the current content,
capped per column, so short labels leave room for more columns.

Dependencies:
    rich.text: Styled cells.
    vakr.query: Row view models.

Safety:
    Pure layout; no I/O.

Example:
    >>> [c.key for c in fit(DEVICE_COLUMNS, [], 20)]
    ['state', 'device', 'age']
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rich.text import Text

from vakr.domain.enums import StatusColor
from vakr.query import DeviceRow, InterfaceRow, Reading

CELL_PADDING = 1
"""Blank cells on each side of a column (Textual ``DataTable.cell_padding``)."""

COLOR_STYLES = {
    StatusColor.RED: "bold red",
    StatusColor.AMBER: "bold yellow",
    StatusColor.GREEN: "green",
    StatusColor.GRAY: "dim",
}
"""Colour per status; the icon and label always accompany it (12.8)."""


@dataclass(frozen=True, slots=True)
class Column[R]:
    """One table column.

    Attributes:
        key: Stable column key.
        label: Heading.
        priority: 0 always shows; larger numbers are dropped first.
        max_width: Widest the column grows; longer text is cut with ``…``.
        cell: Builds the cell for a row.
    """

    key: str
    label: str
    priority: int
    max_width: int
    cell: Callable[[R], Text]


def reading_cell(reading: Reading) -> Text:
    """Render a reading; missing values are dim, stale ones italic.

    Args:
        reading: The reading.

    Returns:
        Text: The styled cell.

    Raises:
        None
    """
    if reading.value is None:
        return Text(reading.text, style="dim")
    return Text(reading.text, style="italic" if reading.stale else "")


def state_cell(row: DeviceRow) -> Text:
    """Render a device status: icon and label, coloured (never colour alone).

    Args:
        row: The device row.

    Returns:
        Text: Such as ``"✖ Down"`` in bold red.

    Raises:
        None
    """
    s = row.status
    return Text(f"{s.icon} {s.label}", style=COLOR_STYLES[s.color])


DEVICE_COLUMNS: tuple[Column[DeviceRow], ...] = (
    Column("state", "State", 0, 34, state_cell),
    Column("device", "Device", 0, 24, lambda r: Text(r.name, style="bold")),
    Column("site", "Site", 2, 16, lambda r: reading_cell(r.site)),
    Column("address", "Address", 2, 22, lambda r: Text(r.address)),
    Column("icmp", "ICMP", 1, 19, lambda r: reading_cell(r.icmp)),
    Column("loss", "Loss", 1, 14, lambda r: reading_cell(r.loss)),
    Column("rtt", "RTT", 3, 17, lambda r: reading_cell(r.latency)),
    Column("snmp", "SNMP", 1, 19, lambda r: reading_cell(r.snmp)),
    Column("uptime", "Uptime", 3, 28, lambda r: reading_cell(r.uptime)),
    Column("ifaces", "Ifs", 4, 9, lambda r: reading_cell(r.interfaces)),
    Column("poll", "Last poll", 4, 24, lambda r: reading_cell(r.last_poll)),
    Column("age", "Age", 0, 9, lambda r: reading_cell(r.age)),
    Column("collector", "Collector", 3, 12, lambda r: reading_cell(r.collector)),
)
"""Device-list columns in display order (13.2)."""

INTERFACE_COLUMNS: tuple[Column[InterfaceRow], ...] = (
    Column("name", "Interface", 0, 22, lambda r: Text(r.name, style="bold")),
    Column("flags", "Flags", 3, 18, lambda r: Text(",".join(r.markers), style="cyan")),
    Column("alias", "Alias", 2, 24, lambda r: Text(r.alias)),
    Column("state", "Adm/Oper", 0, 18, lambda r: reading_cell(r.state)),
    Column("in", "In", 0, 18, lambda r: reading_cell(r.in_rate)),
    Column("out", "Out", 0, 18, lambda r: reading_cell(r.out_rate)),
    Column("in_util", "In%", 1, 22, lambda r: reading_cell(r.in_util)),
    Column("out_util", "Out%", 1, 22, lambda r: reading_cell(r.out_util)),
    Column("in_pps", "In pps", 3, 18, lambda r: reading_cell(r.in_pps)),
    Column("out_pps", "Out pps", 3, 18, lambda r: reading_cell(r.out_pps)),
    Column("errors", "Err i/o", 2, 12, lambda r: reading_cell(r.errors)),
    Column("capacity", "Capacity", 2, 28, lambda r: reading_cell(r.capacity)),
    Column("age", "Age", 0, 17, lambda r: reading_cell(r.age)),
)
"""Interface-table columns in display order (TUI-005)."""


def natural_width[R](column: Column[R], rows: Sequence[R]) -> int:
    """Return the width a column needs for its heading and these rows, capped.

    Args:
        column: The column.
        rows: Rows currently shown.

    Returns:
        int: Characters, at most ``column.max_width``.

    Raises:
        None
    """
    widest = max([len(column.label), *(column.cell(r).cell_len for r in rows)])
    return min(widest, column.max_width)


def fit[R](columns: Sequence[Column[R]], rows: Sequence[R], width: int) -> list[Column[R]]:
    """Choose the columns that fit a terminal width, in display order.

    Args:
        columns: Every column, in display order.
        rows: Rows currently shown, for content widths.
        width: Terminal width in cells.

    Returns:
        list[Column[R]]: Priority-0 columns always; others by priority while
        they fit; ties go to the earlier column.

    Raises:
        None
    """
    need = {c.key: natural_width(c, rows) + 2 * CELL_PADDING for c in columns}
    chosen = {c.key for c in columns if c.priority == 0}
    used = sum(need[k] for k in chosen)
    for c in sorted((c for c in columns if c.priority), key=lambda c: c.priority):
        if used + need[c.key] <= width:
            chosen.add(c.key)
            used += need[c.key]
    return [c for c in columns if c.key in chosen]


def clipped(text: Text, width: int) -> Text:
    """Cut a cell to a column width, marking the cut with ``…``.

    Args:
        text: The cell.
        width: Column width.

    Returns:
        Text: The cell, at most ``width`` cells wide.

    Raises:
        None
    """
    if text.cell_len <= width:
        return text
    cut = text.copy()
    cut.truncate(width, overflow="ellipsis")
    return cut
