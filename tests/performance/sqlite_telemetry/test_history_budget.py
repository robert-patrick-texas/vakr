"""M4 query budgets on SQLite at MVP scale, small enough for the default suite (MVP-ACC-05, 12.9).

- MVP-ACC-05: a 400-day interface traffic graph (two series from 15-minute
  rollups re-bucketed to 12 hours) renders in under 2 s.
- 12.9: the default 15-minute top-utilization and top-error rankings over
  all 1,600 MVP interfaces answer within the 2 s dashboard budget.

``bench_history`` measures the same reads over full days (including the
24-hour ranking) and is run by hand; its numbers are in
``docs/architecture/queries.md``.

Dependencies:
    pytest; tests.performance.sqlite_telemetry.history_load.

Example:
    $ uv run pytest tests/performance/sqlite_telemetry/test_history_budget.py
"""

import time
from datetime import timedelta
from pathlib import Path

from tests.performance.sqlite_telemetry.history_load import (
    build_topology,
    load_minutes,
    metadata_for,
    open_store,
    rollup_days,
)
from tests.performance.sqlite_telemetry.tele_workload import START
from vakr.adapters.sqlite.telemetry import SqliteTelemetry
from vakr.domain.ids import InterfaceId
from vakr.ports.clock import ManualClock
from vakr.query import QuerySettings
from vakr.query.history import HistoryQueries
from vakr.query.history_limits import RangePreset
from vakr.query.history_requests import GraphKind, GraphRequest, TimeRange
from vakr.query.ranking_views import RankingRequest
from vakr.query.rankings import RankingQueries

BUDGET_S = 2.0
SETTINGS = QuerySettings(timezone="UTC")


def test_mvp_acc_05_400_day_graph_under_two_seconds(tmp_path: Path) -> None:
    """MVP-ACC-05, 10.6: 400 days of rollups for one interface render in under 2 s.

    Args:
        tmp_path: Directory for the database file.

    Returns:
        None

    Raises:
        None
    """
    topo = build_topology(21)
    store, telemetry = open_store(tmp_path)
    try:
        iface, east = InterfaceId(topo.interfaces[5][0]), topo.collectors[0]
        assert rollup_days(telemetry(), iface, east, 400) == 400 * 96 * 2
        service = HistoryQueries(metadata_for(topo), telemetry, SETTINGS, ManualClock(START))
        request = GraphRequest(TimeRange(RangePreset.D400), collector_id=east)
        started = time.perf_counter()
        graph = service.interface_graph(iface, GraphKind.TRAFFIC, request)
        elapsed = time.perf_counter() - started
    finally:
        store.close()
    assert graph is not None
    assert graph.available
    assert [(s.metric, len(s.points)) for s in graph.series] == [("in_bps", 800), ("out_bps", 800)]
    assert graph.series[0].resolutions[0].bucket_seconds == 43_200
    assert graph.series[0].points[-1].value is not None
    assert elapsed < BUDGET_S, f"400-day graph took {elapsed:.2f} s"


def test_12_9_top_n_15_minutes_at_mvp_scale(tmp_path: Path) -> None:
    """12.9, DASH-003: the default 15-minute rankings over 1,600 interfaces stay within 2 s.

    Args:
        tmp_path: Directory for the database file.

    Returns:
        None

    Raises:
        None
    """
    topo = build_topology(21)
    store, telemetry = open_store(tmp_path)
    try:
        load_minutes(SqliteTelemetry(store), topo, 16)
        clock = ManualClock(START + timedelta(minutes=16))
        service = RankingQueries(metadata_for(topo), telemetry, SETTINGS, clock)
        started = time.perf_counter()
        util = service.top_utilization(RankingRequest())
        errors = service.top_errors(RankingRequest())
        elapsed = time.perf_counter() - started
    finally:
        store.close()
    assert (util.candidates, len(util.rows), util.excluded_total) == (1600, 10, 0)
    assert util.rows[0].score_pct >= util.rows[-1].score_pct
    assert util.rows[0].coverage.label == "15 of 15 samples"
    assert len(errors.rows) == 10
    assert errors.computed_at == util.computed_at
    assert elapsed < BUDGET_S, f"15-minute rankings took {elapsed:.2f} s"
