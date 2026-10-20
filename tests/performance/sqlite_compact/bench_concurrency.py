r"""Measure reader latency while a writer ingests, under WAL (blueprint 5.3, 12.9).

Three phases run against an ingested database, each for ``--seconds``:
readers alone; readers with a writer appending new minutes as fast as it
can (catch-up, such as replication backfill); and readers with a writer
paced at ``--pace`` seconds per simulated minute, which is ten times real
time at the default of 6. Readers and the writer are separate processes,
like the web/API workers and the ingest service. The writer samples the
WAL size after every commit, which shows whether long reads keep
checkpoints from completing.

Dependencies:
    multiprocessing: ``spawn`` context, so each process has its own SQLite state.
    bench_queries, bench_ingest: The query suite and batch splitting.
    sqlc_*: Workload, layout, and writer.

Safety:
    Appends synthetic rows to the benchmark database named by ``--db``.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_concurrency \
        --db /data/bench/scale-CH.db --candidate C --hot --out conc.json
"""

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

from tests.performance.sqlite_compact.bench_ingest import SEED, START_US, split_batches
from tests.performance.sqlite_compact.bench_queries import build_queries, infer_now
from tests.performance.sqlite_compact.sqlc_measure import summarize_ms
from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db
from tests.performance.sqlite_compact.sqlc_topology import MINUTE_US, build_topology
from tests.performance.sqlite_compact.sqlc_workload import Workload
from tests.performance.sqlite_compact.sqlc_writer import Writer

READ_MIX = ("raw_24h", "ds_7d", "top_util_builtin", "current_device_seek")


def reader(db: str, layout: Layout, now: int, seconds: float, out: mp.Queue) -> None:
    """Run the read mix in a loop and report per-query latencies.

    Args:
        db (str): Database path.
        layout (Layout): Candidate layout.
        now (int): End of the pre-loaded data; query windows end here.
        seconds (float): How long to run.
        out (mp.Queue): Receives ``{query: [ms, ...]}``.

    Returns:
        None

    Raises:
        sqlite3.Error: If a query fails.
    """
    conn = open_db(db, Pragmas(), readonly=True)
    queries = build_queries(layout, now, projection=False)
    lat: dict[str, list[float]] = {n: [] for n in READ_MIX}
    end, i = time.monotonic() + seconds, 0
    while time.monotonic() < end:
        for name in READ_MIX:
            t = time.perf_counter()
            queries[name](conn, i)
            lat[name].append((time.perf_counter() - t) * 1000)
        i += 1
    conn.close()
    out.put(lat)


def writer(
    db: str,
    layout: Layout,
    first_minute: int,
    timing: tuple[float, float, bool],
    out: mp.Queue,
) -> None:
    """Append new minutes in 10-second batches and report commit latencies.

    Args:
        db (str): Database path.
        layout (Layout): Candidate layout.
        first_minute (int): First minute index to generate.
        timing (tuple[float, float, bool]): Seconds to run; seconds per
            simulated minute (0 = as fast as possible); whether to run
            ``wal_checkpoint(TRUNCATE)`` after each move so continuous
            readers cannot keep the WAL from resetting.
        out (mp.Queue): Receives a summary dict.

    Returns:
        None

    Raises:
        sqlite3.Error: If a batch fails.
    """
    seconds, pace, truncate = timing
    conn = open_db(db, Pragmas())
    w, wl = Writer(conn, layout), Workload(build_topology(SEED), START_US)
    commits, moves, ckpts, rows, wal_max, m = [], [], [], 0, 0, first_minute
    busy = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        t_minute, start = time.monotonic(), START_US + m * MINUTE_US
        for part in split_batches(wl.minute(m), 10, start):
            t = time.perf_counter()
            rows += w.write(part)
            commits.append((time.perf_counter() - t) * 1000)
            wal_max = max(wal_max, Path(db + "-wal").stat().st_size)
        if layout.hot and (m + 1) % 15 == 0:
            t = time.perf_counter()
            w.move(start + MINUTE_US - 2 * MINUTE_US)
            moves.append((time.perf_counter() - t) * 1000)
            if truncate:
                t = time.perf_counter()
                busy += conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                ckpts.append((time.perf_counter() - t) * 1000)
        m += 1
        if pace:
            time.sleep(max(0.0, t_minute + pace - time.monotonic()))
    conn.close()
    out.put(
        {
            "minutes": m - first_minute,
            "rows": rows,
            "rows_per_s": round(rows / seconds),
            "commit": summarize_ms(commits),
            "move": summarize_ms(moves) if moves else None,
            "wal_max_bytes": wal_max,
            "truncate_checkpoint": summarize_ms(ckpts) if ckpts else None,
            "truncate_busy": busy,
        }
    )


def run_phase(
    a: argparse.Namespace, layout: Layout, now: int, first_minute: int, pace: float | None
) -> dict[str, object]:
    """Run readers, plus a writer unless ``pace`` is None, and merge their results.

    Args:
        a (argparse.Namespace): Parsed arguments.
        layout (Layout): Candidate layout.
        now (int): End of the pre-loaded data.
        first_minute (int): Writer's first minute.
        pace (float | None): Writer pacing, or None for no writer.

    Returns:
        dict[str, object]: Reader summaries per query and the writer summary.

    Raises:
        RuntimeError: If a child process fails.
    """
    ctx = mp.get_context("spawn")
    q_read, q_write = ctx.Queue(), ctx.Queue()
    procs = [
        ctx.Process(target=reader, args=(a.db, layout, now, a.seconds, q_read))
        for _ in range(a.readers)
    ]
    if pace is not None:
        procs.append(
            ctx.Process(
                target=writer,
                args=(a.db, layout, first_minute, (a.seconds, pace, a.truncate), q_write),
            )
        )
    for p in procs:
        p.start()
    merged: dict[str, list[float]] = {n: [] for n in READ_MIX}
    for _ in range(a.readers):
        for name, lat in q_read.get().items():
            merged[name] += lat
    result: dict[str, object] = {"readers": {n: summarize_ms(v) for n, v in merged.items()}}
    if pace is not None:
        result["writer"] = q_write.get()
    for p in procs:
        p.join()
        if p.exitcode:
            raise RuntimeError(f"child process failed with exit code {p.exitcode}")
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the three phases and write JSON results.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: Process exit status.

    Raises:
        RuntimeError: If a child process fails.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--candidate", choices="ABC", required=True)
    ap.add_argument("--hot", action="store_true")
    ap.add_argument("--readers", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--pace", type=float, default=6.0)
    ap.add_argument("--truncate-after-move", dest="truncate", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    layout = Layout(a.candidate, hot=a.hot)
    now = infer_now(a.db)
    first = (now - START_US) // MINUTE_US
    idle = run_phase(a, layout, now, first, None)
    burst = run_phase(a, layout, now, first, 0.0)
    written = burst["writer"]["minutes"] if isinstance(burst["writer"], dict) else 0
    paced = run_phase(a, layout, now, first + written, a.pace)
    res = {"idle": idle, "max_rate_writer": burst, "paced_writer": paced}
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
