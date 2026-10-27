"""``vakr acceptance export`` on real SQLite databases: output, bounds, exit codes (MVP-ACC-03).

The command is the composition root of the export: it opens both installed
databases without creating either, reads the targets file and opens
``--output`` before any switch to the service user, and maps every failure
to the ``vakr acceptance`` group's exit status (0 written in full, 2 usage,
3 incomplete or unreadable).

Dependencies:
    pytest; vakr.adapters.sqlite; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/query/test_parallel_cli.py
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as samples
from tests.unit.query.test_parallel_export import split, table
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, metadata_migrations
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
    telemetry_migrations,
)
from vakr.cli.main import main
from vakr.domain.ids import BatchId, new_id


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


def seed(state_dir: Path, *, telemetry: bool = True) -> None:
    """Create migrated databases with one device, one port, and ten minutes of data.

    Args:
        state_dir: The state directory.
        telemetry: Also create the telemetry database.

    Returns:
        None

    Raises:
        None
    """
    meta_path = state_dir / METADATA_FILE
    metadata_migrations(meta_path).migrate()
    meta = SqliteMetadataStore(meta_path)
    col = samples.collector("east")
    rtr = samples.device("rtr1.example.net", poll_address="192.0.2.10")
    port = samples.interface(rtr.id, "Gi0/0/1", if_index=2)
    with meta.unit_of_work() as uow:
        uow.collectors.add(col)
        uow.devices.add(rtr)
        uow.interfaces.add(port)
        uow.commit()
    meta.close()
    if not telemetry:
        return
    tele_path = state_dir / TELEMETRY_FILE
    telemetry_migrations(tele_path).migrate()
    store = SqliteTelemetryStore(tele_path)
    now = datetime.now(UTC).replace(microsecond=0)
    rows = []
    for m in range(10):
        at = now - timedelta(minutes=10 - m)
        rows.append(samples.sample(rtr.id, port.id, col.id, at, if_index=2))
        rows.append(samples.icmp(rtr.id, col.id, at, target_address="192.0.2.10"))
    sqlite_telemetry_factory(store)().writer.write_batch(new_id(BatchId), rows)
    store.close()


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Run ``vakr acceptance export``.

    Args:
        capsys: Output capture.
        *args: Arguments after ``export``.

    Returns:
        tuple[int, str, str]: Exit status, stdout, stderr.

    Raises:
        None
    """
    code = main(["acceptance", "export", *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_export_to_stdout_and_to_a_file(
    state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stream holds the samples and cycles; stderr has one summary line.

    Args:
        state: State directory.
        tmp_path: Scratch directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    targets = tmp_path / "targets.txt"
    targets.write_text("interface 192.0.2.10 ifindex=2\nicmp rtr1\ninterface sw9 all\n")
    code, out, err = run(capsys, "--targets", str(targets), "--window", "1h")
    assert code == 0, err
    members = split(out)
    assert len(table(members["rates/d0001.csv"])) == 10
    assert len(table(members["icmp/d0001.csv"])) == 10
    assert "1 interfaces on 1 devices, 10 samples, 10 ICMP cycles" in err
    assert "1 targets matched nothing" in err
    output = tmp_path / "export.txt"
    code, out, err = run(capsys, "--output", str(output), "--window", "5m", "--collector", "east")
    assert code == 0
    assert out == ""
    assert split(output.read_text())["export.json"][0] == "text"
    code, out, err = run(capsys, "--window", "1h", "--limit", "5")
    assert code == 3  # incomplete, as every vakr acceptance command reports it
    assert "the row budget cut it short" in err
    assert "export.json" in split(out)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--limit", "0"], "--limit must be 1 to"),
        (["--window", "32d"], "longer than 31 days"),
        (["--end", "2026-10-27T12:00:00"], "needs a time zone"),
        (["--window", "soon"], ""),
        (["--targets", "/nonexistent/targets.txt"], "No such file"),
        (["--collector", "west"], "no collector named"),
    ],
)
def test_usage_errors_exit_two(
    state: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    """Bad options and windows are usage errors, reported on stderr.

    Args:
        state: State directory.
        capsys: Output capture.
        args: Options that make the run invalid.
        message: Expected explanation.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    code, _, err = run(capsys, *args)
    assert code == 2
    assert message in err


def test_missing_databases_exit_three_and_create_nothing(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No metadata or no telemetry database is "could not be read" (3); nothing is created.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    code, _, err = run(capsys)
    assert code == 3
    assert "does not exist" in err
    assert list(state.iterdir()) == []
    seed(state, telemetry=False)
    code, _, err = run(capsys)
    assert code == 3
    assert TELEMETRY_FILE in err
    assert not (state / TELEMETRY_FILE).exists()


def test_bad_targets_file_and_group_usage(
    state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed targets file is refused by line number; ``vakr acceptance`` alone is usage.

    Args:
        state: State directory.
        tmp_path: Scratch directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    targets = tmp_path / "targets.txt"
    targets.write_text("interface rtr1 ifindex=two\n")
    code, _, err = run(capsys, "--targets", str(targets))
    assert code == 2
    assert "targets line 1" in err
    assert main(["acceptance"]) == 2
    assert "export" in capsys.readouterr().err
