"""The device screen: a compact summary, then the interface table (TUI-004..006, 13.3).

The summary fits in a few lines so the interface table keeps most of a
120x30 terminal: status, address, site, tags, and collector; ICMP probes,
latency, and jitter; SNMP, ``sysUpTime``, last poll, and age; then quality
warnings. A missing value is shown as "—" with its reason. Interfaces come
in TUI-006 order: sticky, WAN circuits, and primary interfaces first.

Keys: ``Escape`` back to the list, ``r`` refresh, ``?`` help, ``q`` quit.

Dependencies:
    textual: Screen and widgets.
    vakr.query: ``StatusQueries`` and the detail view (TUI-007).

Safety:
    Read-only. Reads run in a worker thread, never on the event loop.

Example:
    >>> from vakr.query import Reading
    >>> say(Reading("—", None, "needs at least two replies"))
    '— (needs at least two replies)'
"""

import asyncio
from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from vakr.domain.errors import StorageError
from vakr.query import DeviceDetailView, Reading, StatusQueries
from vakr.query.labels import health_line
from vakr.tui.columns import COLOR_STYLES, INTERFACE_COLUMNS
from vakr.tui.tables import fill


def say(reading: Reading) -> str:
    """Return a reading's text, with the reason when the value is missing.

    Args:
        reading: The reading.

    Returns:
        str: ``"12 s"`` or ``"— (first sample)"``.

    Raises:
        None
    """
    if reading.value is None and reading.reason:
        return f"{reading.text} ({reading.reason})"
    return reading.text


def summary_lines(view: DeviceDetailView) -> list[str]:
    """Build the compact device summary (TUI-004).

    Args:
        view: The device detail.

    Returns:
        list[str]: Console-markup lines, values escaped.

    Raises:
        None
    """
    f = {label: say(r) for label, r in view.facts}
    r, s = view.row, view.row.status
    e = escape
    snmp_requests = dict(view.facts)["SNMP requests"]
    requests = f" · {e(f['SNMP requests'])}" if snmp_requests.value is not None else ""
    lines = [
        f"[{COLOR_STYLES[s.color]}]{e(s.icon)} {e(s.label)}[/]  [b]{e(r.name)}[/]  "
        f"{e(f['Poll address'])} · site {e(f['Site'])} · tags {e(f['Tags'])} · "
        f"collector {e(f['Collector'])} · admin {e(f['Admin state'])}",
        f"ICMP {e(f['ICMP'])} · probes {e(f['Probes'])} · latency {e(f['Latency'])} · "
        f"jitter {e(f['Jitter'])}",
        f"SNMP {e(f['SNMP'])}{requests} · sysUpTime {e(f['sysUpTime'])}",
        f"last poll {e(f['Last poll'])} · age {e(f['Observation age'])} ({e(f['Freshness'])}) "
        f"· interval {e(f['Poll interval'])} · {e(f['Reachability strategy'])}",
    ]
    if len(r.positions) > 1:
        each = (
            f"{p.position}: {p.status.icon} {p.status.label} ({p.age.text})" for p in r.positions
        )
        lines.append("positions " + e(" · ".join(each)))
    if view.warnings:
        lines.append("[yellow]warnings: " + e("; ".join(view.warnings)) + "[/]")
    lines.append(f"[dim]{e(health_line(view.health))}[/]")
    return lines


class DeviceScreen(Screen[None]):
    """One device's summary and interface table."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, queries: StatusQueries, key: str, refresh_s: float) -> None:
        """Prepare the screen for one device.

        Args:
            queries: The read service.
            key: Device UUID (or exact name).
            refresh_s: Seconds between automatic refreshes.

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self._queries = queries
        self._key = key
        self._refresh_s = refresh_s
        self._view: DeviceDetailView | None = None

    def compose(self) -> ComposeResult:
        """Lay out the summary, the interface table, and key hints.

        Args:
            None

        Returns:
            ComposeResult: The widgets.

        Raises:
            None
        """
        yield Static("loading…", id="device-summary")
        yield DataTable(id="interfaces", cursor_type="row", zebra_stripes=True)
        yield Static("", id="device-footer")
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
        self.query_one("#interfaces", DataTable).focus()
        self.set_interval(self._refresh_s, self.action_refresh)
        self.action_refresh()

    def action_refresh(self) -> None:
        """Reload the device in the background.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.run_worker(self._load(), exclusive=True, group="device")

    async def _load(self) -> None:
        """Read the device off the event loop, then show it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        footer = self.query_one("#device-footer", Static)
        try:
            view = await asyncio.to_thread(self._queries.device_detail, self._key)
        except StorageError as exc:
            footer.update(f"[bold yellow]STALE: inventory unreadable: {escape(str(exc))}[/]")
            return
        if view is None:
            footer.update("[bold yellow]this device is no longer in inventory[/]")
            return
        self._view = view
        self._show()

    def _show(self) -> None:
        """Render the latest view.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        v = self._view
        if v is None:
            return
        self.query_one("#device-summary", Static).update("\n".join(summary_lines(v)))
        table = self.query_one("#interfaces", DataTable)
        fill(table, INTERFACE_COLUMNS, v.interfaces, lambda r: str(r.interface_id), self.size.width)
        more = " (more exist; Appendix E.29 bound)" if v.interfaces_truncated else ""
        self.query_one("#device-footer", Static).update(
            f"{len(v.interfaces)} of {v.interfaces_total} interfaces{more} · "
            "sticky, WAN, and primary first · — means not available, never zero"
        )

    def on_resize(self, event: Resize) -> None:
        """Re-fit the interface columns to the new width (TUI-008).

        Args:
            event: The resize event.

        Returns:
            None

        Raises:
            None
        """
        del event
        self._show()
