"""``vakr acceptance``: the group, its options, exit status, and JSON on installed databases.

The installation is relocated with ``VAKR_ROOT``; the databases are real,
migrated SQLite files with a collector, a device, and two hours of polls,
so the reports run end to end through the composition root
(MVP-ACC-01, MVP-ACC-02, MVP-ACC-10, E.35).

Dependencies:
    pytest; tests.contract.contract_samples; vakr.adapters.sqlite; vakr.cli.

Example:
    $ uv run pytest tests/unit/test_cmd_acceptance.py
"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, metadata_migrations
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
    telemetry_migrations,
)
from vakr.cli import cmd_acceptance
from vakr.cli.acceptance_reports import parse_window
from vakr.cli.main import main
from vakr.domain.ids import BatchId, new_id
from vakr.domain.observations import Observation


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the installation and create its state directory.

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


def seed(state_dir: Path) -> None:
    """Create migrated databases with two hours of a device's polls ending now.

    Args:
        state_dir: The state directory.

    Returns:
        None

    Raises:
        None
    """
    metadata_migrations(state_dir / METADATA_FILE).migrate()
    meta = SqliteMetadataStore(state_dir / METADATA_FILE)
    collector, device = s.collector("east"), s.device("core1")
    with meta.unit_of_work() as uow:
        uow.collectors.add(collector)
        uow.devices.add(device)
        uow.commit()
    meta.close()
    telemetry_migrations(state_dir / TELEMETRY_FILE).migrate()
    store = SqliteTelemetryStore(state_dir / TELEMETRY_FILE)
    start = datetime.now(UTC).replace(second=7, microsecond=0) - timedelta(hours=2)
    rows: list[Observation] = [
        s.poll(device.id, collector.id, start + timedelta(minutes=k)) for k in range(120)
    ]
    rows += [s.health(collector.id, start + timedelta(minutes=k)) for k in range(120)]
    sqlite_telemetry_factory(store)().writer.write_batch(new_id(BatchId), rows)
    store.close()


def test_e35_group_lists_its_reports(capsys: pytest.CaptureFixture[str]) -> None:
    """E.35: ``vakr acceptance`` registers schedule, gaps, coverage; no subcommand is usage.

    Args:
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    assert main(["acceptance"]) == 2
    help_text = capsys.readouterr().err
    for name in ("schedule", "gaps", "coverage"):
        assert name in help_text
    assert cmd_acceptance.SUBCOMMAND_MODULES[0] == "vakr.cli.acceptance_reports"


def test_e35_window_option_accepts_hours_and_days() -> None:
    """E.35: ``--window`` takes ``14d`` or ``36h`` and nothing else.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert parse_window("14d") == timedelta(days=14)
    assert parse_window("36h") == timedelta(hours=36)
    for bad in ("14", "2w", "-1d", "1.5d"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_window(bad)


def test_mvp_acc01_schedule_json_on_installed_databases(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MVP-ACC-01: two hours of on-time polls; drift needs 7 days, so it is incomplete (3).

    Args:
        state: The state directory.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    assert main(["acceptance", "schedule", "--window", "2h", "--json"]) == 3
    document = json.loads(capsys.readouterr().out)
    assert document["format"] == "vakr-acceptance-1"
    assert document["criterion"] == "MVP-ACC-01"
    (section,) = document["collectors"]
    assert section["collector"] == "east"
    assert section["late"] == 0
    assert section["overlaps"] == 0


def test_mvp_acc10_gaps_text_ends_with_the_result_line(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MVP-ACC-10: the text report ends with one greppable ``RESULT`` line.

    Args:
        state: The state directory.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    status = main(["acceptance", "gaps", "--window", "1h"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == f"RESULT MVP-ACC-10: {'PASS' if status == 0 else 'FAIL'}"
    assert lines[0].startswith("vakr acceptance gaps (MVP-ACC-10): ")


def test_e35_no_databases_and_bad_limits_are_reported(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.35: no data yet is status 3; an out-of-range ``--limit`` is a usage error (2).

    Args:
        state: The state directory.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    assert main(["acceptance", "coverage"]) == 3
    assert "no data yet" in capsys.readouterr().err
    seed(state)
    assert main(["acceptance", "coverage", "--limit", "0"]) == 2
    assert main(["acceptance", "coverage", "--collector", "west"]) == 2
