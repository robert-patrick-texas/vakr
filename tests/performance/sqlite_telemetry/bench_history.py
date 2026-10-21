r"""Time the M4 query services on a full MVP day in SQLite (MVP-ACC-05, 12.9, E.28).

Loads ``--minutes`` of the MVP workload (default one day: 5.47 million rows,
about five minutes) through the real adapter, plus 400 days of 15-minute
rollups for one interface, then times each service call once cold and
``--runs`` times warm (five by default) and prints a Markdown table with the
cold time, the warm median, and the nearest-rank p95 of every run.
``--reuse`` keeps an existing database in ``--data-dir`` instead of loading
again; ``--only top_`` times only the rankings. ``--project`` migrates the
file and builds the ranking projection first, leaving ``--tail`` minutes
(45, the worst case between two projector passes) to native scans;
``--native`` migrates and forgets it, so every ranking scans natively
(``bench_projection``, E.28). It is a ``python -m`` script, not a test;
``test_history_budget`` is the small default-suite check.

Dependencies:
    tests.performance.sqlite_telemetry.history_load, bench_projection; vakr.query.

Safety:
    Writes only under ``--data-dir`` (default ``.claude/bench-data/history``, gitignored).

Example:
    $ uv run python -m tests.performance.sqlite_telemetry.bench_history --minutes 1440
    $ uv run python -m tests.performance.sqlite_telemetry.bench_history --reuse --project \
        --runs 19 --only top_
"""

import argparse
import statistics
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from tests.performance.sqlite_telemetry.bench_projection import build_projection
from tests.performance.sqlite_telemetry.history_load import (
    build_topology,
    load_minutes,
    load_topology,
    metadata_for,
    open_store,
    rollup_days,
    save_topology,
)
from tests.performance.sqlite_telemetry.tele_workload import START, Topology
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
)
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.logic.stats import p95
from vakr.ports.clock import ManualClock
from vakr.query import QuerySettings
from vakr.query.catalog import CatalogQueries
from vakr.query.catalog_order import InterfaceListRequest, InterfaceSort
from vakr.query.history import HistoryQueries
from vakr.query.history_limits import RangePreset, RankingWindow
from vakr.query.history_requests import GraphKind, GraphRequest, TimeRange
from vakr.query.ranking_views import RankingRequest
from vakr.query.rankings import RankingQueries


def _time(call: Callable[[], Any], runs: int) -> tuple[float, float, float]:
    """Run a call once cold and ``runs`` times warm.

    Args:
        call: The call.
        runs: Warm repetitions.

    Returns:
        tuple: Cold seconds, the warm median, and the nearest-rank p95 of
        every run (cold included), in seconds.

    Raises:
        None
    """
    started = time.perf_counter()
    call()
    cold = time.perf_counter() - started
    warm = []
    for _ in range(runs):
        started = time.perf_counter()
        call()
        warm.append(time.perf_counter() - started)
    return cold, statistics.median(warm), p95([cold, *warm])


def _suite(path: Path, topo: Topology, minutes: int) -> dict[str, Callable[[], Any]]:
    """Build the timed calls over an existing database.

    Args:
        path: Data directory holding ``telemetry.db``.
        topo: The topology the database was loaded with.
        minutes: Minutes loaded, which sets the clock.

    Returns:
        dict: Name to call.

    Raises:
        None
    """
    store = SqliteTelemetryStore(path / TELEMETRY_FILE)
    telemetry = sqlite_telemetry_factory(store)
    meta = metadata_for(topo)
    clock = ManualClock(START + timedelta(minutes=minutes))
    s = QuerySettings(timezone="UTC")
    iface, dev, east = (
        InterfaceId(topo.interfaces[5][0]),
        DeviceId(topo.devices[5]),
        topo.collectors[0],
    )
    hist = HistoryQueries(meta, telemetry, s, clock)
    catalog = CatalogQueries(meta, telemetry, s, clock)

    def graph(preset: RangePreset) -> Callable[[], Any]:
        """Return a traffic-graph call for one preset.

        Args:
            preset: The range.

        Returns:
            Callable: The call.

        Raises:
            None
        """
        request = GraphRequest(TimeRange(preset), collector_id=east)
        return lambda: hist.interface_graph(iface, GraphKind.TRAFFIC, request)

    def ranking(window: RankingWindow) -> Callable[[], Any]:
        """Return a ranking call that never reuses a scan (a fresh service each time).

        Args:
            window: The ranking window.

        Returns:
            Callable: The call.

        Raises:
            None
        """
        return lambda: RankingQueries(meta, telemetry, s, clock).top_utilization(
            RankingRequest(window=window, collector_id=east)
        )

    shared = RankingQueries(meta, telemetry, s, clock)
    hour, day = GraphRequest(TimeRange(RangePreset.H1)), GraphRequest(TimeRange(RangePreset.H24))
    presets = (RangePreset.H1, RangePreset.H24, RangePreset.D7, RangePreset.D400)
    by_traffic = InterfaceListRequest(sort=InterfaceSort.TRAFFIC)
    return {
        **{f"graph_traffic_{p.value}": graph(p) for p in presets},
        "latency_1h_probes": lambda: hist.device_latency(dev, hour),
        "latency_24h_envelope": lambda: hist.device_latency(dev, day),
        **{f"top_utilization_{w.value}": ranking(w) for w in RankingWindow},
        "top_errors_24h_after_utilization_cached": lambda: (
            shared.top_utilization(RankingRequest(window=RankingWindow.H24)),
            shared.top_errors(RankingRequest(window=RankingWindow.H24)),
        ),
        "interface_list_priority_page": catalog.interface_list,
        "interface_list_by_traffic": lambda: catalog.interface_list(by_traffic),
        "search_te0": lambda: catalog.search("te0/0/1"),
    }


def main(argv: list[str] | None = None) -> int:
    """Load (or reuse) the workload and print timings.

    Args:
        argv: Command-line arguments.

    Returns:
        int: Exit status, 0.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=int, default=1440)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--data-dir", type=Path, default=Path(".claude/bench-data/history"))
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--only", default="", help="time only calls whose name starts with this")
    parser.add_argument(
        "--project",
        action="store_true",
        help="migrate and build the ranking projection first (E.28)",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=45,
        help="minutes of newest data left unprojected (with --project)",
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="migrate and forget the ranking projection first (native scans)",
    )
    args = parser.parse_args(argv)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    saved = args.data_dir / "topology.json"
    if args.reuse and saved.exists() and (args.data_dir / TELEMETRY_FILE).exists():
        topo = load_topology(saved)
    else:
        topo = build_topology(21)
        save_topology(topo, saved)
        store, telemetry = open_store(args.data_dir)
        started = time.perf_counter()
        rows = load_minutes(SqliteTelemetry(store), topo, args.minutes)
        rollup_days(telemetry(), InterfaceId(topo.interfaces[5][0]), topo.collectors[0], 400)
        store.close()
        print(f"loaded {rows} rows in {time.perf_counter() - started:.0f} s", file=sys.stderr)
    clock = START + timedelta(minutes=args.minutes)
    if args.project or args.native:
        tail = timedelta(minutes=args.tail if args.project else 24 * 60 + 60)
        print(build_projection(args.data_dir, clock, tail), flush=True)
    print("| Call | Cold (ms) | Warm median (ms) | p95 (ms) |\n|---|---:|---:|---:|")
    for name, call in _suite(args.data_dir, topo, args.minutes).items():
        if not name.startswith(args.only):
            continue
        cold, warm, high = _time(call, args.runs)
        print(f"| {name} | {cold * 1000:.0f} | {warm * 1000:.0f} | {high * 1000:.0f} |",
              flush=True)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
