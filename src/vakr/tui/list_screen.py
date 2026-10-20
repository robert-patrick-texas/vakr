"""The default TUI screen: devices, Attention first, then Active, then Offline (TUI-002, 13.2).

Keys: ``/`` search, ``f`` cycle the bucket filter, ``s`` cycle the sort,
``r`` refresh now, ``Enter`` open the device, ``h`` health, ``?`` help,
``]``/``[`` next/previous page, ``q`` quit; arrows and PageUp/PageDown move.

Every refresh reads through ``StatusQueries`` in a worker thread
(``asyncio.to_thread``), so a slow disk never freezes the screen, and only
one refresh runs at a time (``exclusive``). If a refresh fails, the rows
already shown stay, and the summary says in bold that they are stale and
why; they are never presented as current (13.4).

Dependencies:
    textual: Screen, widgets, bindings.
    vakr.query: ``StatusQueries`` and the views (TUI-007: no SQL, no adapters).
    vakr.tui.*: Columns, table filling, controls, and the other screens.

Safety:
    Read-only: no key changes anything on the node.

Example:
    >>> [b.key for b in DeviceListScreen.BINDINGS][:3]
    ['slash', 'f', 's']
"""

import asyncio
from datetime import datetime
from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from vakr.domain.errors import StorageError
from vakr.query import DeviceListRequest, DeviceListView, StatusQueries
from vakr.query.labels import health_line
from vakr.query.text import display_zone, local_time
from vakr.tui.columns import DEVICE_COLUMNS
from vakr.tui.device_screen import DeviceScreen
from vakr.tui.list_controls import ListControls, SearchBox
from vakr.tui.modals import HealthScreen
from vakr.tui.tables import fill


class DeviceListScreen(Screen[None]):
    """Device list with search, filter, sort, paging, and periodic refresh."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("slash", "search", "Search"),
        Binding("f", "filter", "Filter"),
        Binding("s", "sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("h", "health", "Health"),
        Binding("right_square_bracket", "page(1)", "Next page", show=False),
        Binding("left_square_bracket", "page(-1)", "Prev page", show=False),
    ]

    def __init__(self, queries: StatusQueries, refresh_s: float, zone: str, title: str) -> None:
        """Prepare the screen; nothing is read until it mounts.

        Args:
            queries: The read service.
            refresh_s: Seconds between automatic refreshes.
            zone: Display timezone name (empty for the host's).
            title: Product and version for the header.

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self._queries = queries
        self._refresh_s = refresh_s
        self._zone = display_zone(zone)
        self._title = title
        self.controls = ListControls()
        self._view: DeviceListView | None = None
        self._error: str | None = None
        self._loaded_at: datetime | None = None

    def compose(self) -> ComposeResult:
        """Lay out header, search box, table, summary, and key hints.

        Args:
            None

        Returns:
            ComposeResult: The widgets.

        Raises:
            None
        """
        yield Static("vakr: loading…", id="header")
        yield SearchBox(placeholder="search name, address, site, tag, or status", id="search")
        yield DataTable(id="devices", cursor_type="row", zebra_stripes=True)
        yield Static("", id="summary")
        yield Footer()

    def on_mount(self) -> None:
        """Start the refresh timer and the first load.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.query_one("#search", SearchBox).display = False
        self.query_one("#devices", DataTable).focus()
        self.set_interval(self._refresh_s, self.action_refresh)
        self.action_refresh()

    def action_refresh(self) -> None:
        """Reload the list in the background; a newer request replaces a running one.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        summary = self.controls.summary(self._view, self._error, self._loaded_at, self._zone)
        self.query_one("#summary", Static).update(summary + " · refreshing…")
        self.run_worker(self._load(self.controls.request()), exclusive=True, group="devices")

    async def _load(self, request: DeviceListRequest) -> None:
        """Read the list off the event loop, then show it.

        Args:
            request: What to read.

        Returns:
            None

        Raises:
            None
        """
        try:
            view = await asyncio.to_thread(self._queries.device_list, request)
        except StorageError as exc:
            self._error = f"inventory unreadable: {exc}; run `vakr doctor`"
        else:
            self._view, self._error, self._loaded_at = view, None, view.generated_at
        self._show()

    def _show(self) -> None:
        """Render the latest view, or keep the old one and mark it stale.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        view = self._view
        if view is not None:
            table = self.query_one("#devices", DataTable)
            fill(table, DEVICE_COLUMNS, view.rows, lambda r: str(r.device_id), self.size.width)
            when = local_time(view.generated_at, self._zone)
            self.query_one("#header", Static).update(
                f"{self._title} · {when} · {escape(health_line(view.health))} · "
                f"refresh {self._refresh_s:g} s"
            )
        summary = self.controls.summary(view, self._error, self._loaded_at, self._zone)
        self.query_one("#summary", Static).update(summary)

    def on_resize(self, event: Resize) -> None:
        """Re-fit the columns to the new width (TUI-008).

        Args:
            event: The resize event.

        Returns:
            None

        Raises:
            None
        """
        del event
        self._show()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open the selected device (TUI-004).

        Args:
            event: The selection, keyed by device UUID.

        Returns:
            None

        Raises:
            None
        """
        if (key := event.row_key.value) is not None:
            self.app.push_screen(DeviceScreen(self._queries, key, self._refresh_s))

    def action_search(self) -> None:
        """Show and focus the search box.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        box = self.query_one("#search", SearchBox)
        box.display = True
        box.focus()

    def on_input_changed(self, event: SearchBox.Changed) -> None:
        """Apply the search as it is typed.

        Args:
            event: The new search text.

        Returns:
            None

        Raises:
            None
        """
        self.controls.set_search(event.value)
        self.action_refresh()

    def action_filter(self) -> None:
        """Cycle the bucket filter (``f``).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.controls.next_filter()
        self.action_refresh()

    def action_sort(self) -> None:
        """Cycle the sort (``s``).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.controls.next_sort()
        self.action_refresh()

    def action_page(self, step: int) -> None:
        """Move one page forward or back (``]`` and ``[``).

        Args:
            step: +1 or -1.

        Returns:
            None

        Raises:
            None
        """
        if self.controls.page(step, self._view.total if self._view else 0):
            self.action_refresh()

    def action_health(self) -> None:
        """Show collector and storage health (13.2 ``h``).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.app.push_screen(HealthScreen(self._view.health if self._view else None, self._error))
