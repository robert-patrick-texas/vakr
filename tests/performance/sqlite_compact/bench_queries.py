# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
r"""Run the ADR-021 query suite against a benchmark database: warm p50/p95 plus cold runs.

Warm runs repeat each query on one series after a warm-up, so data is in
both SQLite's page cache and the OS page cache. Cold runs open a fresh
connection, evict the database and WAL from the OS page cache, and query
a different series each time. Each result records the rows the query read
(``samples``), so latency can be plotted against points returned.

Dependencies:
    sqlc_queries, sqlc_current: The queries under test.
    sqlc_measure: Timing, percentiles, cache eviction.
    sqlc_schema: Layout and connection settings.

Safety:
    Opens the database read-only; nothing is modified.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_queries \
        --db /data/bench/c.db --candidate C --runs 50 --cold 5 --out q.json
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from tests.performance.sqlite_compact import sqlc_current as cur
from tests.performance.sqlite_compact import sqlc_queries as q
from tests.performance.sqlite_compact.sqlc_measure import evict, summarize_ms, time_runs
from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db, tables_for

HOUR_US = 3_600_000_000
DAY_US = 24 * HOUR_US
Query = Callable[[sqlite3.Connection, int], list[tuple]]


def samples_of(name: str, rows: list[tuple]) -> int:
    """Count the native samples a query result represents.

    Args:
        name (str): Query name from ``build_queries``.
        rows (list[tuple]): Its result.

    Returns:
        int: Samples read; for downsampled queries the sum of bucket counts.

    Raises:
        None
    """
    if name.startswith("ds_"):
        return sum(r[1] for r in rows)
    return len(rows)


def build_queries(layout: Layout, now: int, projection: bool) -> dict[str, Query]:
    """Build the query suite; each query takes a connection and a series index.

    Args:
        layout (Layout): Candidate layout.
        now (int): End of the data, Unix microseconds; windows end here.
        projection (bool): Whether ``cur_iface`` exists.

    Returns:
        dict[str, Query]: Query name to callable.

    Raises:
        None
    """

    def key(i: int) -> tuple[int, int]:
        """Pick a deterministic series spread over routers and switches.

        Args:
            i (int): Run index.

        Returns:
            tuple[int, int]: ``(interface_key, collector_key)``.

        Raises:
            None
        """
        return 1 + (i * 97) % 1600, 1 + i % 2

    m15 = (now - 15 * 60_000_000, now)
    out: dict[str, Query] = {
        "raw_1h": lambda c, i: q.raw(c, layout, key(i), (now - HOUR_US, now)),
        "raw_24h": lambda c, i: q.raw(c, layout, key(i), (now - DAY_US, now)),
        "ds_7d": lambda c, i: q.downsampled(c, layout, key(i), (now - 7 * DAY_US, now)),
        "ds_30d": lambda c, i: q.downsampled(c, layout, key(i), (now - 30 * DAY_US, now)),
        "icmp_raw_24h": lambda c, i: q.icmp_raw(
            c, layout, (1 + i % 100, 1 + i % 2), (now - DAY_US, now)
        ),
        "top_util_builtin": lambda c, _i: q.top_utilization(c, layout, m15, "builtin"),
        "top_util_window": lambda c, _i: q.top_utilization(c, layout, m15, "window"),
        "top_util_python": lambda c, _i: q.top_utilization(c, layout, m15, "python"),
        "top_errors": lambda c, _i: q.top_errors(c, layout, m15),
        "current_device_seek": lambda c, i: cur.latest_by_seek(c, layout, 81 + i % 20),
        "current_all_seek": lambda c, _i: cur.latest_by_seek(c, layout),
    }
    if projection:
        out["current_device_proj"] = lambda c, i: cur.latest_from_projection(c, 81 + i % 20)
        out["current_all_proj"] = lambda c, _i: cur.latest_from_projection(c)
    return out


def run_suite(
    db_path: str,
    layout: Layout,
    now: int,
    runs: int = 50,
    cold: int = 5,
    projection: bool = False,
    only: set[str] | None = None,
) -> dict[str, dict]:
    """Time every query warm (``runs`` repeats) and cold (``cold`` evicted runs).

    Args:
        db_path (str): Database file.
        layout (Layout): Candidate layout.
        now (int): End of the data, Unix microseconds.
        runs (int): Warm runs per query, after one untimed warm-up.
        cold (int): Cold runs per query; 0 skips them.
        projection (bool): Include the ``cur_iface`` queries.
        only (set[str] | None): Restrict to these query names.

    Returns:
        dict[str, dict]: Per query: ``warm`` and ``cold`` summaries, the
        rows returned and the samples read.

    Raises:
        sqlite3.Error: If a query fails.
    """
    results: dict[str, dict] = {}
    conn = open_db(db_path, Pragmas(), readonly=True)
    for name, fn in build_queries(layout, now, projection).items():
        if only and name not in only:
            continue
        rows = fn(conn, 0)
        lat, _ = time_runs(lambda fn=fn: fn(conn, 0), runs)
        entry = {"warm": summarize_ms(lat), "rows": len(rows), "samples": samples_of(name, rows)}
        if cold:
            cold_lat = []
            for i in range(1, cold + 1):
                c2 = open_db(db_path, Pragmas(), readonly=True)
                tables_for(c2, "iface", 0, 1)  # load the schema before evicting
                evict(db_path)
                lat_i, _ = time_runs(lambda c2=c2, fn=fn, i=i: fn(c2, i), 1)
                cold_lat += lat_i
                c2.close()
            entry["cold"] = summarize_ms(cold_lat)
        results[name] = entry
    conn.close()
    return results


def infer_now(db_path: str) -> int:
    """Infer the end of the data: the minute after the newest interface row.

    Args:
        db_path (str): Database file.

    Returns:
        int: Unix microseconds, aligned to a minute.

    Raises:
        sqlite3.Error: If the catalog is empty or unreadable.
    """
    conn = open_db(db_path, Pragmas(), readonly=True)
    tables = tables_for(conn, "iface", 0, 2**62)[-1:]
    hot = Layout.hot_table("iface")
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE name = ?", (hot,)).fetchone():
        tables.append(hot)
    newest = max(conn.execute(f"SELECT MAX(ts_us) FROM {t}").fetchone()[0] or 0 for t in tables)
    conn.close()
    return (newest // 60_000_000 + 1) * 60_000_000


def main(argv: list[str] | None = None) -> int:
    """Run the suite from the command line and write JSON results.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: Process exit status.

    Raises:
        sqlite3.Error: If a query fails.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--candidate", choices="ABC", required=True)
    ap.add_argument("--hot", action="store_true")
    ap.add_argument("--projection", action="store_true")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--cold", type=int, default=5)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    layout = Layout(a.candidate, hot=a.hot)
    res = run_suite(
        a.db, layout, infer_now(a.db), a.runs, a.cold, a.projection, set(a.only) if a.only else None
    )
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
