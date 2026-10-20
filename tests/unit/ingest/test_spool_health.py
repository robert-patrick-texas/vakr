"""The ``spool`` health check and the health snapshot (OPS-001, OBS-001, OBS-007, SPOOL-T07).

Each test builds an installed-node layout under ``VAKR_ROOT`` and runs the
same check ``vakr check health`` runs. Large spools are simulated with
sparse files, which cost no disk space.

Dependencies:
    pytest; vakr.health; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_health.py
"""

import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import COLLECTOR, T0, interface_samples, run
from vakr.domain.timestamps import to_epoch_us
from vakr.health.model import Status
from vakr.health.registry import CHECKS, run_checks
from vakr.health.spool_check import check_spool
from vakr.ingest import SpoolSettings
from vakr.ingest.capacity import CapacityState
from vakr.ingest.fileops import FileOps
from vakr.ingest.incidents import LossIncident, LossKind
from vakr.ingest.segments import QUARANTINE_DIR
from vakr.ingest.spool import Spool
from vakr.ingest.state import RunState, save_run_state
from vakr.ingest.status import SpoolHealth, collector_state, read_status, write_status
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock, SystemClock


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """An installed-node layout with a 64 MiB spool limit and 1 MiB segments.

    Args:
        tmp_path: Installation root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(
        "config_version = 1\n[spool]\nmax_size_mib = 64\nsegment_size_mib = 1\n"
    )
    paths.state_dir.mkdir(parents=True)
    return paths


def _collector_dir(paths: InstallPaths) -> Path:
    """Create and return the collector's spool directory.

    Args:
        paths: The layout.

    Returns:
        Path: ``<state>/spool/<collector UUID>``.

    Raises:
        None
    """
    directory = paths.state_dir / "spool" / str(COLLECTOR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sparse_segment(paths: InstallPaths, mib: int) -> None:
    """Add a sparse segment file of ``mib`` MiB.

    Args:
        paths: The layout.
        mib: Apparent size.

    Returns:
        None

    Raises:
        None
    """
    with (_collector_dir(paths) / "seg-00000000000000000001.spool").open("wb") as f:
        f.truncate(mib * 1024 * 1024)


def test_ops001_check_is_registered_and_quiet_off_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPS-001: the check runs with every other one and is OK on a host that is not a node.

    Args:
        tmp_path: An empty root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    assert check_spool in CHECKS
    (result,) = [r for r in run_checks() if r.name == "spool"]
    assert result.status is Status.OK
    assert "not an installed vakr node" in result.summary


@pytest.mark.usefixtures("node")
def test_no_spool_yet_is_ok() -> None:
    """A fresh node without a collector spool is OK.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result = check_spool()
    assert (result.status, result.summary.startswith("no spool yet")) == (Status.OK, True)


@pytest.mark.parametrize(
    ("mib", "status", "words"),
    [
        (1, Status.OK, "nothing waiting"),
        (46, Status.WARNING, "warning"),
        (55, Status.CRITICAL, "critical"),
        (62, Status.CRITICAL, "normal polling paused"),
    ],
)
def test_spool_t07_capacity_states_map_to_plugin_status(
    node: InstallPaths, mib: int, status: Status, words: str
) -> None:
    """SPOOL-T07: 70/85/95% of the limit are WARNING, CRITICAL, and CRITICAL with polling paused.

    Args:
        node: The layout.
        mib: Spool size in MiB of the 64 MiB limit.
        status: Expected status.
        words: Expected text.

    Returns:
        None

    Raises:
        None
    """
    _sparse_segment(node, mib)
    result = check_spool()
    assert result.status is status
    assert words in result.summary
    perf = {d.label: d.value for d in result.perfdata}
    assert perf["spool_bytes"] == mib * 1024 * 1024
    assert perf["spool_pct"] == round(100 * mib / 64, 2)


def test_invalid_spool_settings_are_critical(node: InstallPaths) -> None:
    """Settings that stop the collector from starting are CRITICAL.

    Args:
        node: The layout.

    Returns:
        None

    Raises:
        None
    """
    (node.config_dir / "config.toml").write_text("config_version = 1\n[spool]\nwarning_pct = 90\n")
    result = check_spool()
    assert result.status is Status.CRITICAL
    assert "spool.warning_pct" in result.summary


def _status(paths: InstallPaths, **changes: object) -> None:
    """Write a ``status.json`` as a running ingester would.

    Args:
        paths: The layout.
        **changes: Fields to set on an empty snapshot taken now.

    Returns:
        None

    Raises:
        None
    """
    now = to_epoch_us(SystemClock().now_utc())
    health = dataclasses.replace(SpoolHealth.empty(str(COLLECTOR), now, 64 << 20), **changes)
    write_status(_collector_dir(paths), health, FileOps())


def test_store014_database_outage_is_a_warning_not_target_down(node: InstallPaths) -> None:
    """STORE-014, OBS-003: refused writes are reported as the collector's condition only.

    Args:
        node: The layout.

    Returns:
        None

    Raises:
        None
    """
    _sparse_segment(node, 1)
    _status(node, storage_available=False, consecutive_failures=4,
            last_error="StorageError: telemetry.db is locked")  # fmt: skip
    result = check_spool()
    assert result.status is Status.WARNING
    assert "telemetry database not accepting writes (StorageError: telemetry.db is locked)" in (
        result.summary
    )
    assert "polling continues" in result.summary
    assert "down" not in result.summary


def test_store007_recent_incident_and_quarantine_warn(node: InstallPaths) -> None:
    """STORE-007: a loss in the last 24 h, or files awaiting review in quarantine/, are WARNING.

    Args:
        node: The layout.

    Returns:
        None

    Raises:
        None
    """
    directory = _collector_dir(node)
    (directory / QUARANTINE_DIR).mkdir()
    (directory / QUARANTINE_DIR / "undecodable-1-1.spool").write_bytes(b"x" * 10)
    result = check_spool()
    assert result.status is Status.WARNING
    assert "quarantine need review" in result.summary
    from vakr.ingest.incident_log import IncidentLog

    now = to_epoch_us(SystemClock().now_utc())
    IncidentLog(directory, FileOps()).record(
        LossIncident.new(LossKind.TORN_TAIL, now, first_seq=3, estimated=1, detail="test")
    )
    result = check_spool()
    assert "1 telemetry-loss incident(s) in 24 h" in result.summary
    assert "(+1 more)" in result.summary
    assert {d.label: d.value for d in result.perfdata}["spool_incidents"] == 1.0


def test_obs007_ingestion_behind_and_silent_collector_warn(node: InstallPaths) -> None:
    """OBS-007: an old unacknowledged record, or a collector that stopped reporting, is WARNING.

    Args:
        node: The layout.

    Returns:
        None

    Raises:
        None
    """
    settings = SpoolSettings.defaults(node.state_dir / "spool").with_values(fsync_records=1)
    old = SystemClock().now_utc() - timedelta(minutes=10)

    async def fill() -> None:
        """Append records stamped ten minutes ago, then crash (no clean stop).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(settings, COLLECTOR, clock=ManualClock(old))
        await spool.open()
        await spool.append(interface_samples(3, start=T0))
        await spool.close()

    run(fill())
    result = check_spool()
    assert result.status is Status.WARNING
    assert "ingestion behind" in result.summary
    assert {d.label: d.value for d in result.perfdata}["spool_oldest_s"] >= 600
    directory = _collector_dir(node)
    save_run_state(directory, RunState(False, None, 0), FileOps())
    _status(node, taken_us=to_epoch_us(old))
    assert "(+1 more)" in check_spool().summary  # behind, and not reporting


def test_snapshot_round_trip_and_collector_state() -> None:
    """The snapshot survives ``status.json``; capacity trouble degrades only the collector.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    empty = SpoolHealth.empty(str(COLLECTOR), 5, 64)
    assert SpoolHealth.from_dict(empty.to_dict()) == empty
    assert collector_state(empty).value == "healthy"
    critical = dataclasses.replace(empty, state=CapacityState.CRITICAL)
    assert collector_state(critical).value == "degraded"
    assert read_status(Path("/nonexistent")) is None
