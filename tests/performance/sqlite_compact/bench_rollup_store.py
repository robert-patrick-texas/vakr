# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
r"""Build the 400-day rollup store in monthly partitions and time long-range graphs.

MVP-ACC-05 requires 400-day graphs to render in under 2 s. This builds
``--days`` of 15-minute interface and ICMP rollups at the full 2x-MVP
series count (3,200 and 200), one UTC day per transaction like a rollup
job catching up, then measures size per row and the 30, 90, 180 and
400-day graph queries warm and cold. Days are counted back from the end
of the native window, so the store covers days 1-400 as in production,
where rollups are computed while native rows still exist (E.10).

Dependencies:
    sqlc_rollup_gen: Synthetic rollup rows.
    sqlc_queries: ``rollup_range`` and ``icmp_rollup_range``.
    sqlc_measure, sqlc_schema, sqlc_writer: Timing, layout, base tables.

Safety:
    Writes only under ``--data-dir``; stops before ``--cap-gib``.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_rollup_store --days 400 \
        --data-dir /data/bench --out rollup-store.json
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

from tests.performance.sqlite_compact.bench_ingest import SEED, START_US, dir_bytes
from tests.performance.sqlite_compact.sqlc_measure import (
    dbstat_by_prefix,
    evict,
    file_bytes,
    host_context,
    summarize_ms,
    time_runs,
)
from tests.performance.sqlite_compact.sqlc_queries import icmp_rollup_range, rollup_range
from tests.performance.sqlite_compact.sqlc_rollup_gen import day_rows
from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db, tables_for
from tests.performance.sqlite_compact.sqlc_topology import DAY_US, build_topology
from tests.performance.sqlite_compact.sqlc_writer import create_base

END_US = START_US + 30 * DAY_US  # rollups end where the native window ends


def load(
    path: str, days: int, cap_gib: float, data_dir: str, rounded: bool = False
) -> dict[str, object]:
    """Create the store and insert ``days`` of rollups, newest day last.

    Args:
        path (str): Database file.
        days (int): Days of rollups to generate.
        cap_gib (float): Stop before the data directory exceeds this size.
        data_dir (str): Directory checked against the cap.
        rounded (bool): Round bit rates to whole bits per second, which
            SQLite then stores as 3-6 byte integers instead of 8-byte floats.

    Returns:
        dict[str, object]: Days loaded, rows, insert seconds and rate.

    Raises:
        sqlite3.Error: If an insert fails.
    """
    layout, topo = Layout("C"), build_topology(SEED)
    conn = open_db(path, Pragmas())
    create_base(conn, topo, layout)
    rows = loaded = 0
    insert_s = 0.0
    for d in range(days):
        if dir_bytes(data_dir) > cap_gib * 2**30:
            break
        day0 = END_US - (days - d) * DAY_US
        iface, icmp = day_rows(topo, day0)
        if rounded:
            iface = [
                (*r[:8], *(None if v is None else float(round(v)) for v in r[8:14]), *r[14:])
                for r in iface
            ]
        t = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE")
        for kind, batch in (("iface_15m", iface), ("icmp_15m", icmp)):
            table = layout.ensure(conn, kind, day0)
            marks = ", ".join("?" * len(batch[0]))
            conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({marks})", batch)
        conn.execute("COMMIT")
        insert_s += time.perf_counter() - t
        rows, loaded = rows + len(iface) + len(icmp), d + 1
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return {
        "days": loaded,
        "rows": rows,
        "insert_s": round(insert_s, 1),
        "rows_per_s": round(rows / insert_s) if insert_s else 0,
    }


QUERIES = {"iface": (rollup_range, 1600), "icmp": (icmp_rollup_range, 100)}


def graph(conn: sqlite3.Connection, name: str, window: tuple[int, int], i: int) -> list[tuple]:
    """Query one long-range graph for a deterministic series.

    Args:
        conn (sqlite3.Connection): Reader connection.
        name (str): ``"iface"`` or ``"icmp"``.
        window (tuple[int, int]): ``(t0_us, t1_us)``.
        i (int): Run index selecting the series.

    Returns:
        list[tuple]: Graph points; column 2 is the native sample count.

    Raises:
        sqlite3.Error: If the query fails.
    """
    fn, n = QUERIES[name]
    return fn(conn, Layout("C"), (1 + (i * 97) % n, 1 + i % 2), window)


def time_graphs(path: str, runs: int, cold: int) -> dict[str, dict]:
    """Time interface and ICMP rollup graphs over 30-400 days, warm and cold.

    Args:
        path (str): Database file.
        runs (int): Warm runs per window after one warm-up.
        cold (int): Cold runs per window, each on a different series.

    Returns:
        dict[str, dict]: Per window: points, samples represented, warm and
        cold summaries.

    Raises:
        sqlite3.Error: If a query fails.
    """
    out = {}
    conn = open_db(path, Pragmas(), readonly=True)
    for days in (30, 90, 180, 400):
        window = (END_US - days * DAY_US, END_US)
        for name in QUERIES:
            points = graph(conn, name, window, 0)
            lat, _ = time_runs(lambda n=name, w=window: graph(conn, n, w, 0), runs)
            cold_lat = []
            for i in range(1, cold + 1):
                c2 = open_db(path, Pragmas(), readonly=True)
                tables_for(c2, "iface_15m", 0, 1)  # load the schema before evicting
                evict(path)
                cold_lat += time_runs(lambda c=c2, n=name, w=window, i=i: graph(c, n, w, i), 1)[0]
                c2.close()
            out[f"{name}_{days}d"] = {
                "points": len(points),
                "samples": sum(p[1] for p in points),
                "warm": summarize_ms(lat),
                "cold": summarize_ms(cold_lat),
            }
    conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    """Build the store, measure it, and write JSON results.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: Process exit status.

    Raises:
        sqlite3.Error: If loading or a query fails.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--name", default="rollup-store")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--cold", type=int, default=5)
    ap.add_argument("--cap-gib", type=float, default=14.0)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--round", action="store_true", help="round rollup bit rates")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    path = str(Path(a.data_dir) / f"{a.name}.db")
    for suffix in ("", "-wal", "-shm"):
        Path(path + suffix).unlink(missing_ok=True)
    res: dict[str, object] = {
        "host": host_context(a.data_dir),
        "seed": SEED,
        "load": load(path, a.days, a.cap_gib, a.data_dir, a.round),
    }
    res["size"] = file_bytes(path)
    conn = open_db(path, Pragmas())
    res["dbstat"] = dbstat_by_prefix(conn)
    conn.close()
    res["graphs"] = time_graphs(path, a.runs, a.cold)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    if not a.keep:
        for suffix in ("", "-wal", "-shm"):
            Path(path + suffix).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
