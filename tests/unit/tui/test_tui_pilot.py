"""``vakr tui`` driven headless: list, device screen, narrow terminals, outages (TUI-T01..T04).

Each test starts the real ``VakrTui`` over memory stores filled through the
ports, presses keys with Textual's pilot, and reads the tables and status
lines an operator would see.

Dependencies:
    pytest, textual; tests.unit.query.query_testkit, tests.unit.tui.tui_testkit.

Example:
    $ uv run pytest tests/unit/tui/test_tui_pilot.py
"""

from textual.pilot import Pilot

from tests.unit.query.query_testkit import Fleet, mixed_fleet
from tests.unit.tui.tui_testkit import cells, column_labels, drive, press, settle, text_of
from vakr.domain.enums import IcmpState
from vakr.domain.errors import StorageError
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.quality import Quality
from vakr.ports.metadata import MetadataUnitOfWork
from vakr.query import DeviceDetailView, StatusQueries
from vakr.tui.app import VakrTui
from vakr.tui.device_screen import DeviceScreen
from vakr.tui.list_screen import DeviceListScreen
from vakr.tui.modals import HealthScreen, HelpScreen


def names(app: VakrTui) -> list[str]:
    """Return the device names in the list, in order.

    Args:
        app: The app on the list screen.

    Returns:
        list[str]: Names.

    Raises:
        None
    """
    labels = column_labels(app, "devices")
    at = labels.index("Device")
    return [row[at] for row in cells(app, "devices")]


def test_default_screen_orders_buckets_with_text_and_icons() -> None:
    """TUI-T01, TUI-002: Attention, then Active, then Offline; icons and labels, not colour only.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def script(app: VakrTui, pilot: Pilot[None]) -> tuple[list[str], list[str], str, str]:
        """Read the list.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            tuple: Names, state cells, summary, header.

        Raises:
            None
        """
        del pilot
        states = [row[0] for row in cells(app, "devices")]
        return names(app), states, text_of(app, "summary"), text_of(app, "header")

    order, states, summary, header = drive(mixed_fleet().queries(), script)
    assert order == ["core", "old", "edge", "sw2", "sw10", "lab"]
    assert states[0] == "✖ Down"
    assert states[1] == "? Unknown — no fresh observation"
    assert states[-1] == "○ Disabled"
    assert summary.startswith("3 attention, 2 active, 1 offline · filter all · sort status")
    assert "telemetry ok" in header
    assert "refresh 600 s" in header


def test_search_filter_sort_and_refresh() -> None:
    """TUI-T01, TUI-003: ``/`` search, Escape clears, ``f`` filter, ``s`` sort, ``r`` refresh.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()

    async def script(app: VakrTui, pilot: Pilot[None]) -> list[list[str]]:
        """Press keys and record the names after each step.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            list[list[str]]: Names after search, clear, filter, sort, refresh.

        Raises:
            None
        """
        seen = []
        await press(app, pilot, "slash", "s", "w")
        seen.append(names(app))
        await press(app, pilot, "escape")
        seen.append(names(app))
        await press(app, pilot, "f")
        seen.append(names(app))
        await press(app, pilot, "f", "f", "f", "s")
        seen.append(names(app))
        fleet.poll(fleet.device("new1"))
        await press(app, pilot, "r")
        seen.append(names(app))
        return seen

    search, cleared, attention, by_name, refreshed = drive(fleet.queries(), script)
    assert search == ["sw2", "sw10"]
    assert len(cleared) == 6
    assert attention == ["core", "old", "edge"]
    assert by_name == ["core", "edge", "lab", "old", "sw2", "sw10"]
    assert "new1" in refreshed


def test_enter_opens_the_device_screen_with_sticky_interfaces_first() -> None:
    """TUI-T02, TUI-004..006: summary facts and the interface table; Escape goes back.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    fleet.interface(d, "Gi1/0/2", if_high_speed=1000)
    fleet.interface(d, "Gi1/0/10", annotation=InterfaceAnnotation(sticky=True))
    fleet.poll(d)

    async def script(app: VakrTui, pilot: Pilot[None]) -> tuple[str, list[list[str]], str, str]:
        """Open the device, read it, and go back.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            tuple: Summary, interface cells, screen class names before and after Escape.

        Raises:
            None
        """
        await press(app, pilot, "enter")
        opened = type(app.screen).__name__
        summary, rows = text_of(app, "device-summary"), cells(app, "interfaces")
        await press(app, pilot, "escape")
        return summary, rows, opened, type(app.screen).__name__

    summary, rows, opened, back = drive(fleet.queries(), script)
    assert (opened, back) == (DeviceScreen.__name__, DeviceListScreen.__name__)
    assert "✔ OK  sw1" in summary
    for fact in ("ICMP reachable", "probes 5/5 answered", "SNMP responding", "sysUpTime 41 d 16 h"):
        assert fact in summary
    assert "last poll 30 s ago, complete" in summary
    assert [r[0] for r in rows] == ["Gi1/0/10", "Gi1/0/2"]
    assert rows[1][-1] == "—"  # no sample: a dash, never zero


def test_120x30_is_usable_and_narrow_terminals_drop_low_priority_columns() -> None:
    """TUI-T03, TUI-008, 13.4: state, name, and age always stay; rate and age on interfaces.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    d = fleet.device("sw9")
    fleet.sample(fleet.interface(d, "Gi1/0/1", if_alias="uplink", if_high_speed=1000))

    async def script(app: VakrTui, pilot: Pilot[None]) -> tuple[list[str], ...]:
        """Read the columns at 120x30, then 60x20, then the device screen at 60x20.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            tuple: Column labels at each step.

        Raises:
            None
        """
        wide = column_labels(app, "devices")
        await pilot.resize_terminal(60, 20)
        await settle(app, pilot)
        narrow = column_labels(app, "devices")
        await press(app, pilot, "slash", "s", "w", "9", "enter", "enter")
        return wide, narrow, column_labels(app, "interfaces")

    wide, narrow, interfaces = drive(fleet.queries(), script)
    assert {"State", "Device", "Site", "ICMP", "Loss", "SNMP", "Age"} <= set(wide)
    assert narrow[0:2] == ["State", "Device"]
    assert "Age" in narrow
    assert len(narrow) < len(wide)
    assert {"Interface", "Adm/Oper", "In", "Out", "Age"} <= set(interfaces)
    assert len(interfaces) < 13


def test_telemetry_outage_is_explicit_and_never_down() -> None:
    """TUI-T04, 13.4: metadata stays visible; every enabled device is Unknown with the reason.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def script(app: VakrTui, pilot: Pilot[None]) -> tuple[list[list[str]], str]:
        """Read the list and header.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            tuple: Cells and header.

        Raises:
            None
        """
        del pilot
        return cells(app, "devices"), text_of(app, "header")

    rows, header = drive(mixed_fleet().queries(telemetry=False), script)
    states = {r[0] for r in rows}
    assert states == {"? Unknown — telemetry unavailable", "○ Disabled"}
    assert "telemetry unavailable: no telemetry database yet" in header


def test_failed_refresh_keeps_rows_but_labels_them_stale() -> None:
    """TUI-T04, 13.4: cached state is never presented as current.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    broken = {"now": False}

    def metadata() -> MetadataUnitOfWork:
        """Open a unit of work, or fail like an unreadable database.

        Args:
            None

        Returns:
            MetadataUnitOfWork: A memory unit of work.

        Raises:
            StorageError: Once ``broken["now"]`` is set.
        """
        if broken["now"]:
            raise StorageError("database metadata.db is not readable", retryable=False)
        return fleet.uow()

    queries = StatusQueries(metadata, fleet.telemetry, fleet.queries()._settings, fleet.clock)

    async def script(app: VakrTui, pilot: Pilot[None]) -> tuple[int, str]:
        """Break the store and refresh.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            tuple: Rows still shown and the summary.

        Raises:
            None
        """
        broken["now"] = True
        await press(app, pilot, "r")
        return len(cells(app, "devices")), text_of(app, "summary")

    count, summary = drive(queries, script)
    assert count == 6
    assert summary.startswith("STALE: inventory unreadable: database metadata.db is not readable")
    assert "Rows shown are from 2026-10-20 12:00:00 UTC" in summary


def test_help_health_and_paging_keys() -> None:
    """13.2: ``?`` help, ``h`` health, ``]``/``[`` paging within bounds, ``q`` quits.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    fleet.health(spool_used_pct=1.5)

    async def script(app: VakrTui, pilot: Pilot[None]) -> list[str]:
        """Open and close the overlays; try to page past the end.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            list[str]: Screen class names and texts seen.

        Raises:
            None
        """
        seen = []
        await press(app, pilot, "question_mark")
        seen += [type(app.screen).__name__, text_of(app, "help-text")]
        await press(app, pilot, "escape", "h")
        seen += [type(app.screen).__name__, text_of(app, "health-text")]
        await press(app, pilot, "escape", "right_square_bracket", "left_square_bracket")
        seen.append(text_of(app, "summary"))
        await press(app, pilot, "q")
        return seen

    help_name, help_text, health_name, health_text, summary = drive(fleet.queries(), script)
    assert (help_name, health_name) == (HelpScreen.__name__, HealthScreen.__name__)
    assert "Unknown is not Down" in help_text
    assert "collector east" in health_text
    assert "spool 1.5%" in health_text
    assert "1-6 of 6" in summary


class Flaky(StatusQueries):
    """A service whose device detail can fail or vanish on demand.

    Attributes:
        mode: ``"ok"``, ``"broken"`` (storage error), or ``"gone"`` (device deleted).
    """

    mode = "ok"

    def device_detail(self, key: str) -> DeviceDetailView | None:
        """Return the detail, raise, or return nothing, per ``mode``.

        Args:
            key: Device name or UUID.

        Returns:
            DeviceDetailView | None: The view, or ``None`` when "gone".

        Raises:
            StorageError: When "broken".
        """
        if self.mode == "broken":
            raise StorageError("database metadata.db is locked", retryable=True)
        return None if self.mode == "gone" else super().device_detail(key)


def test_device_screen_positions_warnings_and_failures() -> None:
    """TUI-T02, TUI-T04: per-position verdicts, quality warnings, and refresh failures.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet(("east", "west"))
    d = fleet.device("sw1")
    fleet.poll(d, position="east", icmp=IcmpState.UNREACHABLE, snmp=None)
    fleet.poll(d, position="west", icmp_quality=Quality.PARTIAL_LOSS)
    queries = Flaky(fleet.uow, fleet.telemetry, fleet.queries()._settings, fleet.clock)

    async def script(app: VakrTui, pilot: Pilot[None]) -> list[str]:
        """Open the device, then make refreshes fail and the device vanish.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            list[str]: Summary, then the footer after each failure.

        Raises:
            None
        """
        await press(app, pilot, "enter")
        seen = [text_of(app, "device-summary")]
        queries.mode = "broken"
        await press(app, pilot, "r")
        seen.append(text_of(app, "device-footer"))
        queries.mode = "gone"
        await press(app, pilot, "r")
        seen.append(text_of(app, "device-footer"))
        return seen

    summary, broken, gone = drive(queries, script)
    assert "Reachable from west only" in summary
    assert "positions east: ✖ Down" in summary
    assert "west: ✔ OK" in summary
    assert "warnings: 1 of 5 probes lost" in summary
    assert broken.startswith("STALE: inventory unreadable: database metadata.db is locked")
    assert gone == "this device is no longer in inventory"
