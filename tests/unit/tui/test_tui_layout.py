"""Column fitting, list controls, and the ``vakr tui`` command line (TUI-008, TUI-003, 13.1).

Dependencies:
    pytest; vakr.tui; vakr.cli.

Example:
    $ uv run pytest tests/unit/tui/test_tui_layout.py
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from rich.text import Text

from tests.unit.query.query_testkit import mixed_fleet
from tests.unit.query.test_status_cli import seed
from vakr.cli import cmd_tui
from vakr.cli.main import main
from vakr.query import QueryLimits, Reading
from vakr.tui.app import VakrTui
from vakr.tui.columns import DEVICE_COLUMNS, INTERFACE_COLUMNS, clipped, fit, reading_cell
from vakr.tui.list_controls import ListControls
from vakr.tui.modals import health_text


def test_fit_keeps_priority_zero_columns_and_display_order() -> None:
    """TUI-008, 13.4: state, name, and age on devices; name, state, rates, age on interfaces.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rows = list(mixed_fleet().queries().device_list().rows)
    tiny = [c.key for c in fit(DEVICE_COLUMNS, rows, 10)]
    assert tiny == ["state", "device", "age"]
    wide = [c.key for c in fit(DEVICE_COLUMNS, rows, 400)]
    assert wide == [c.key for c in DEVICE_COLUMNS]
    keys = [c.key for c in fit(INTERFACE_COLUMNS, [], 10)]
    assert keys == ["name", "state", "in", "out", "age"]


def test_cells_carry_text_not_only_style() -> None:
    """12.8: missing is a dash, stale says so; clipping marks the cut.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert reading_cell(Reading("—", None, "first sample")).style == "dim"
    assert reading_cell(Reading("1 s (stale)", 1.0, None, True)).style == "italic"
    assert str(clipped(Text("Unknown — collector overloaded"), 12)) == "Unknown — c…"
    assert str(clipped(Text("short"), 12)) == "short"


def test_list_controls_paging_and_summaries() -> None:
    """TUI-003: pages stay inside the list; failures say STALE with the time of the rows shown.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    c = ListControls()
    assert not c.page(-1, 10)
    assert not c.page(1, 10)
    assert c.page(1, 900)
    assert c.request().offset == 500
    c.set_search("x" * 300)
    assert (c.offset, len(c.request().search)) == (0, 256)
    assert c.summary(None, None, None, UTC) == "loading…"
    when = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    assert c.summary(None, "boom", when, UTC).startswith("[bold yellow]STALE: boom.")
    assert "never" in c.summary(None, "boom", None, UTC)
    cut = mixed_fleet().queries(limits=QueryLimits(max_devices=2)).device_list()
    assert "list truncated (E.29)" in c.summary(cut, None, None, UTC)
    assert "no data loaded yet" in health_text(None, "boom")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the installation.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: The state directory.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    state = tmp_path / "var/lib/vakr"
    state.mkdir(parents=True)
    return state


def test_tui_command_line(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """13.1: ``--api-url`` is refused for now; ``--refresh`` is bounded; no data yet exits 3.

    Args:
        root: State directory.
        capsys: Output capture.
        monkeypatch: Replaces the app's main loop.

    Returns:
        None

    Raises:
        None
    """
    assert main(["tui", "--api-url", "https://vakr.example.net"]) == 3
    assert "API arrives in a later release" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["tui", "--refresh", "1"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        main(["tui", "--refresh", "soon"])
    assert main(["tui"]) == 3
    assert "no data yet" in capsys.readouterr().err
    started: dict[str, Any] = {}

    def fake_run(app: VakrTui) -> None:
        """Record how the app was configured instead of taking over the terminal.

        Args:
            app: The app.

        Returns:
            None

        Raises:
            None
        """
        started.update(refresh=app.refresh_s, zone=app.zone)

    monkeypatch.setattr(VakrTui, "run", fake_run)
    seed(root)
    assert main(["tui"]) == 0
    assert started == {"refresh": 10.0, "zone": ""}
    assert main(["tui", "--refresh", "5"]) == 0
    assert started["refresh"] == 5.0
    monkeypatch.setattr(cmd_tui, "drop_to_service_user", lambda: "cannot switch")
    assert main(["tui"]) == 3


def test_tui_refuses_an_unreadable_metadata_database(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A metadata file that is not a database is reported, and nothing starts.

    Args:
        root: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    (root / "metadata.db").write_bytes(b"not a database" * 100)
    assert main(["tui"]) == 3
    assert "cannot read the metadata database" in capsys.readouterr().err
