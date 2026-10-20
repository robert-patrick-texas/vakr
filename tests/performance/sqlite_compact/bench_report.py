r"""Turn ADR-021 result files into the Markdown tables of the performance report.

Reads every ``*.json`` that ``bench_plan`` wrote and prints the ingest
sweep table, per-layout size, query latency by data volume, rollup,
retention, concurrency and rollup-store tables. It also writes a compact
JSON digest (``--digest``) that is committed next to the report, so the
published numbers can be traced back without the multi-gigabyte inputs.

Dependencies:
    json, pathlib: Result files.

Safety:
    Read-only apart from the optional digest file.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_report \
        --results /data/bench/results --digest docs/performance/adr021-digest.json
"""

import argparse
import json
from pathlib import Path

MIB = 2**20


def _load(results: Path) -> dict[str, dict]:
    """Load every result file, keyed by file stem.

    Args:
        results (Path): Directory of result files.

    Returns:
        dict[str, dict]: Stem to parsed JSON.

    Raises:
        OSError: If a file cannot be read.
        json.JSONDecodeError: If a file is not valid JSON.
    """
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(results.glob("*.json"))
    }


def sweep_table(data: dict[str, dict]) -> list[str]:
    """Format the two-hour ingest sweeps.

    Args:
        data (dict[str, dict]): Loaded results.

    Returns:
        list[str]: Markdown lines.

    Raises:
        None
    """
    lines = [
        "| Run | rows/s | commit p50 / p95 / max ms | move p50 / max ms | written MiB/h | "
        "bytes/row | replay inserted, rows/s |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in data.items():
        if not name.startswith("sweep-"):
            continue
        d, mv = r["days"][0], r["days"][0].get("move") or {}
        c = d["commit"]
        moves = f"{mv['p50_ms']:.0f} / {mv['max_ms']:.0f}" if mv else "-"
        rep = r.get("replay", {})
        lines.append(
            f"| {name[6:]} | {d['rows_per_s']:,} | {c['p50_ms']:.1f} / {c['p95_ms']:.1f} / "
            f"{c['max_ms']:.0f} | {moves} | {d['wchar'] / MIB / 2:,.0f} | "
            f"{r['final']['db'] / d['rows']:.1f} | {rep.get('inserted')}, "
            f"{rep.get('rows_per_s', 0):,} |"
        )
    return lines


def query_table(name: str, r: dict) -> list[str]:
    """Format one scale run's query latencies at each data volume.

    Args:
        name (str): Experiment name.
        r (dict): Its ingest result.

    Returns:
        list[str]: Markdown lines.

    Raises:
        None
    """
    days = sorted(r["queries"], key=int)
    head = "| Query | samples | " + " | ".join(f"{d} d warm p50/p95 | cold p50" for d in days)
    lines = [f"**{name}**", "", head + " |", "|---|---|" + "---|---|" * len(days)]
    for q in r["queries"][days[-1]]:
        cells = []
        for d in days:
            e = r["queries"][d].get(q)
            if e is None:
                cells += ["-", "-"]
                continue
            cold = e.get("cold", {}).get("p50_ms")
            cells += [
                f"{e['warm']['p50_ms']:.1f} / {e['warm']['p95_ms']:.1f}",
                f"{cold:.1f}" if cold is not None else "-",
            ]
        lines.append(
            f"| {q} | {r['queries'][days[-1]][q]['samples']:,} | " + " | ".join(cells) + " |"
        )
    return lines


def size_lines(name: str, r: dict) -> list[str]:
    """Format bytes per row and per-day growth for a scale run.

    Args:
        name (str): Experiment name.
        r (dict): Its ingest result.

    Returns:
        list[str]: Markdown lines.

    Raises:
        None
    """
    rows = sum(d["rows"] for d in r["days"])
    lines = [
        f"**{name}**: {len(r['days'])} days, {rows:,} rows, file "
        f"{r['final']['db'] / 2**30:.2f} GiB, {r['final']['db'] / rows:.1f} bytes/row; "
        f"elapsed {r['elapsed_s'] / 60:.0f} min",
        "",
        # dbstat counts leaf cells only; WITHOUT ROWID tables also keep rows in
        # interior cells, so bytes per row is computed in the report from row counts.
        "| Table family | pages | MiB | fill |",
        "|---|---|---|---|",
    ]
    for fam, s in sorted(r["dbstat"].items(), key=lambda kv: -kv[1]["bytes"]):
        if s["bytes"] < MIB:
            continue
        fill = 1 - s["unused"] / s["bytes"] if s["bytes"] else 0
        lines.append(f"| {fam} | {s['pages']:,} | {s['bytes'] / MIB:,.0f} | {fill:.2f} |")
    return lines


def digest(data: dict[str, dict]) -> dict[str, object]:
    """Reduce results to the numbers the report cites, dropping bulky fields.

    Args:
        data (dict[str, dict]): Loaded results.

    Returns:
        dict[str, object]: Per experiment, everything except host compile options.

    Raises:
        None
    """
    out: dict[str, object] = {}
    for name, r in data.items():
        slim = {k: v for k, v in r.items() if k != "host"}
        if isinstance(slim.get("args"), dict):
            # Result paths are host-local and say nothing about the measurement.
            slim["args"] = {k: v for k, v in slim["args"].items() if k != "out"}
        if "host" in r:
            slim["host"] = {k: v for k, v in r["host"].items() if k != "sqlite_compile_options"}
        out[name] = slim
    return out


def main(argv: list[str] | None = None) -> int:
    """Print the report tables and optionally write the digest.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: Process exit status.

    Raises:
        OSError: If results cannot be read or the digest written.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--digest", type=Path)
    a = ap.parse_args(argv)
    data = _load(a.results)
    out = ["## Ingest sweeps", "", *sweep_table(data), ""]
    for name in ("scale-CH.0", "scale-B.0", "scale-A.0"):
        if name in data:
            out += [
                f"## {name}",
                "",
                *size_lines(name, data[name]),
                "",
                *query_table(name, data[name]),
                "",
            ]
    for name, r in data.items():
        if name.split(".")[0] in (
            "scale-CH",
            "scale-B",
            "scale-A",
            "rollup-store",
        ) and not name.endswith(".0"):
            out += [
                f"## {name}",
                "",
                "```json",
                json.dumps({k: v for k, v in r.items() if k not in ("host", "dbstat")}, indent=1),
                "```",
                "",
            ]
    print("\n".join(out))
    if a.digest:
        a.digest.write_text(json.dumps(digest(data), indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
