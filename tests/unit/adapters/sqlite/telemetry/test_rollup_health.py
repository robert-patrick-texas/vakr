"""The ``rollups`` self-health check: frontier lag and native data waiting (RET-009, E.10, OPS-001).

Every test relocates the installation with ``VAKR_ROOT`` and writes data
relative to the real clock, because ``vakr check health`` reads it.

Dependencies:
    pytest; tests.integration.rollups.rollup_lab; vakr.health.rollup_check.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_rollup_health.py
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
from vakr.adapters.sqlite.telemetry.rollup_health import RollupVitals
from vakr.app.collector.rollup_job import RollupJob
from vakr.domain.rollups import bucket_start
from vakr.domain.timestamps import to_epoch_us
from vakr.health.model import Status
from vakr.health.registry import CHECKS, run_checks
from vakr.health.rollup_check import check_rollups, judge
from vakr.ports.clock import SystemClock

HOUR, DAY = timedelta(hours=1), timedelta(days=1)


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


def test_check_is_registered_and_quiet_without_a_node_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPS-001: ``vakr check health`` and ``vakr doctor`` run it; nothing to check is OK.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    assert check_rollups in CHECKS
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    assert check_rollups().status is Status.OK
    (tmp_path / "var/lib/vakr").mkdir(parents=True)
    result = check_rollups()
    assert (result.status, result.summary) == (Status.OK, "no current telemetry database; "
                                               "see telemetry_db")  # fmt: skip
    assert "rollups" in {r.name for r in run_checks()}


def test_lag_warns_after_three_hours_and_clears_once_rolled_up(state: Path) -> None:
    """RET-009, E.10: six hours of native data without rollups is WARNING; a pass clears it.

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
    behind = check_rollups()
    assert behind.status is Status.WARNING
    assert "not keeping up" in behind.summary
    lag = {p.label: p.value for p in behind.perfdata}["rollup_lag_s"]
    assert 5 * 3600 <= lag <= 6 * 3600
    RollupJob(backend.rollup_source, backend.rollups).run(clock.now_utc())
    caught_up = check_rollups()
    assert caught_up.status is Status.OK
    assert caught_up.summary.startswith("up to ")
    assert {p.label: p.value for p in caught_up.perfdata}["rollup_lag_s"] <= 1800
    store.close()


def test_native_data_past_retention_without_rollups_is_critical(state: Path) -> None:
    """ROLL-T04, E.10: uncovered native data older than its retention cannot expire.

    Args:
        state: The state directory.

    Returns:
        None

    Raises:
        None
    """
    store = migrated(state)
    backend = SqliteTelemetry(store)
    base = bucket_start(SystemClock().now_utc()) - 31 * DAY
    fleet = Fleet(devices=1, interfaces=1)
    fleet.write(backend, fleet.observations(base, 30))
    result = check_rollups()
    assert result.status is Status.CRITICAL
    assert "retention cannot expire it" in result.summary
    waiting = {p.label: p.value for p in result.perfdata}["rollup_waiting_age_s"]
    assert waiting >= 31 * 86_400 - 3600
    store.close()


def test_judge_ranks_lag_and_waiting_age() -> None:
    """E.10: OK up to 3 h behind, WARNING beyond, CRITICAL past native retention.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    now = to_epoch_us(SystemClock().now_utc())
    h = 3_600_000_000
    frontier = now - now % (h // 4) - 4 * h
    fresh = RollupVitals(frontier, frontier + 1, frontier + 2 * h)
    assert judge(fresh, now, 30 * DAY).status is Status.OK
    late = RollupVitals(frontier, frontier + 1, frontier + 3 * h + 1)
    assert judge(late, now, 30 * DAY).status is Status.WARNING
    assert judge(late, now, timedelta(hours=1)).status is Status.CRITICAL
    first = RollupVitals(None, frontier + 5, frontier + 5)
    found = judge(first, now, 30 * DAY)
    assert found.status is Status.OK
    assert found.summary.startswith("up to nothing yet, ")
    idle = judge(RollupVitals(frontier, None, None), now, 30 * DAY)
    assert (idle.status, [p.value for p in idle.perfdata]) == (Status.OK, [0.0, 0.0])
