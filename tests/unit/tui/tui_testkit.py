"""Helpers to drive ``VakrTui`` headless with Textual's pilot (TUI-T01..T04).

``drive`` runs an async script against the app in a terminal of a given
size and returns whatever the script returns. ``settle`` waits until the
background loads have finished and the screen has repainted, so tests read
what an operator would see. ``cells`` and ``column_labels`` read a table.

Dependencies:
    asyncio, textual; tests.unit.query.query_testkit.

Example:
    >>> TERMINAL
    (120, 30)
"""

import asyncio
from collections.abc import Awaitable, Callable

from textual.pilot import Pilot
from textual.widgets import DataTable, Static

from vakr.query import StatusQueries
from vakr.tui.app import VakrTui

TERMINAL = (120, 30)
"""The terminal size TUI-008 requires to be fully usable."""


def drive[T](
    queries: StatusQueries,
    script: Callable[[VakrTui, Pilot[None]], Awaitable[T]],
    size: tuple[int, int] = TERMINAL,
) -> T:
    """Run the app headless and apply ``script`` to it.

    Args:
        queries: The service the app reads.
        script: Async steps taking the app and the pilot.
        size: Terminal columns and rows.

    Returns:
        T: The script's result.

    Raises:
        None
    """

    async def main() -> T:
        """Start the app, settle, run the script.

        Args:
            None

        Returns:
            T: The script's result.

        Raises:
            None
        """
        app = VakrTui(queries, refresh_s=600, zone="UTC")
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            return await script(app, pilot)

    return asyncio.run(main())


async def settle(app: VakrTui, pilot: Pilot[None]) -> None:
    """Wait for background reads to finish and the screen to repaint.

    Args:
        app: The app.
        pilot: Its pilot.

    Returns:
        None

    Raises:
        None
    """
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


async def press(app: VakrTui, pilot: Pilot[None], *keys: str) -> None:
    """Press keys, then settle.

    Args:
        app: The app.
        pilot: Its pilot.
        *keys: Key names.

    Returns:
        None

    Raises:
        None
    """
    await pilot.press(*keys)
    await settle(app, pilot)


def cells(app: VakrTui, table_id: str) -> list[list[str]]:
    """Return a table's cell text, row by row.

    Args:
        app: The app.
        table_id: The table's widget ID, such as ``"devices"``.

    Returns:
        list[list[str]]: Plain text of every cell.

    Raises:
        None
    """
    table = app.screen.query_one(f"#{table_id}", DataTable)
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


def column_labels(app: VakrTui, table_id: str) -> list[str]:
    """Return a table's column headings.

    Args:
        app: The app.
        table_id: The table's widget ID.

    Returns:
        list[str]: Headings in display order.

    Raises:
        None
    """
    table = app.screen.query_one(f"#{table_id}", DataTable)
    return [str(c.label) for c in table.columns.values()]


def text_of(app: VakrTui, widget_id: str) -> str:
    """Return the plain text a ``Static`` shows.

    Args:
        app: The app.
        widget_id: The widget ID, such as ``"summary"``.

    Returns:
        str: Its text without markup.

    Raises:
        None
    """
    return str(app.screen.query_one(f"#{widget_id}", Static).render())
