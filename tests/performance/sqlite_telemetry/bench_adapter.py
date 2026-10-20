r"""Load full MVP days through the real telemetry adapter and measure it against ADR-021.

Every observation goes through ``SqliteTelemetry.writer.write_batch`` in
10-second flushes (about 634 rows), ``maintain`` runs every 15 simulated
minutes, and the rollups M4 would write are written through
``RollupRepository.write`` once each bucket is 10 minutes old (E.10). The
run then measures bytes per row with ``dbstat``, times graph and state
queries warm and cold, projects the per-node worst case of blueprint 27.3,
writes ``results.json``, prints a Markdown summary, and deletes the
database unless ``--keep`` is given. A size cap stops the load early.

Dependencies:
    tests.performance.sqlite_telemetry.tele_workload, bench_probe.
    vakr.adapters.sqlite.telemetry: The adapter under test.

Safety:
    Writes only under ``--data-dir`` (default ``.claude/bench-data``, gitignored).

Example:
    $ uv run python -m tests.performance.sqlite_telemetry.bench_adapter --days 1 \
        --data-dir .claude/bench-data/telemetry
"""

import argparse
import json
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from tests.performance.sqlite_telemetry.bench_probe import project, sizes, timed
from tests.performance.sqlite_telemetry.tele_workload import (
    IFACE_METRICS,
    START,
    Generator,
    Rollups,
    Topology,
    build_topology,
)
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_migrations,
)
from vakr.domain.enums import ObservationType
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.paging import PageRequest
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.ports.telemetry.results import RetentionPolicy

MINUTE = timedelta(minutes=1)


def _pct(values: list[float], q: float) -> float:
    """Return a percentile of ``values`` (nearest rank on the sorted list).

    Args:
        values: Measurements.
        q: Fraction, such as 0.95.

    Returns:
        float: The value, or 0.0 for no values.

    Raises:
        None
    """
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def _written() -> int:
    """Return the bytes this process has passed to ``write()`` (``/proc/self/io``).

    Args:
        None

    Returns:
        int: ``wchar``, or 0 where unavailable.

    Raises:
        None
    """
    try:
        text = Path("/proc/self/io").read_text(encoding="ascii")
    except OSError:
        return 0
    return next((int(line.split()[1]) for line in text.splitlines() if line.startswith("wchar")), 0)


def ingest(backend: SqliteTelemetry, topo: Topology, minutes: int, cap: int, path: Path) -> dict:
    """Load ``minutes`` of the workload, timing every flush, move, and rollup write.

    Args:
        backend: The adapter.
        topo: The inventory.
        minutes: Simulated minutes to load (1,440 a day).
        cap: Stop if the file grows past this many bytes.
        path: The database file.

    Returns:
        dict: Rows, rates, and latency percentiles in ms.

    Raises:
        None
    """
    gen, roll = Generator(topo, 21), Rollups()
    flushes, moves, rollups, rows, buckets, moved, written = [], [], [], 0, 0, 0, 0
    wchar = _written()
    for m in range(minutes):
        minute = gen.minute(m)
        roll.add(minute)
        for k in range(6):
            batch = minute[k * len(minute) // 6 : (k + 1) * len(minute) // 6]
            t = time.perf_counter()
            rows += backend.writer.write_batch(new_id(BatchId), batch).inserted
            flushes.append(time.perf_counter() - t)
        last = m + 1 == minutes
        if (m + 1) % 15 and not last:
            continue
        t = time.perf_counter()
        moved += backend.maintain(START + (m + 1 + 3 * last) * MINUTE).moved
        moves.append(time.perf_counter() - t)
        # A bucket is complete once its end is 10 minutes past (E.10); the last pass flushes all.
        ready = -(-minutes // 15) if last else (m + 1) // 15 - 1
        for index in range(written, ready):
            t = time.perf_counter()
            buckets += backend.rollups.write(roll.take(index))
            rollups.append(time.perf_counter() - t)
        written = max(written, ready)
        if path.stat().st_size > cap:
            print(f"stopping at minute {m}: file passed the {cap} byte cap", file=sys.stderr)
            break
    ms = 1000.0
    return {
        "rows": rows,
        "moved": moved,
        "rollup_metric_buckets": buckets,
        "insert_seconds": sum(flushes),
        "rows_per_s": rows / sum(flushes),
        "flushes": len(flushes),
        "rows_per_flush": rows / len(flushes),
        "flush_p50_ms": statistics.median(flushes) * ms,
        "flush_p95_ms": _pct(flushes, 0.95) * ms,
        "flush_max_ms": max(flushes) * ms,
        "move_p50_ms": statistics.median(moves) * ms,
        "move_max_ms": max(moves) * ms,
        "rollup_write_p50_ms": statistics.median(rollups) * ms,
        "rollup_write_max_ms": max(rollups) * ms,
        "writer_busy_seconds": sum(flushes) + sum(moves) + sum(rollups),
        "process_written_gb_per_day": (_written() - wchar) / (minutes / 1440) / 1e9,
    }


def queries(path: Path, topo: Topology, days: int) -> dict[str, dict[str, float]]:
    """Time graph, state, and rollup queries warm and cold.

    Args:
        path: The database file.
        topo: The inventory.
        days: Days loaded.

    Returns:
        dict[str, dict[str, float]]: Query name to latency figures.

    Raises:
        None
    """
    east = topo.collectors[0]
    iface = topo.interfaces[5][0]
    switch = topo.devices[90]
    day = TimeWindow(START + timedelta(days=days - 1), START + timedelta(days=days))
    hour = TimeWindow(day.start + timedelta(hours=12), day.start + timedelta(hours=13))
    month = TimeWindow(day.end - timedelta(days=30), day.end)
    bps = Metric.IN_BPS
    suite = {
        "series_1h_raw": lambda b: b.query.interface_series(iface, east, bps, hour),
        "series_24h_capped_1200": lambda b: b.query.interface_series(iface, east, bps, day),
        "downsampled_24h_120s": lambda b: b.query.downsampled_series(iface, east, bps, day),
        "downsampled_30d_1h": lambda b: b.query.downsampled_series(iface, east, bps, month),
        "icmp_24h_raw": lambda b: b.query.icmp_series(
            DeviceId(switch), east, Metric.RTT_MEDIAN_US, day
        ),
        "read_raw_page_500": lambda b: b.query.read_raw(
            ObservationType.INTERFACE, iface, day, PageRequest(limit=500)
        ),
        "device_state": lambda b: b.current.device_state(DeviceId(switch), east, now=day.end),
        "interface_states_switch_40": lambda b: b.current.interface_states(
            DeviceId(switch), east, PageRequest(limit=100), now=day.end
        ),
        "device_states_all_100": lambda b: b.current.device_states(
            east, PageRequest(), now=day.end
        ),
        "rollups_read_96": lambda b: b.rollups.read(iface, east, bps, day, limit=96),
    }
    return {name: timed(path, q) for name, q in suite.items()}


def rollup_graph(directory: Path) -> dict[str, float]:
    """Time 400-day graphs from a store holding 400 days of one series' rollups (MVP-ACC-05).

    A graph reads one series' rows, which are contiguous in each month
    partition, so the other series' volume does not change its cost; 400
    days of all six interface metrics for one interface, written through
    ``RollupRepository.write`` one day at a time, therefore measure the
    MVP-ACC-05 query directly. Native retention of one minute then moves the
    horizon to the rollup frontier, so the whole window is served from rollups.

    Args:
        directory: Where to create the separate database.

    Returns:
        dict[str, float]: Latency figures for the downsampled and port graphs.

    Raises:
        None
    """
    path = directory / "rollup400.db"
    for stale in directory.glob("rollup400.db*"):
        stale.unlink()
    telemetry_migrations(path).migrate()
    store = SqliteTelemetryStore(path, policy=RetentionPolicy(MINUTE, timedelta(days=400)))
    backend, iface, east = SqliteTelemetry(store), new_id(InterfaceId), new_id(CollectorId)
    width = timedelta(seconds=900)
    for day in range(400):
        backend.rollups.write(
            [
                RollupBucket(
                    iface,
                    east,
                    metric,
                    START + timedelta(days=day) + k * width,
                    900,
                    15,
                    15,
                    15,
                    minimum=1e6 + k,
                    mean=2e6 + k * 3.5,
                    p95=3e6 + k / 3,
                    maximum=4e6 + k * 7.25,
                )
                for k in range(96)
                for metric in IFACE_METRICS
            ]
        )
    end = START + timedelta(days=400)
    backend.retention.apply(end)
    store.close()
    window = TimeWindow(START, end)
    out = {
        f"downsampled_400d_{k}": v
        for k, v in timed(
            path, lambda b: b.query.downsampled_series(iface, east, Metric.IN_BPS, window)
        ).items()
    }
    out |= {
        f"port_series_400d_capped_{k}": v
        for k, v in timed(
            path, lambda b: b.query.interface_series(iface, east, Metric.IN_BPS, window)
        ).items()
    }
    out["file_mb"] = path.stat().st_size / 1e6
    for file in directory.glob("rollup400.db*"):
        file.unlink()
    return out


def main(argv: list[str] | None = None) -> int:
    """Run the measurement and print a summary.

    Args:
        argv: Arguments without the program name.

    Returns:
        int: 0 on success.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path(".claude/bench-data/telemetry"))
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--minutes", type=int, help="load this many minutes instead (smoke test)")
    parser.add_argument("--cap-bytes", type=int, default=8 * 10**9)
    parser.add_argument("--keep", action="store_true", help="keep the database afterwards")
    args = parser.parse_args(argv)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    path = args.data_dir / TELEMETRY_FILE
    for stale in args.data_dir.glob(f"{TELEMETRY_FILE}*"):
        stale.unlink()
    telemetry_migrations(path).migrate()
    topo = build_topology(21)
    store = SqliteTelemetryStore(path)
    started = time.perf_counter()
    minutes = args.minutes or args.days * 1440
    days = minutes / 1440
    report: dict[str, Any] = {
        "ingest": ingest(SqliteTelemetry(store), topo, minutes, args.cap_bytes, path)
    }
    store.close()
    report["load_wall_seconds"] = time.perf_counter() - started
    report["sizes"] = sizes(path)
    report["projection"] = project(report["sizes"], days)
    report["queries"] = queries(path, topo, max(1, round(days)))
    report["rollup_graph_400d"] = rollup_graph(args.data_dir)
    (args.data_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not args.keep:
        for file in args.data_dir.glob(f"{TELEMETRY_FILE}*"):
            file.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
