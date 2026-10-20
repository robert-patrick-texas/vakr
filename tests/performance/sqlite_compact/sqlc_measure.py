"""Timing, cache-control, size, and host-context helpers for the ADR-021 benchmark.

Blueprint 23.3 says a performance number without its context is not an
acceptance result, so every run records the host, storage, SQLite build
and source revision next to its percentiles. Cold-cache runs evict the
database files from the Linux page cache with ``posix_fadvise(DONTNEED)``,
which works without root for files the user owns; the hypervisor's own
cache, if any, is outside the guest's control and is noted in the report.

Dependencies:
    os, platform, subprocess: Host facts, page-cache eviction, ``git``.
    sqlite3: Build options and the ``dbstat`` virtual table.
    statistics: Means for latency samples.

Safety:
    Read-only apart from the cache hint; nothing here modifies a database.

Example:
    >>> summarize_ms([1.0, 2.0, 3.0, 4.0])["p50_ms"]
    2.5
"""

import os
import platform
import sqlite3
import statistics
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Return the ``q`` quantile by linear interpolation (Hyndman-Fan type 7).

    This is the rule ``numpy.percentile`` and SQLite's ``percentile_cont``
    use, so benchmark statistics and rollup values agree.

    Args:
        sorted_values (Sequence[float]): Ascending values; must not be empty.
        q (float): Quantile between 0 and 1.

    Returns:
        float: The interpolated quantile.

    Raises:
        IndexError: If ``sorted_values`` is empty.
    """
    h = (len(sorted_values) - 1) * q
    lo = int(h)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def summarize_ms(samples: list[float]) -> dict[str, float]:
    """Summarize latency samples given in milliseconds.

    Args:
        samples (list[float]): One latency per run, in milliseconds.

    Returns:
        dict[str, float]: ``n``, ``p50_ms``, ``p95_ms``, ``p99_ms``,
        ``mean_ms``, and ``max_ms``, rounded to 0.01 ms.

    Raises:
        IndexError: If ``samples`` is empty.
    """
    s = sorted(samples)
    out = {"n": len(s), "mean_ms": statistics.fmean(s), "max_ms": s[-1]}
    for name, q in (("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99)):
        out[name] = quantile(s, q)
    return {k: round(v, 2) for k, v in out.items()}


def time_runs(
    fn: Callable[[], object], runs: int, before: Callable[[], None] | None = None
) -> tuple[list[float], object]:
    """Time ``fn`` over several runs, calling ``before`` untimed ahead of each.

    Args:
        fn (Callable[[], object]): The query to time; its result is kept.
        runs (int): Number of timed runs.
        before (Callable[[], None] | None): Untimed preparation, such as a
            cache eviction, or None.

    Returns:
        tuple[list[float], object]: Latencies in milliseconds and the last result.

    Raises:
        Exception: Whatever ``fn`` raises.
    """
    out, result = [], None
    for _ in range(runs):
        if before is not None:
            before()
        t = time.perf_counter()
        result = fn()
        out.append((time.perf_counter() - t) * 1000.0)
    return out, result


def evict(db_path: str) -> None:
    """Drop a database file and its WAL from the Linux page cache.

    Args:
        db_path (str): Main database file; ``-wal`` and ``-shm`` are included.

    Returns:
        None

    Raises:
        OSError: If an existing file cannot be opened.
    """
    for suffix in ("", "-wal"):
        p = db_path + suffix
        if not Path(p).exists():
            continue
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)  # dirty pages cannot be dropped; write them first
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)


def file_bytes(db_path: str) -> dict[str, int]:
    """Report the on-disk size of a database and its WAL.

    Args:
        db_path (str): Main database file.

    Returns:
        dict[str, int]: ``db`` and ``wal`` sizes in bytes (0 when absent).

    Raises:
        None
    """

    def size(p: str) -> int:
        """Return a file's size, or 0 when it does not exist.

        Args:
            p (str): File path.

        Returns:
            int: Size in bytes.

        Raises:
            None
        """
        return Path(p).stat().st_size if Path(p).exists() else 0

    return {"db": size(db_path), "wal": size(db_path + "-wal")}


def dbstat_by_prefix(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Sum ``dbstat`` pages and payload per table family (partition suffix removed).

    Args:
        conn (sqlite3.Connection): Connection to the database to inspect.

    Returns:
        dict[str, dict[str, int]]: Family such as ``obs_iface`` or
        ``obs_iface_key`` to ``pages``, ``bytes``, ``payload``, ``unused``, ``cells``.

    Raises:
        sqlite3.Error: If the ``dbstat`` virtual table is unavailable.
    """
    rows = conn.execute(
        "SELECT name, COUNT(*), SUM(pgsize), SUM(payload), SUM(unused),"
        " SUM(CASE WHEN pagetype = 'leaf' THEN ncell ELSE 0 END) FROM dbstat GROUP BY name"
    )
    out: dict[str, dict[str, int]] = {}
    for name, pages, nbytes, payload, unused, cells in rows:
        parts = name.split("_")
        family = "_".join(p for p in parts if not (p[:1] in "dm" and p[1:].isdigit()))
        agg = out.setdefault(
            family, dict.fromkeys(("pages", "bytes", "payload", "unused", "cells"), 0)
        )
        for key, val in zip(
            ("pages", "bytes", "payload", "unused", "cells"),
            (pages, nbytes, payload, unused, cells),
            strict=True,
        ):
            agg[key] += val or 0
    return out


def _run(cmd: list[str]) -> str:
    """Run a read-only command and return its stripped output, or "" on failure.

    Args:
        cmd (list[str]): Command and arguments.

    Returns:
        str: Standard output, stripped.

    Raises:
        None
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed read-only commands
            cmd, capture_output=True, text=True, check=False, timeout=10
        ).stdout.strip()
    except OSError:
        return ""


def host_context(data_dir: str) -> dict[str, object]:
    """Collect the blueprint 23.3 context: revision, host, storage, and versions.

    Args:
        data_dir (str): Directory holding the benchmark databases.

    Returns:
        dict[str, object]: JSON-serializable facts about this run.

    Raises:
        None
    """
    cpu = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        platform.processor(),
    )
    mem_kib = int(Path("/proc/meminfo").read_text().split()[1])
    conn = sqlite3.connect(":memory:")
    return {
        "source_revision": _run(["git", "rev-parse", "--short", "HEAD"]),
        "source_dirty": bool(_run(["git", "status", "--porcelain", "--untracked-files=no"])),
        "cpu": cpu,
        "cpus": os.cpu_count(),
        "memory_gib": round(mem_kib / 2**20, 1),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "sqlite_compile_options": [r[0] for r in conn.execute("PRAGMA compile_options")],
        "attach_limit": conn.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED),
        "storage": _run(["findmnt", "-n", "-o", "SOURCE,FSTYPE,OPTIONS", "-T", data_dir]),
        "disk": _run(["lsblk", "-dn", "-o", "NAME,ROTA,SIZE,MODEL"]),
    }
