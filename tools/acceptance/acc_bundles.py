# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Load every extracted field bundle that holds parallel-run data, and merge them.

The operator runs the field kit daily during the parallel run, possibly on
two hosts: the one running MRTG and SmokePing (profiles ``mrtg`` and
``smokeping``) and the application's node (profile ``acceptance``), or one
host doing both. ``field_inbox.py`` extracts each bundle into
``inbox/<bundle-id>/``. This module reads them all, merges overlapping
days (a window or sample counts once), puts MRTG's windows in the
application's in/out orientation (a reversed target swaps them, and its
limits), and records each tool's vantage host. Pseudonymized bundles join
only when they share one pseudonym key; a mix is reported.

Dependencies:
    acc_sources, acc_vakr: File formats.

Safety:
    Reads at most ``max_bundles`` bundle directories, each only through
    the members the field kit writes.

Example:
    >>> limit_bps({"maxbytes": "125000000", "absmax": ""}, "1")
    1000000000.0
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from acc_sources import Round, Window, mrtg_windows, parse_log, smokeping_rounds
from acc_vakr import Export, read_export, read_table


@dataclass
class MrtgTarget:
    """One MRTG target across bundles, in the application's orientation.

    Attributes:
        name: Target name (pseudonymized in pseudonymized bundles).
        kind: ``ifindex``, ``ifname``, ``ifdescr``, ``ip``, ``mac``, or a
            non-comparable kind.
        key: The selector's value.
        aliases: The device's aliases.
        counter_bits: 64 or 32.
        limit_in: Bits/s above which MRTG discards an inbound value, or ``None``.
        limit_out: The same for outbound.
        unknaszero: MRTG logs unknown polls as 0.
        rateup: True for ``.log`` files (whole bytes per second).
        comparable: Whether the field kit found it comparable.
        note: Why not.
        vantage: Host MRTG ran on.
        windows: ``(in_bps, out_bps)`` windows by end time.
        last_run: Latest MRTG run seen for it.
    """

    name: str
    kind: str
    key: str
    aliases: set[str]
    counter_bits: int
    limit_in: float | None
    limit_out: float | None
    unknaszero: bool
    rateup: bool
    comparable: bool
    note: str
    vantage: str
    windows: dict[int, Window] = field(default_factory=dict)
    last_run: int | None = None


@dataclass
class SmokeSeries:
    """One SmokePing target as measured from one vantage point.

    Attributes:
        path: Target path.
        host: The probed host.
        aliases: Its aliases.
        pings: Pings per round.
        comparable: Whether the field kit found it comparable.
        note: Why not.
        vantage: The master's host, or the slave's name.
        rounds: Rounds by end time.
    """

    path: str
    host: str
    aliases: set[str]
    pings: int
    comparable: bool
    note: str
    vantage: str
    rounds: dict[int, Round] = field(default_factory=dict)


@dataclass
class Inbox:
    """Everything read from the bundles.

    Attributes:
        bundles: ``(bundle id, created, profiles)`` of each bundle used.
        mrtg: MRTG targets by name.
        smokeping: SmokePing series by ``(path, vantage)``.
        export: The application's export.
        key_ids: Pseudonym key ids seen (``""`` for clear bundles).
        problems: Things the report must mention.
    """

    bundles: list[tuple[str, str, list[str]]] = field(default_factory=list)
    mrtg: dict[str, MrtgTarget] = field(default_factory=dict)
    smokeping: dict[tuple[str, str], SmokeSeries] = field(default_factory=dict)
    export: Export = field(default_factory=Export)
    key_ids: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)


def limit_bps(row: dict[str, str], direction: str) -> float | None:
    """Return the rate above which MRTG ignores a value, in bits per second.

    Args:
        row: A ``targets.csv`` row.
        direction: ``"1"`` (MRTG's in) or ``"2"`` (MRTG's out).

    Returns:
        float | None: ``AbsMax`` if set, else ``MaxBytes1``/``MaxBytes2`` or ``MaxBytes``, x8.

    Raises:
        None
    """
    text = row.get("absmax") or row.get(f"maxbytes{direction}") or row.get("maxbytes") or ""
    try:
        return float(text) * 8
    except ValueError:
        return None


def _mrtg(inbox: Inbox, bundle: Path, step: int) -> None:
    """Merge one bundle's MRTG members.

    Args:
        inbox: The merged inbox.
        bundle: Bundle directory.
        step: Window length kept.

    Returns:
        None

    Raises:
        ValueError: If a member is malformed.
    """
    summary = json.loads((bundle / "mrtg" / "summary.json").read_text(encoding="utf-8"))
    for row in read_table(bundle / "mrtg" / "targets.csv"):
        flip = row["reversed"] == "yes"
        limits = (limit_bps(row, "1"), limit_bps(row, "2"))
        target = inbox.mrtg.setdefault(row["target"], MrtgTarget(
            row["target"], row["kind"], row.get(row["kind"], "") or "",
            set(row["aliases"].split()), int(row["counter_bits"] or 0),
            limits[1] if flip else limits[0], limits[0] if flip else limits[1],
            "unknaszero" in row["options"].split(","), row["logformat"] != "rrdtool",
            row["comparable"] == "yes", row["note"], summary.get("host", "")))  # fmt: skip
        data = bundle / row["data"] if row["data"] else None
        if data is None or not data.is_file():
            continue
        if data.suffix == ".log":
            parsed = parse_log(data.read_text(encoding="utf-8"), step)
        else:
            info = data.with_name(data.name.replace(".fetch.txt", ".info.txt"))
            meta = info.read_text(encoding="utf-8") if info.is_file() else ""
            parsed = mrtg_windows(data.read_text(encoding="utf-8"), meta, step)
        for w in parsed.windows:
            values = (w.values[1], w.values[0]) if flip else w.values
            target.windows[w.end] = Window(w.end, w.step, values)
        if parsed.last_run and (target.last_run or 0) < parsed.last_run:
            target.last_run = parsed.last_run


def _smokeping(inbox: Inbox, bundle: Path) -> None:
    """Merge one bundle's SmokePing members.

    Args:
        inbox: The merged inbox.
        bundle: Bundle directory.

    Returns:
        None

    Raises:
        ValueError: If a member is malformed.
    """
    summary = json.loads((bundle / "smokeping" / "summary.json").read_text(encoding="utf-8"))
    for row in read_table(bundle / "smokeping" / "targets.csv"):
        vantage = row["slave"] or summary.get("host", "")
        series = inbox.smokeping.setdefault((row["path"], vantage), SmokeSeries(
            row["path"], row["host"], set(row["aliases"].split()), int(row["pings"] or 0),
            row["comparable"] == "yes", row["note"], vantage))  # fmt: skip
        data = bundle / row["data"] if row["data"] else None
        if data is not None and data.is_file():
            for r in smokeping_rounds(data.read_text(encoding="utf-8"), int(row["step"] or 300)):
                series.rounds[r.end] = r


def load_inbox(root: Path, step: int, max_bundles: int, only: list[str] | None = None) -> Inbox:
    """Read and merge every bundle under ``root`` that holds parallel-run members.

    Args:
        root: The inbox directory (``inbox/``).
        step: MRTG window length kept.
        max_bundles: Most bundle directories read.
        only: Bundle ids to read; ``None`` reads all.

    Returns:
        Inbox: The merged data.

    Raises:
        None
    """
    inbox = Inbox()
    found = sorted(p.parent for p in root.glob("*/manifest.json"))
    if only is not None:
        found = [p for p in found if p.name in only]
    if len(found) > max_bundles:
        inbox.problems.append(f"{len(found)} bundles found; only the newest {max_bundles} read")
        found = found[-max_bundles:]
    for bundle in found:
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        parts = [n for n in ("mrtg", "smokeping", "acceptance") if (bundle / n).is_dir()]
        if not parts:
            continue
        pseudo = manifest.get("pseudonymization", {})
        inbox.key_ids.add((pseudo.get("key_id") or "") if pseudo.get("enabled") else "")
        inbox.bundles.append((bundle.name, manifest.get("created_utc", ""), parts))
        try:
            if "mrtg" in parts:
                _mrtg(inbox, bundle, step)
            if "smokeping" in parts:
                _smokeping(inbox, bundle)
            if "acceptance" in parts and (bundle / "acceptance" / "export.json").is_file():
                read_export(bundle / "acceptance", inbox.export)
        except (ValueError, KeyError, OSError) as exc:
            inbox.problems.append(f"{bundle.name}: unreadable member ({type(exc).__name__}: {exc})")
    if len(inbox.key_ids) > 1:
        inbox.problems.append("the bundles use different pseudonym keys (or mix clear and "
                              "pseudonymized), so names cannot join across them")  # fmt: skip
    return inbox
