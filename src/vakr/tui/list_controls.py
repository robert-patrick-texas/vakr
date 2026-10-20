"""Device-list controls: search, bucket filter, sort, and page, and the summary line (TUI-003).

The state behind the ``/``, ``f``, ``s``, ``]`` and ``[`` keys lives here,
apart from the screen, so it is plain Python that tests can drive directly
and the screen stays short. ``SearchBox`` is the search input: Enter
returns focus to the table, Escape clears and hides it.

Dependencies:
    textual: ``Input`` for the search box.
    vakr.query: The request and list view.

Safety:
    Pure state; reads nothing. Search text is cut to the 256-character
    request limit, never interpreted as a pattern.

Example:
    >>> c = ListControls()
    >>> c.next_filter(); c.request().buckets
    frozenset({<StatusBucket.ATTENTION: 'attention'>})
"""

from datetime import datetime, tzinfo
from typing import ClassVar

from rich.markup import escape
from textual.binding import Binding, BindingType
from textual.widgets import Input

from vakr.domain.enums import StatusBucket
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.query import DeviceListRequest, DeviceListView, DeviceSort
from vakr.query.listing import MAX_SEARCH_LENGTH
from vakr.query.text import local_time

FILTERS: tuple[frozenset[StatusBucket], ...] = (
    frozenset(),
    frozenset({StatusBucket.ATTENTION}),
    frozenset({StatusBucket.ACTIVE}),
    frozenset({StatusBucket.OFFLINE}),
)
"""Bucket filters ``f`` cycles through: all, then each bucket."""
SORTS = tuple(DeviceSort)


class ListControls:
    """Search, filter, sort, and page of the device list.

    Attributes:
        search: Current search text.
        filter_index: Index into ``FILTERS``.
        sort_index: Index into ``SORTS``.
        offset: First row of the current page.
    """

    def __init__(self) -> None:
        """Start with no search, all buckets, status order, first page.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.search = ""
        self.filter_index = 0
        self.sort_index = 0
        self.offset = 0

    def request(self) -> DeviceListRequest:
        """Return the request for the current controls.

        Args:
            None

        Returns:
            DeviceListRequest: Pages of E.19's maximum, 500; the table scrolls virtually.

        Raises:
            None
        """
        return DeviceListRequest(
            search=self.search[:MAX_SEARCH_LENGTH],
            buckets=FILTERS[self.filter_index],
            sort=SORTS[self.sort_index],
            offset=self.offset,
            limit=MAX_PAGE_LIMIT,
        )

    def set_search(self, text: str) -> None:
        """Change the search and go back to the first page.

        Args:
            text: New search text.

        Returns:
            None

        Raises:
            None
        """
        self.search, self.offset = text, 0

    def next_filter(self) -> None:
        """Cycle the bucket filter: all, attention, active, offline.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.filter_index, self.offset = (self.filter_index + 1) % len(FILTERS), 0

    def next_sort(self) -> None:
        """Cycle the sort: status, name, site, age.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.sort_index = (self.sort_index + 1) % len(SORTS)

    def page(self, step: int, total: int) -> bool:
        """Move one page forward or back, staying inside the list.

        Args:
            step: +1 for the next page, -1 for the previous one.
            total: Rows in the list.

        Returns:
            bool: True when the page changed.

        Raises:
            None
        """
        offset = self.offset + step * MAX_PAGE_LIMIT
        if 0 <= offset < max(total, 1) and offset != self.offset:
            self.offset = offset
            return True
        return False

    def summary(
        self,
        view: DeviceListView | None,
        error: str | None,
        loaded_at: datetime | None,
        zone: tzinfo,
    ) -> str:
        """Describe counts, filter, sort, and page in one line; say STALE after a failure.

        Args:
            view: The latest list, if any.
            error: Why the last refresh failed, if it did.
            loaded_at: When the rows shown were read.
            zone: Display timezone.

        Returns:
            str: Console markup.

        Raises:
            None
        """
        if error is not None:
            since = local_time(loaded_at, zone) if loaded_at else "never"
            return f"[bold yellow]STALE: {escape(error)}. Rows shown are from {since}.[/]"
        if view is None:
            return "loading…"
        c = view.counts
        names = "+".join(sorted(b.value for b in FILTERS[self.filter_index])) or "all"
        parts = [
            f"{c.attention} attention, {c.active} active, {c.offline} offline",
            f"filter {names}",
            f"sort {SORTS[self.sort_index].value}",
            f"{view.offset + 1 if view.rows else 0}-{view.offset + len(view.rows)} of {view.total}",
        ]
        if self.search:
            parts.append(f"search {escape(repr(self.search))}")
        if view.truncated:
            parts.append("[bold yellow]list truncated (E.29)[/]")
        return " · ".join(parts)


class SearchBox(Input):
    """The ``/`` search box: Enter keeps the search, Escape clears it."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "clear", "Clear search")]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Keep the search and return focus to the table.

        Args:
            event: The submitted search.

        Returns:
            None

        Raises:
            None
        """
        event.stop()
        self.screen.focus_next()

    def action_clear(self) -> None:
        """Clear and hide the box; the list shows every device again.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.value = ""
        self.display = False
        self.screen.focus_next()
