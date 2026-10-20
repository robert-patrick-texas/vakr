"""``VakrTui``: the read-only Textual application behind ``vakr tui`` (TUI-001, ADR-013).

The app owns nothing but a ``StatusQueries`` service and a refresh
interval; screens do the reading, always through the service and always in
a worker thread. It opens on the device list (TUI-002); ``Enter`` opens a
device (TUI-004). There is no key that changes anything: the TUI is for
looking, and administration stays in the CLI and, later, the web UI.

Dependencies:
    textual: The application framework (ADR-013).
    vakr.query: Data access (TUI-007).
    vakr.tui.list_screen: The first screen.

Safety:
    Read-only. Never reads credential files (13.1) and never shows
    credential references (TUI-010): the views carry none.

Example:
    >>> VakrTui.TITLE
    'vakr'
"""

from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from vakr.query import StatusQueries
from vakr.tui.list_screen import DeviceListScreen
from vakr.tui.modals import HelpScreen
from vakr.version import get_version

CSS = """
Screen { layout: vertical; }
#header { height: auto; max-height: 2; background: $boost; padding: 0 1; }
#search { height: 3; }
#devices, #interfaces { height: 1fr; }
#summary, #device-footer { height: 1; padding: 0 1; }
#device-summary { height: auto; max-height: 9; padding: 0 1; }
ModalScreen { align: center middle; }
.overlay {
    width: 90; max-width: 100%; height: auto; max-height: 100%;
    border: round $accent; background: $panel; padding: 0 1;
}
"""


class VakrTui(App[None]):
    """The ``vakr tui`` application."""

    TITLE = "vakr"
    CSS = CSS
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, queries: StatusQueries, *, refresh_s: float, zone: str = "") -> None:
        """Bind the app to its query service.

        Args:
            queries: The read service.
            refresh_s: Seconds between automatic refreshes (E.29 bounds).
            zone: Display timezone name; empty means the host's (TIME-002).

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self.queries = queries
        self.refresh_s = refresh_s
        self.zone = zone

    def on_mount(self) -> None:
        """Open the device list.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        title = f"vakr {get_version()}"
        self.push_screen(DeviceListScreen(self.queries, self.refresh_s, self.zone, title))

    def action_help(self) -> None:
        """Show keys and what the states mean (13.2 ``?``).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())
