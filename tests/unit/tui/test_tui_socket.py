"""The TUI over the ``vakr-api`` socket: the in-process screens, and STALE when it goes (13.4).

The pilot drives the app twice over the mixed fleet: once through
``SocketStatusSource`` on a real socket, once through the API's own
in-process ``OverviewQueries``. The device list, its summary and header,
and the device screen must read the same (E.29 #10, TUI-007); only the
read time in the header differs. When the API disappears after a good
read, the rows stay and are marked STALE with what to run (13.4).

Dependencies:
    pytest, textual; tests.unit.tui.tui_testkit, tests.unit.client.client_testkit.

Example:
    $ uv run pytest tests/unit/tui/test_tui_socket.py
"""

import asyncio
import re
import threading

from textual.pilot import Pilot

from tests.unit.api.api_testkit import ApiKit
from tests.unit.client.client_testkit import api_socket, short_dir
from tests.unit.query.query_testkit import mixed_fleet
from tests.unit.tui.tui_testkit import cells, drive, press, text_of
from vakr.client.status import SocketStatusSource
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.query import DeviceListRequest, DeviceListView
from vakr.tui.app import VakrTui
from vakr.tui.reading import Reader, SupersededError

READ_TIME = re.compile(r" · read in \d+ ms")

type Screens = tuple[list[list[str]], str, str, str, list[list[str]]]


async def screens(app: VakrTui, pilot: Pilot[None]) -> Screens:
    """Read the list, then open the first device and read its screen.

    Args:
        app: The app.
        pilot: Its pilot.

    Returns:
        Screens: List cells, summary, header without the read time, device summary, interfaces.

    Raises:
        None
    """
    rows, summary = cells(app, "devices"), text_of(app, "summary")
    header = READ_TIME.sub("", text_of(app, "header"))
    await press(app, pilot, "enter")
    return rows, summary, header, text_of(app, "device-summary"), cells(app, "interfaces")


def test_e29_10_tui_screens_over_the_socket_equal_in_process() -> None:
    """E.29 #10, TUI-007: list, header, and device screen are identical on both paths.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(fleet=mixed_fleet())
    with short_dir() as d, api_socket(kit.app, d / "api.sock") as source:
        over_socket = drive(source, screens)
    in_process = drive(kit.context.overview, screens)
    assert over_socket == in_process
    assert [r[1] for r in over_socket[0]][:2] == ["core", "old"]
    assert "Down" in over_socket[3]


def test_13_4_api_gone_after_a_good_read_keeps_rows_marked_stale() -> None:
    """13.4: rows stay, marked STALE with what to run; never shown as current.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(fleet=mixed_fleet())

    async def script(app: VakrTui, pilot: Pilot[None]) -> tuple[int, str, str, str]:
        """Break the source, refresh the list, open a device, refresh it.

        Args:
            app: The app.
            pilot: Its pilot.

        Returns:
            tuple: Rows still shown, the summary, the device footer, and the header.

        Raises:
            None
        """
        header = text_of(app, "header")
        source = app.source
        assert isinstance(source, SocketStatusSource)
        await press(app, pilot, "enter")
        source.socket_path = "/nonexistent/vakr/api.sock"
        await press(app, pilot, "r")
        footer = text_of(app, "device-footer")
        await press(app, pilot, "escape", "r")
        return len(cells(app, "devices")), text_of(app, "summary"), footer, header

    with short_dir() as d, api_socket(kit.app, d / "api.sock") as source:
        count, summary, footer, header = drive(source, script)
    gone = "vakr-api is not running: /nonexistent/vakr/api.sock is gone; check `systemctl status"
    assert count == 6
    assert summary.startswith(f"STALE: {gone}")
    assert "Rows shown are from" in summary
    assert footer.startswith(f"STALE: {gone}")
    assert re.search(r" · read in \d+ ms · refresh 600 s$", header)


def test_e29_one_read_in_flight_per_screen_newer_reads_supersede_queued_ones() -> None:
    """E.29: a read that has not started gives way to a newer one; reads never overlap.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(fleet=mixed_fleet())
    gate, active, peak = threading.Event(), [0], [0]
    lock = threading.Lock()

    class Slow:
        """A source whose first read waits for the gate, counting overlapping reads."""

        def device_list(self, request: DeviceListRequest | None = None) -> DeviceListView:
            """Read through the API's services, counting reads in flight.

            Args:
                request: The request.

            Returns:
                DeviceListView: The view.

            Raises:
                None
            """
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            gate.wait(5)
            try:
                return kit.context.overview.device_list(request)
            finally:
                with lock:
                    active[0] -= 1

    async def main() -> list[str]:
        """Ask for five reads at once, as fast typing would.

        Args:
            None

        Returns:
            list[str]: How each ended.

        Raises:
            None
        """
        reader, slow = Reader(), Slow()
        tasks = [asyncio.create_task(reader.read(slow.device_list, DeviceListRequest(limit=n)))
                 for n in (1, 2, 3, 4, MAX_PAGE_LIMIT)]  # fmt: skip
        await asyncio.sleep(0.2)
        gate.set()
        ended = await asyncio.gather(*tasks, return_exceptions=True)
        return [type(e).__name__ if isinstance(e, BaseException) else str(e.limit) for e in ended]

    ended = asyncio.run(main())
    assert peak[0] == 1
    assert ended[0] == "1"
    assert ended[-1] == str(MAX_PAGE_LIMIT)
    assert set(ended[1:-1]) == {SupersededError.__name__}
