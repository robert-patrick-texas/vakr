r"""Load days of the 2x-MVP workload into one candidate layout, measuring ingest and size.

One simulated minute holds 3,600 rows (3,200 interface, 200 ICMP, 200
SNMP device). Batches are flushed every ``--batch-seconds``: 60 gives one
transaction per minute, 10 gives six smaller ones, split by timestamp the
way phased polling fills the spool. Per day the run records rows, insert
time, commit-latency percentiles, file and WAL size, and the bytes the
process wrote to storage (``/proc/self/io``). At each ``--query-at`` day
count it runs the query suite, so latency can be compared as data grows.

Dependencies:
    sqlc_workload, sqlc_topology: The deterministic workload.
    sqlc_schema, sqlc_writer: Layouts and the batch writer.
    sqlc_measure: Statistics, sizes, ``dbstat``, host context.
    bench_queries: The query suite.

Safety:
    Writes only under ``--data-dir`` and stops before the directory grows
    past ``--cap-gib``. Rows contain no secrets.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_ingest --candidate C \
        --days 2 --data-dir /data/bench --out c.json
"""

import argparse
import json
import os
import time
from bisect import bisect_left
from collections import deque
from pathlib import Path

from tests.performance.sqlite_compact.bench_queries import run_suite
from tests.performance.sqlite_compact.sqlc_measure import (
    dbstat_by_prefix,
    file_bytes,
    host_context,
    summarize_ms,
)
from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db
from tests.performance.sqlite_compact.sqlc_topology import MINUTE_US, build_topology
from tests.performance.sqlite_compact.sqlc_workload import MinuteBatch, Workload
from tests.performance.sqlite_compact.sqlc_writer import Writer, create_base

START_US = 1_788_220_800_000_000  # 2026-09-01T00:00:00Z
SEED = 21


def proc_io() -> dict[str, int]:
    """Read this process's I/O counters.

    Args:
        None

    Returns:
        dict[str, int]: ``wchar`` (bytes passed to write calls) and
        ``write_bytes`` (bytes sent to the storage layer).

    Raises:
        OSError: If ``/proc/self/io`` is unreadable.
    """
    fields = dict(line.split(": ") for line in Path("/proc/self/io").read_text().splitlines())
    return {"wchar": int(fields["wchar"]), "write_bytes": int(fields["write_bytes"])}


def split_batches(b: MinuteBatch, seconds: int, minute_start: int) -> list[dict[str, list]]:
    """Split one minute into flush batches of ``seconds`` by observation time.

    Args:
        b (MinuteBatch): The minute's rows.
        seconds (int): Flush interval; 60 or more means one batch.
        minute_start (int): Start of the minute, Unix microseconds.

    Returns:
        list[dict[str, list]]: One ``{kind: rows}`` dict per flush.

    Raises:
        None
    """
    kinds = {"iface": b.iface, "icmp": b.icmp, "snmp": b.snmp}
    if seconds >= 60:
        return [kinds]
    edges = [minute_start + s * 1_000_000 for s in range(seconds, 60, seconds)]
    out = []
    for i in range(len(edges) + 1):
        part = {}
        for kind, rows in kinds.items():
            lo = bisect_left(rows, edges[i - 1], key=lambda r: r[2]) if i else 0
            hi = bisect_left(rows, edges[i], key=lambda r: r[2]) if i < len(edges) else len(rows)
            part[kind] = rows[lo:hi]
        out.append(part)
    return out


def dir_bytes(path: str) -> int:
    """Sum the sizes of the files directly in a directory.

    Args:
        path (str): Directory.

    Returns:
        int: Total bytes.

    Raises:
        OSError: If the directory cannot be listed.
    """
    return sum(e.stat().st_size for e in os.scandir(path) if e.is_file())


def ingest_day(
    a: argparse.Namespace, w: Writer, wl: Workload, day: int, keep: deque
) -> dict[str, object]:
    """Ingest one simulated UTC day and summarize its timings.

    Args:
        a (argparse.Namespace): Parsed arguments.
        w (Writer): Batch writer.
        wl (Workload): Workload generator.
        day (int): Day index since ``START_US``.
        keep (deque): Receives each flushed batch, bounded to the replay window.

    Returns:
        dict[str, object]: Rows, insert seconds, rows per second, commit
        and move latency summaries, and process I/O for the day.

    Raises:
        sqlite3.Error: If a batch fails.
    """
    commits, moves, rows = [], [], 0
    io0 = proc_io()
    for m in range(day * 1440, (day + 1) * 1440)[: a.minutes or None]:
        minute_start = START_US + m * MINUTE_US
        for part in split_batches(wl.minute(m), a.batch_seconds, minute_start):
            t = time.perf_counter()
            rows += w.write(part)
            commits.append((time.perf_counter() - t) * 1000)
            keep.append(part)
        if a.hot and (m + 1) % a.move_minutes == 0:
            t = time.perf_counter()
            w.move(minute_start + MINUTE_US - a.move_lag_minutes * MINUTE_US)
            moves.append((time.perf_counter() - t) * 1000)
    insert_s = (sum(commits) + sum(moves)) / 1000
    io1 = proc_io()
    return {
        "day": day,
        "rows": rows,
        "insert_s": round(insert_s, 2),
        "rows_per_s": round(rows / insert_s),
        "commit": summarize_ms(commits),
        "move": summarize_ms(moves) if moves else None,
        "wchar": io1["wchar"] - io0["wchar"],
        "write_bytes": io1["write_bytes"] - io0["write_bytes"],
    }


def replay(w: Writer, batches: deque) -> dict[str, object]:
    """Re-submit already committed batches, as after an uncertain commit (STORE-009).

    Args:
        w (Writer): Batch writer.
        batches (deque): Byte-identical batches that were committed earlier.

    Returns:
        dict[str, object]: Batches and rows replayed, rows inserted (which
        must be 0), and the time taken.

    Raises:
        sqlite3.Error: If a batch fails.
    """
    t, inserted = time.perf_counter(), 0
    for part in batches:
        inserted += w.write(part)
    seconds = time.perf_counter() - t
    rows = sum(len(r) for part in batches for r in part.values())
    return {
        "batches": len(batches),
        "rows": rows,
        "inserted": inserted,
        "seconds": round(seconds, 3),
        "rows_per_s": round(rows / seconds) if rows else 0,
    }


def parse(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line options.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        argparse.Namespace: Options.

    Raises:
        SystemExit: On invalid arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidate", choices="ABC", required=True)
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--minutes", type=int, default=0, help="stop each day early (sweeps)")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--batch-seconds", type=int, default=60)
    ap.add_argument("--sync", choices=("NORMAL", "FULL"), default="NORMAL")
    ap.add_argument("--page-size", type=int, default=4096)
    ap.add_argument("--wal-autocheckpoint", type=int, default=1000)
    ap.add_argument("--auto-vacuum", choices=("NONE", "INCREMENTAL"), default="NONE")
    ap.add_argument("--uuid-index", action="store_true")
    ap.add_argument("--round-rates", action="store_true")
    ap.add_argument("--projection", action="store_true")
    ap.add_argument("--hot", action="store_true")
    ap.add_argument("--move-minutes", type=int, default=15)
    ap.add_argument("--move-lag-minutes", type=int, default=2)
    ap.add_argument("--replay-minutes", type=int, default=0)
    ap.add_argument("--query-at", type=int, nargs="*", default=[])
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--cold", type=int, default=5)
    ap.add_argument("--cap-gib", type=float, default=14.0)
    ap.add_argument("--keep", action="store_true", help="keep the database afterwards")
    ap.add_argument("--out", required=True)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build the database day by day and write a JSON result file.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: Process exit status.

    Raises:
        sqlite3.Error: If ingest fails.
    """
    a = parse(argv)
    layout = Layout(a.candidate, uuid_index=a.uuid_index, hot=a.hot)
    path = str(Path(a.data_dir) / f"{a.name or a.candidate}.db")
    for suffix in ("", "-wal", "-shm"):
        Path(path + suffix).unlink(missing_ok=True)
    pragmas = Pragmas(a.sync, a.page_size, a.auto_vacuum, wal_autocheckpoint=a.wal_autocheckpoint)
    conn = open_db(path, pragmas)
    topo = build_topology(SEED)
    create_base(conn, topo, layout, project=a.projection)
    w = Writer(conn, layout, round_rates=a.round_rates, project=a.projection)
    wl = Workload(topo, START_US)
    result: dict[str, object] = {
        "args": vars(a),
        "seed": SEED,
        "start_us": START_US,
        "host": host_context(a.data_dir),
        "days": [],
        "queries": {},
    }
    per_minute = max(1, 60 // a.batch_seconds)
    keep: deque = deque(maxlen=max(1, a.replay_minutes * per_minute))
    t_run = time.perf_counter()
    for day in range(a.days):
        if dir_bytes(a.data_dir) > a.cap_gib * 2**30:
            result["stopped"] = f"disk cap reached before day {day}"
            break
        entry = ingest_day(a, w, wl, day, keep)
        entry |= file_bytes(path)
        result["days"].append(entry)
        print(json.dumps(entry), flush=True)
        if day + 1 in a.query_at:
            now = START_US + (day * 1440 + (a.minutes or 1440)) * MINUTE_US
            result["queries"][str(day + 1)] = run_suite(
                path, layout, now, a.runs, a.cold, a.projection
            )
    result["elapsed_s"] = round(time.perf_counter() - t_run, 1)
    if a.replay_minutes:
        result["replay"] = replay(w, keep)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result["final"] = file_bytes(path)
    result["dbstat"] = dbstat_by_prefix(conn)
    conn.close()
    Path(a.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    if not a.keep:
        for suffix in ("", "-wal", "-shm"):
            Path(path + suffix).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
