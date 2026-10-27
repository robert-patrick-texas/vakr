# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""The ``mrtg`` profile: MRTG's targets and recent data for the parallel run (FIELD-002).

During the parallel run the development side compares the application's
5-minute traffic averages with MRTG's (MVP-ACC-03). This profile records,
for every ``Target[]``: what it graphs (``ifIndex``, ``ifName``, ``ifDescr``,
address, MAC, or something not comparable), the device's aliases, the
counter width, ``MaxBytes``/``AbsMax``, ``Options``, and MRTG's own data for
the comparison window. MRTG's native ``.log`` files keep 5-minute rows for
only about 50 hours before folding them into 30-minute rows, so the kit
must run at least every two days during the run; with ``LogFormat:
rrdtool`` the finest archive reaches back about 66 hours and the kit uses
``rrdtool fetch``.

Members: ``mrtg/targets.csv`` (a table, pseudonymized column by column),
``mrtg/summary.json``, ``mrtg/logs/<key>.log`` (the counter line and the
rows of the window, as MRTG wrote them), and ``mrtg/rrd/<key>.info.txt``
and ``.fetch.txt``.

Dependencies:
    fieldkit_mrtgcfg: Parsed configuration (read during preflight).
    fieldkit_hosts, fieldkit_rrd: Aliases and RRD reads.

Safety:
    Raw configuration never enters the bundle; communities were already
    handed to the bundle writer for redaction. Log files are read with a
    size cap, and nothing on the host is changed.

Example:
    >>> trim_log("1793091603 10 20\n1793091603 1 2 3 4\n1793091300 5 6 7 8\n", 1793091400)
    '1793091603 10 20\n1793091603 1 2 3 4\n'
"""

import csv
import io
import socket
from pathlib import Path
from typing import Any

from fieldkit_context import Context
from fieldkit_hosts import host_aliases, resolve_all
from fieldkit_mrtgmodel import MrtgConfig, MrtgTarget, interval_seconds
from fieldkit_rrd import capture_rrd, compare_window, rrdtool_version

LOG_MAX_BYTES = 4 * 1024 * 1024
"""Largest ``.log`` file read; a normal one is about 100 KB."""
COLUMNS = ("key", "target", "kind", "reversed", "ifindex", "ifname", "ifdescr", "ip", "mac",
           "host", "aliases", "port", "snmp_version", "counter_bits", "maxbytes", "maxbytes1",
           "maxbytes2", "absmax", "options", "interval_s", "logformat", "data", "comparable",
           "note")  # fmt: skip
NOT_COMPARABLE = {
    "type": "selected by ifType; more than one interface may match",
    "oid": "not one interface's octet counters",
    "expression": "arithmetic over several targets",
    "script": "values come from a script",
    "invalid": "the Target value did not parse",
}
COUNTER_OPTIONS = frozenset({"gauge", "absolute"})


def trim_log(text: str, start: int) -> str:
    """Keep a ``.log`` file's counter line and the rows from ``start`` on.

    Args:
        text: The log as MRTG wrote it (newest row first).
        start: Window start, epoch seconds.

    Returns:
        str: The first line and every later row whose timestamp is at least ``start``.

    Raises:
        None
    """
    lines = text.splitlines()
    kept = lines[:1]
    for line in lines[1:]:
        stamp = line.split(" ", 1)[0]
        if not stamp.isdigit() or int(stamp) < start:
            break
        kept.append(line)
    return "".join(f"{line}\n" for line in kept)


def data_path(cfg: MrtgConfig, target: MrtgTarget) -> Path | None:
    """Find a target's ``.log`` or ``.rrd`` file.

    Args:
        cfg: The configuration.
        target: The target.

    Returns:
        Path | None: The file (MRTG lower-cases names; the configured case is
        tried too), or ``None`` without ``WorkDir`` or ``LogDir``.

    Raises:
        None
    """
    base = cfg.globals.get("logdir") or cfg.globals.get("workdir")
    if not base:
        return None
    directory = Path(base) / target.values.get("directory", "").strip()
    suffix = ".rrd" if cfg.globals.get("logformat", "").lower() == "rrdtool" else ".log"
    candidates = [directory / f"{name}{suffix}" for name in (target.name.lower(), target.name)]
    return next((p for p in candidates if p.is_file()), candidates[0])


def _note(target: MrtgTarget) -> str:
    """Say why a target cannot be compared, or ``""`` when it can.

    Args:
        target: The target.

    Returns:
        str: A fixed explanation.

    Raises:
        None
    """
    if target.spec.kind in NOT_COMPARABLE:
        return NOT_COMPARABLE[target.spec.kind]
    if target.options() & COUNTER_OPTIONS:
        return "gauge or absolute values, not interface counters"
    return "" if target.spec.comparable else "no host"


def _capture(ctx: Context, cfg: MrtgConfig, target: MrtgTarget, key: str,
             window: tuple[int, int]) -> str:  # fmt: skip
    """Store one target's data for the window.

    Args:
        ctx: Run context.
        cfg: The configuration.
        target: The target.
        key: Its key, such as ``t0001``.
        window: Start and end epoch seconds.

    Returns:
        str: The data member's path, or ``""``.

    Raises:
        None
    """
    path = data_path(cfg, target)
    if path is None:
        return ""
    if path.suffix == ".rrd":
        return capture_rrd(ctx, ctx.args.rrdtool, path, f"mrtg/rrd/{key}", window)
    member = f"mrtg/logs/{key}.log"
    with ctx.steps.step(f"mrtg.{key}.log") as record:
        if not path.is_file():
            record["error"] = "no .log file in WorkDir/LogDir (has MRTG run since it was added?)"
            return ""
        if path.stat().st_size > LOG_MAX_BYTES:
            record["error"] = f"larger than {LOG_MAX_BYTES} bytes; not read"
            return ""
        ctx.bundle.add_text(member, trim_log(path.read_text(errors="replace"), window[0]))
        return member
    return ""


def _row(key: str, target: MrtgTarget, aliases: list[str], cfg: MrtgConfig,
         data: str, note: str) -> list[Any]:  # fmt: skip
    """Build one ``targets.csv`` row.

    Args:
        key: Target key.
        target: The target.
        aliases: The device's aliases.
        cfg: The configuration.
        data: Data member path.
        note: Why it is not comparable, or ``""``.

    Returns:
        list[Any]: Cells in ``COLUMNS`` order.

    Raises:
        None
    """
    spec, values = target.spec, target.values
    keys = {k: spec.key if spec.kind == k else "" for k in ("ifindex", "ifname", "ifdescr", "ip",
                                                            "mac")}  # fmt: skip
    return [key, target.name, spec.kind, "yes" if spec.reversed else "no", *keys.values(),
            spec.host, " ".join(aliases), spec.port, spec.version or "1", spec.counter_bits or "",
            values.get("maxbytes", ""), values.get("maxbytes1", ""), values.get("maxbytes2", ""),
            values.get("absmax", ""), ",".join(sorted(target.options())),
            interval_seconds(cfg.globals.get("interval", "")),
            cfg.globals.get("logformat", "rateup") or "rateup", data,
            "no" if note else "yes", note]  # fmt: skip


def collect_mrtg(ctx: Context) -> None:
    """Run the ``mrtg`` profile.

    Args:
        ctx: Run context; ``ctx.parsed["mrtg"]`` holds the preflight parse.

    Returns:
        None

    Raises:
        None
    """
    cfg: MrtgConfig = ctx.parsed["mrtg"]
    targets = cfg.targets[: ctx.args.max_targets]
    window = compare_window(ctx.args.compare_hours)
    resolved = resolve_all({t.spec.host for t in targets if t.spec.host})
    rows, wanted = [], ctx.parsed.setdefault("acceptance_targets", [])
    for number, target in enumerate(targets, start=1):
        if ctx.steps.expired():
            break
        key, note = f"t{number:04d}", _note(target)
        aliases = host_aliases(target.spec.host, resolved.get(target.spec.host, []))
        data = "" if note else _capture(ctx, cfg, target, key, window)
        rows.append(_row(key, target, aliases, cfg, data, note))
        if not note:
            wanted.append(
                {"kind": "interface", "device": aliases, target.spec.kind: target.spec.key}
            )
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows([COLUMNS, *rows])
    ctx.bundle.add_text("mrtg/targets.csv", buffer.getvalue(), kind="table")
    rrd = cfg.globals.get("logformat", "").lower() == "rrdtool"
    ctx.bundle.add_json("mrtg/summary.json", {
        "host": socket.gethostname(), "configs": cfg.files, "targets": len(cfg.targets),
        "listed": len(rows), "comparable": sum(1 for r in rows if r[-2] == "yes"),
        "window": {"start": window[0], "end": window[1]}, "notes": cfg.notes[:200],
        "logformat": "rrdtool" if rrd else "rateup",
        "interval_s": interval_seconds(cfg.globals.get("interval", "")),
        "rrdtool": rrdtool_version(ctx.args.rrdtool) if rrd else None,
    })  # fmt: skip
