r"""The full ADR-021 benchmark plan: every published number comes from a named step here.

Each experiment is a list of steps; a step runs one ``bench_*`` module as a
subprocess with its arguments plus ``--data-dir`` and ``--out``. Results
land in ``<results>/<experiment>[.<step>].json``; an experiment whose
results exist is skipped unless ``--force`` is given, so an interrupted
plan resumes. Databases are deleted when an experiment ends, which keeps
the data directory under the 15 GB budget.

Dependencies:
    subprocess, sys: Runs each step with the current interpreter.

Safety:
    Deletes only ``*.db`` files (and their WAL and SHM) it created under
    ``--data-dir``.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_plan \
        --data-dir /data/bench --results /data/bench/results --only sweep-C-10-NORMAL
"""

import argparse
import subprocess
import sys
from pathlib import Path

PKG = "tests.performance.sqlite_compact"
SWEEP_MIN = "120"


def _sweeps() -> dict[str, list[list[str]]]:
    """Two-hour ingest sweeps: layout x flush interval x ``synchronous``.

    Args:
        None

    Returns:
        dict[str, list[list[str]]]: Experiment name to its single step.

    Raises:
        None
    """
    out: dict[str, list[list[str]]] = {}
    layouts = {
        "A": ["--candidate", "A"],
        "B": ["--candidate", "B"],
        "C": ["--candidate", "C"],
        "CH5": ["--candidate", "C", "--hot", "--move-minutes", "5"],
        "CH15": ["--candidate", "C", "--hot", "--move-minutes", "15"],
    }
    for name, lay in layouts.items():
        for batch in ("60", "10"):
            for sync in ("NORMAL", "FULL"):
                out[f"sweep-{name}-{batch}-{sync}"] = [
                    [
                        "bench_ingest",
                        *lay,
                        "--minutes",
                        SWEEP_MIN,
                        "--batch-seconds",
                        batch,
                        "--sync",
                        sync,
                        "--replay-minutes",
                        "10",
                    ]
                ]
    variants = {
        "ckpt10k": ["--wal-autocheckpoint", "10000"],
        "uuid": ["--uuid-index"],
        "round": ["--round-rates"],
        "proj": ["--projection"],
        "page8k": ["--page-size", "8192"],
    }
    for vname, extra in variants.items():
        for name in ("C", "CH15"):
            out[f"sweep-{name}-10-NORMAL-{vname}"] = [
                [
                    "bench_ingest",
                    *layouts[name],
                    "--minutes",
                    SWEEP_MIN,
                    "--batch-seconds",
                    "10",
                    "--replay-minutes",
                    "10",
                    *extra,
                ]
            ]
    return out


def _scale() -> dict[str, list[list[str]]]:
    """Full-day runs: size and query latency as data grows, then rollups and retention.

    ``{db}`` in a step is replaced with the experiment's database path. The
    recommended layout (C with hot staging) is built from the recommended
    settings. A and B use one-minute batches and a 10,000-page checkpoint
    only to shorten the build; their ingest cost comes from the sweeps.

    Args:
        None

    Returns:
        dict[str, list[list[str]]]: Experiment name to its steps.

    Raises:
        None
    """
    ch = ["--candidate", "C", "--hot"]
    fast = [
        "--batch-seconds",
        "60",
        "--wal-autocheckpoint",
        "10000",
        "--auto-vacuum",
        "INCREMENTAL",
        "--keep",
    ]
    return {
        "scale-CH": [
            [
                "bench_ingest",
                *ch,
                "--days",
                "16",
                "--batch-seconds",
                "10",
                "--sync",
                "FULL",
                "--move-minutes",
                "15",
                "--query-at",
                "1",
                "2",
                "4",
                "8",
                "16",
                "--replay-minutes",
                "30",
                "--keep",
            ],
            ["bench_rollup", "--db", "{db}", *ch, "--day", "15"],
            ["bench_concurrency", "--db", "{db}", *ch],
            ["bench_retention", "--db", "{db}", *ch],
            ["bench_concurrency", "--db", "{db}", *ch, "--truncate-after-move"],
        ],
        "scale-B": [
            [
                "bench_ingest",
                "--candidate",
                "B",
                "--days",
                "8",
                *fast,
                "--query-at",
                "1",
                "2",
                "4",
                "8",
            ],
            ["bench_retention", "--db", "{db}", "--candidate", "B"],
        ],
        "scale-A": [
            ["bench_ingest", "--candidate", "A", "--days", "4", *fast, "--query-at", "1", "2", "4"],
            ["bench_retention", "--db", "{db}", "--candidate", "A"],
        ],
        "rollup-store": [
            ["bench_rollup_store", "--days", "400", "--round", "--name", "rollup-store"]
        ],
    }


PLAN: dict[str, list[list[str]]] = _sweeps() | _scale()


def run_step(step: list[str], name: str, data_dir: Path, out: Path) -> int:
    """Run one benchmark module as a subprocess.

    Args:
        step (list[str]): Module name followed by its arguments.
        name (str): Database name for the step (``--name``) where accepted.
        data_dir (Path): Database directory.
        out (Path): JSON result path.

    Returns:
        int: The subprocess exit status.

    Raises:
        OSError: If the interpreter cannot be started.
    """
    args = [str(data_dir / f"{name}.db") if x == "{db}" else x for x in step[1:]]
    cmd = [
        sys.executable,
        "-m",
        f"{PKG}.{step[0]}",
        *args,
        "--data-dir",
        str(data_dir),
        "--out",
        str(out),
    ]
    if step[0] == "bench_ingest" and "--name" not in step:
        cmd += ["--name", name]
    print("+", " ".join(cmd[2:]), flush=True)
    return subprocess.run(cmd, check=False).returncode  # noqa: S603


def main(argv: list[str] | None = None) -> int:
    """Run the selected experiments in plan order.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: 0 when every step succeeded, otherwise 1.

    Raises:
        OSError: If a directory cannot be created.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="experiment names or prefixes")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    names = [n for n in PLAN if not a.only or any(n.startswith(p) for p in a.only)]
    if a.list:
        print("\n".join(names))
        return 0
    a.data_dir.mkdir(parents=True, exist_ok=True)
    a.results.mkdir(parents=True, exist_ok=True)
    failed = False
    for name in names:
        steps = PLAN[name]
        for i, step in enumerate(steps):
            out = a.results / (f"{name}.json" if len(steps) == 1 else f"{name}.{i}.json")
            if out.exists() and not a.force:
                continue
            failed |= run_step(step, name, a.data_dir, out) != 0
        for db in a.data_dir.glob(f"{name}*.db*"):
            db.unlink()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
