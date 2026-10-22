"""``VakrTui``: the read-only Textual application behind ``vakr tui`` (TUI-001, ADR-013).

The app owns nothing but a ``StatusSource`` (``vakr-api`` over its socket,
or the local databases) and a refresh interval; screens do the reading,
always through the source and always in a worker thread. It opens on the
device list (TUI-002); ``Enter`` opens a device (TUI-004). There is no key
that changes anything: the TUI is for looking, and administration stays in
the CLI and the web UI.

Dependencies:
    textual: The application framework (ADR-013).
    vakr.query: Data access through ``StatusSource`` (TUI-007).
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

from vakr.query import StatusSource
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

    def __init__(
        self,
        source: StatusSource,
        *,
        refresh_s: float,
        zone: str = "",
        data: str = "",
        node: str = "",
    ) -> None:
        """Bind the app to its data source.

        Args:
            source: Where the views come from.
            refresh_s: Seconds between automatic refreshes (E.29 bounds).
            zone: Display timezone name; empty means the host's (TIME-002).
            data: The data path for the header, such as ``"vakr-api socket"``; empty to omit.
            node: This node's position and role (MPOS-003), such as ``"west (peer of ...)"``.

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self.source = source
        self.refresh_s = refresh_s
        self.zone = zone
        self.data = data
        self.node = node

    def on_mount(self) -> None:
        """Open the device list.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        title = f"vakr {get_version()}" + (f" · {self.node}" if self.node else "")
        title += f" · data: {self.data}" if self.data else ""
        self.push_screen(DeviceListScreen(self.source, self.refresh_s, self.zone, title))

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
