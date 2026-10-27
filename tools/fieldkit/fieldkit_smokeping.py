# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""The ``smokeping`` profile: SmokePing's targets and recent data for the parallel run.

During the parallel run the development side compares the application's
median RTT and loss with SmokePing's from the same vantage point
(MVP-ACC-04). This profile records every probed host with its probe, pings
per round, step, and the hosts that probe it (this host as the master,
and any slaves), and fetches each RRD's finest ``AVERAGE`` rows for the
comparison window: ``uptime``, ``loss`` (lost pings per round), ``median``
(seconds), and ``ping1`` .. ``pingN`` (sorted RTTs, in seconds). The
default Database keeps 5-minute rows for 3.5 days, so running the kit
daily keeps every row.

Members: ``smokeping/targets.csv`` (a table, pseudonymized column by
column), ``smokeping/summary.json``, and ``smokeping/rrd/<key>.info.txt``
and ``.fetch.txt``.

Dependencies:
    fieldkit_smokecfg: Parsed configuration (read during preflight).
    fieldkit_hosts, fieldkit_rrd: Aliases and RRD reads.

Safety:
    Only allow-listed settings were kept; probe passwords and secrets were
    handed to the bundle writer for redaction. Nothing on the host changes.

Example:
    >>> rrd_path("/var/lib/smokeping", "Core/r1", "west")
    PosixPath('/var/lib/smokeping/Core/r1~west.rrd')
"""

import csv
import io
import socket
from pathlib import Path
from typing import Any

from fieldkit_context import Context
from fieldkit_hosts import host_aliases, resolve_all
from fieldkit_rrd import capture_rrd, compare_window, rrdtool_version
from fieldkit_smokemodel import SmokeConfig, SmokeTarget

COLUMNS = ("key", "path", "menu", "title", "host", "aliases", "probe", "pings", "step", "slave",
           "data", "comparable", "note")  # fmt: skip


def rrd_path(datadir: str, path: str, slave: str = "") -> Path:
    """Return where SmokePing keeps a target's RRD (``~slave`` for a slave's).

    Args:
        datadir: The ``datadir`` setting.
        path: Target path in the tree.
        slave: Slave name, or ``""`` for the master.

    Returns:
        Path: The RRD file.

    Raises:
        None
    """
    return Path(datadir) / f"{path}{'~' + slave if slave else ''}.rrd"


def _series(target: SmokeTarget) -> list[str]:
    """List who probes a target: ``""`` for this master, then each slave.

    Args:
        target: The target.

    Returns:
        list[str]: Vantage points.

    Raises:
        None
    """
    return ([""] if target.master else []) + list(target.slaves)


def _note(cfg: SmokeConfig, target: SmokeTarget) -> str:
    """Say why a target cannot be compared, or ``""`` when it can.

    Args:
        cfg: The configuration.
        target: The target.

    Returns:
        str: A fixed explanation.

    Raises:
        None
    """
    if target.host.upper().startswith("DYNAMIC"):
        return "dynamic host"
    parent = cfg.probes.get(target.probe, {}).get("_parent", "")
    if not any(n.lower().startswith("fping") for n in (target.probe, parent)):
        return "not an FPing probe from this host"
    return "" if cfg.datadir else "no datadir"


def collect_smokeping(ctx: Context) -> None:
    """Run the ``smokeping`` profile.

    Args:
        ctx: Run context; ``ctx.parsed["smokeping"]`` holds the preflight parse.

    Returns:
        None

    Raises:
        None
    """
    cfg: SmokeConfig = ctx.parsed["smokeping"]
    targets = cfg.targets[: ctx.args.max_targets]
    window = compare_window(ctx.args.compare_hours, cfg.step)
    resolved = resolve_all({t.host for t in targets})
    rows: list[list[Any]] = []
    wanted = ctx.parsed.setdefault("acceptance_targets", [])
    for target in targets:
        aliases = host_aliases(target.host, resolved.get(target.host, []))
        note = _note(cfg, target)
        pings = cfg.probe_value(target.probe, "pings", cfg.pings)
        step = cfg.probe_value(target.probe, "step", cfg.step)
        for slave in _series(target):
            if ctx.steps.expired():
                break
            key = f"s{len(rows) + 1:04d}"
            path = rrd_path(cfg.datadir, target.path, slave)
            data = "" if note else capture_rrd(ctx, ctx.args.rrdtool, path,
                                               f"smokeping/rrd/{key}", window)  # fmt: skip
            rows.append([key, target.path, target.menu, target.title, target.host,
                         " ".join(aliases), target.probe, pings, step, slave, data,
                         "no" if note else "yes", note])  # fmt: skip
        if not note:
            wanted.append({"kind": "icmp", "device": aliases})
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows([COLUMNS, *rows])
    ctx.bundle.add_text("smokeping/targets.csv", buffer.getvalue(), kind="table")
    ctx.bundle.add_json("smokeping/summary.json", {
        "host": socket.gethostname(), "configs": cfg.files, "datadir": cfg.datadir,
        "step": cfg.step, "pings": cfg.pings, "targets": len(cfg.targets), "series": len(rows),
        "probes": sorted(cfg.probes), "window": {"start": window[0], "end": window[1]},
        "notes": cfg.notes[:200], "rrdtool": rrdtool_version(ctx.args.rrdtool),
    })  # fmt: skip
