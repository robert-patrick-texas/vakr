"""Edge cases: paging bounds, unreadable stores and configuration, rendering corners.

Dependencies:
    pytest; vakr.query; vakr.cli status helpers.

Example:
    $ uv run pytest tests/unit/query/test_query_edges.py
"""

from pathlib import Path

import pytest

from tests.unit.query.query_testkit import NOW, Fleet, mixed_fleet
from tests.unit.query.test_status_cli import seed
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE
from vakr.cli import status_stores
from vakr.cli.main import main
from vakr.cli.status_render import render_detail, render_list
from vakr.domain.enums import IcmpState
from vakr.domain.errors import StorageError
from vakr.domain.paging import Page, PageRequest
from vakr.ports.telemetry import TelemetryBackend
from vakr.query import QueryLimits
from vakr.query.detail import read_interface_states
from vakr.query.snapshot import pages


def test_pages_follows_cursors_and_stops_at_the_cap() -> None:
    """E.29: bounded reads, one page at a time, never more than the cap.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    data = list(range(1_200))
    asked: list[int] = []

    def fetch(request: PageRequest) -> Page[int]:
        """Serve ``data`` in pages keyed by offset text.

        Args:
            request: Limit and cursor.

        Returns:
            Page[int]: One page.

        Raises:
            None
        """
        asked.append(request.limit)
        start = int(request.cursor or 0)
        end = start + request.limit
        more = str(end) if end < len(data) else None
        return Page(tuple(data[start:end]), more, len(data))

    assert pages(fetch, 5_000) == (data, False)
    assert asked == [500, 500, 500]
    asked.clear()
    items, truncated = pages(fetch, 700)
    assert (len(items), truncated, asked) == (700, True, [500, 201])


def test_interface_state_reads_report_storage_trouble() -> None:
    """13.4: interface samples that cannot be read become a reason, not an exception.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    c = fleet.collectors["east"].id

    def broken() -> TelemetryBackend:
        """Fail like a damaged telemetry database.

        Args:
            None

        Returns:
            TelemetryBackend: Never.

        Raises:
            StorageError: Always.
        """
        raise StorageError("telemetry.db is damaged", retryable=False)

    assert read_interface_states(None, d.id, c, NOW, 10) == ({}, "no telemetry database yet")
    assert read_interface_states(broken, d.id, c, NOW, 10)[1] == (
        "telemetry unreadable: telemetry.db is damaged"
    )
    assert read_interface_states(fleet.telemetry, d.id, c, NOW, 10) == ({}, None)


def test_render_corners() -> None:
    """Long cells are cut, truncation is announced, and positions are compared.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    fleet.device("x" * 40)
    text = render_list(fleet.queries(limits=QueryLimits(max_devices=3)).device_list(), "UTC")
    assert "more devices exist than one list reads" in text
    two = Fleet(("east", "west"))
    d = two.device("sw1", poll_address="192.0.2.7")
    two.poll(d, position="east", icmp=IcmpState.UNREACHABLE, snmp=None)
    two.poll(d, position="west", reachability=None)
    detail = two.queries().device_detail("sw1")
    assert detail is not None
    out = render_detail(detail)
    assert "Positions:" in out
    assert "east" in out
    assert "west" in out
    assert "none in inventory" in out
    long = Fleet()
    long.poll(long.device("y" * 30, comment="z" * 10))
    listing = render_list(long.queries().device_list(), "UTC")
    assert "y" * 24 in listing


def test_interval_override_is_explained() -> None:
    """12.4: configured and effective intervals both show when they differ.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    fleet.poll(d, configured_interval_s=120)
    view = fleet.queries().device_detail("sw1")
    assert view is not None
    assert dict(view.facts)["Poll interval"].text == "60 s effective, 120 s configured"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    state_dir = tmp_path / "var/lib/vakr"
    state_dir.mkdir(parents=True)
    return state_dir


def test_unreadable_telemetry_and_bad_configuration(
    state: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """13.4: a damaged telemetry file shows inventory as Unknown; bad config falls back loudly.

    Args:
        state: State directory.
        capsys: Output capture.
        monkeypatch: Makes the configuration unreadable.

    Returns:
        None

    Raises:
        None
    """
    seed(state, telemetry=False)
    (state / TELEMETRY_FILE).write_bytes(b"not a database" * 100)
    config = state.parent.parent.parent / "etc/vakr"
    config.mkdir(parents=True)
    (config / "config.toml").write_text("[polling\n", encoding="utf-8")
    assert main(["status"]) == 3
    out, err = capsys.readouterr()
    assert "Unknown — telemetry unavailable" in out
    assert "telemetry unreadable" in out
    assert "configuration has problems" in err

    def unreadable(config_dir: Path, *, strict: bool) -> None:
        """Fail like a configuration file the service user may not read.

        Args:
            config_dir: Ignored.
            strict: Ignored.

        Returns:
            None

        Raises:
            OSError: Always.
        """
        del config_dir, strict
        raise PermissionError("config.toml: permission denied")

    monkeypatch.setattr(status_stores, "load_effective_config", unreadable)
    assert main(["status"]) == 3
    assert "using packaged defaults: config.toml: permission denied" in capsys.readouterr().err
