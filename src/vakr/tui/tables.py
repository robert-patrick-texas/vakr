"""Fill a Textual ``DataTable`` from view rows, keeping the operator's place (TUI-003, 12.8).

A refresh rebuilds the table, because the column set may change with the
data or the terminal width. The row under the cursor is remembered by its
stable key (a UUID, never a position) and selected again afterwards, so an
auto-refresh never moves the operator's focus to another device (12.8:
"auto-refresh preserves keyboard focus").

Dependencies:
    textual.widgets: ``DataTable``.
    vakr.tui.columns: Column choice and cell rendering.

Safety:
    Display only.

Example:
    >>> SCROLLBAR_WIDTH
    2
"""

from collections.abc import Callable, Sequence

from textual.widgets import DataTable

from vakr.tui.columns import Column, clipped, fit, natural_width

SCROLLBAR_WIDTH = 2
"""Cells a vertical scrollbar takes from the table."""


def selected_key(table: DataTable) -> str | None:
    """Return the key of the row under the cursor.

    Args:
        table: The table.

    Returns:
        str | None: The row key, or ``None`` for an empty table.

    Raises:
        None
    """
    if not table.row_count or not table.is_valid_row_index(table.cursor_row):
        return None
    key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
    return key.value


def fill[R](
    table: DataTable,
    columns: Sequence[Column[R]],
    rows: Sequence[R],
    key: Callable[[R], str],
    width: int,
) -> list[Column[R]]:
    """Replace a table's columns and rows, keeping the cursor on the same row key.

    Args:
        table: The table.
        columns: Every possible column, in display order.
        rows: Rows to show, in order.
        key: Stable key of a row.
        width: Width available to the table, in cells.

    Returns:
        list[Column[R]]: The columns shown.

    Raises:
        None
    """
    keep = selected_key(table)
    shown = fit(columns, rows, width - SCROLLBAR_WIDTH)
    widths = {c.key: natural_width(c, rows) for c in shown}
    table.clear(columns=True)
    for c in shown:
        table.add_column(c.label, width=widths[c.key], key=c.key)
    for r in rows:
        table.add_row(*(clipped(c.cell(r), widths[c.key]) for c in shown), key=key(r))
    if keep is not None:
        keys = [key(r) for r in rows]
        if keep in keys:
            table.move_cursor(row=keys.index(keep), animate=False)
    return shown
