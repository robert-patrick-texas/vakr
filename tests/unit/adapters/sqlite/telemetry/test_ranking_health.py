"""The ``rankings`` self-health check: complete data the projection misses (E.28, OPS-001).

Every test relocates the installation with ``VAKR_ROOT`` and writes data
relative to the real clock, because ``vakr check health`` reads it.

Dependencies:
    pytest; tests.integration.rollups.rollup_lab; vakr.health.ranking_check.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_ranking_health.py
"""

from datetime import timedelta
from pathlib import Path

import pytest

from tests.integration.rollups.rollup_lab import Fleet
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_migrations,
)
from vakr.adapters.sqlite.telemetry.ranking_health import ProjectionVitals
from vakr.app.collector.ranking_projector import RankingProjector
from vakr.domain.errors import StorageError
from vakr.domain.rollups import bucket_start
from vakr.health import ranking_check
from vakr.health.model import Status
from vakr.health.ranking_check import check_rankings, judge
from vakr.health.registry import CHECKS, run_checks
from vakr.ports.clock import SystemClock

HOUR = timedelta(hours=1)


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


def migrated(state_dir: Path) -> SqliteTelemetryStore:
    """Create the node's telemetry database and open it.

    Args:
        state_dir: The state directory.

    Returns:
        SqliteTelemetryStore: The open store; close it when done.

    Raises:
        None
    """
    telemetry_migrations(state_dir / TELEMETRY_FILE).migrate()
    return SqliteTelemetryStore(state_dir / TELEMETRY_FILE)


def test_check_is_registered_and_quiet_without_a_node_data_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPS-001: ``vakr check health`` runs it; no node, database, or recent data is OK.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    assert check_rankings in CHECKS
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    assert check_rankings().status is Status.OK
    state_dir = tmp_path / "var/lib/vakr"
    state_dir.mkdir(parents=True)
    assert check_rankings().summary == "no current telemetry database; see telemetry_db"
    migrated(state_dir).close()
    quiet = check_rankings()
    assert (quiet.status, quiet.perfdata[0].value) == (Status.OK, 0.0)
    assert "rankings" in {r.name for r in run_checks()}


def test_e28_unprojected_hours_warn_and_a_projector_pass_clears_them(state: Path) -> None:
    """E.28: six unprojected hours are WARNING, never CRITICAL; one pass makes it OK.

    Args:
        state: The state directory.

    Returns:
        None

    Raises:
        None
    """
    store = migrated(state)
    backend, clock = SqliteTelemetry(store), SystemClock()
    base = bucket_start(clock.now_utc()) - 6 * HOUR
    fleet = Fleet(devices=1, interfaces=1)
    fleet.write(backend, fleet.observations(base, 360))
    behind = check_rankings()
    assert behind.status is Status.WARNING
    assert "stay exact" in behind.summary
    missing = {p.label: p.value for p in behind.perfdata}["ranking_unprojected_s"]
    assert 5 * 3600 <= missing <= 6 * 3600
    RankingProjector(backend.ranking_projection).run(clock.now_utc())
    caught_up = check_rankings()
    assert caught_up.status is Status.OK
    assert "0.0 h read natively" in caught_up.summary
    store.close()


def test_e28_judge_thresholds() -> None:
    """E.28: one hour unprojected is still OK; more is WARNING.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert judge(ProjectionVitals(96, 4)).status is Status.OK
    late = judge(ProjectionVitals(90, 5))
    assert (late.status, late.perfdata[0].value) == (Status.WARNING, 4500.0)


def test_an_unreadable_projection_is_unknown(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OPS-001: a database that cannot be read is UNKNOWN, never a target's problem.

    Args:
        state: The state directory.
        monkeypatch: Breaks the measurement.

    Returns:
        None

    Raises:
        None
    """
    migrated(state).close()

    def broken(*_args: object) -> ProjectionVitals:
        """Fail like a locked or damaged file.

        Args:
            *_args: Ignored.

        Returns:
            ProjectionVitals: Never.

        Raises:
            StorageError: Always.
        """
        raise StorageError("disk I/O error", retryable=True)

    monkeypatch.setattr(ranking_check, "measure_projection", broken)
    result = check_rankings()
    assert (result.status, result.summary.startswith("cannot read")) == (Status.UNKNOWN, True)
