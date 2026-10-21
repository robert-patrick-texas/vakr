# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Check Node.js: the version pin, the ``engines.node`` floor, and the installed binary.

A project that builds a front end with npm pins Node in three places that
drift apart: a version file read by CI (``.node-version``), the
``engines.node`` range in ``package.json`` that npm enforces, and the
binary actually installed on the build host. Each is compared with the
newest Long Term Support release from the nodejs.org index (TOOLS-001,
ADR-035 #18). Build hosts follow LTS lines, so a Current (odd-numbered or
not yet promoted) release never counts as the latest; the newest Current
release is named in the row's detail for information only.

Precision follows the pin: ``24`` in the version file is current while
24.x is the newest LTS line; the ``engines`` floor is compared by major
line, because a floor of 24.22.0 says which line is supported, not which
patch to install; the installed binary is compared exactly.

Dependencies:
    json, re: Read ``package.json`` and its ``engines`` range.
    updates_checks: Shared task model and row helpers.
    updates_versions: Version parsing.

Safety:
    Read-only. ``node --version`` runs from the config's argument list,
    without a shell.

Example:
    >>> node_latest([{"version": "v26.1.0", "lts": False},
    ...              {"version": "v24.22.1", "lts": "Krypton"}]).version.text
    '24.22.1'
    >>> engines_floor_text(">=24.22.0 <25")
    '24.22.0'
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import updates_local as local
from updates_checks import Context, attempt, command_version, make_row, read_local
from updates_fetch import FetchError
from updates_report import Result
from updates_sources import Latest
from updates_versions import latest_stable

_FLOOR = re.compile(r"^\s*(?:>=|\^|~|=)?\s*v?(\d+(?:\.\d+)*)")
_LINE = 1  # Node release lines are majors (24.x); engines floors compare at this precision


def node_latest(data: Any, lts: bool = True) -> Latest:
    """Pick the newest LTS (or, with ``lts=False``, Current) release from the index.

    Args:
        data: Decoded ``https://nodejs.org/dist/index.json`` list.
        lts: True for releases whose ``lts`` field names a codename.

    Returns:
        Latest: The release and its nodejs.org announcement.

    Raises:
        FetchError: If the index lists no matching stable release.
    """
    # ``lts`` is false for Current releases and a codename ("Krypton") for LTS ones.
    tags = [e["version"] for e in data if e.get("lts") or not lts]
    best = latest_stable(tags)
    if best is None:
        raise FetchError(f"nodejs.org: no {'LTS' if lts else 'stable'} release found")
    return Latest(best, f"https://nodejs.org/en/blog/release/{best.text}")


def engines_floor_text(spec: str) -> str | None:
    """Return the lowest version an ``engines.node`` range admits.

    Args:
        spec: A range such as ``>=24.22.0 <25``, ``^24.1.0``, or ``24.x``.

    Returns:
        str | None: The floor, such as ``24.22.0``, or None for an
        unbounded range such as ``*``.

    Raises:
        None
    """
    match = _FLOOR.match(spec)
    return match[1] if match else None


def engines_floor(package_json: Path) -> str | None:
    """Read the ``engines.node`` floor of a ``package.json``.

    Args:
        package_json: Path to the file.

    Returns:
        str | None: The floor, or None when the range has no lower bound.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If it is not JSON or declares no ``engines.node``.
    """
    data = json.loads(package_json.read_text(encoding="utf-8"))
    spec = (data.get("engines") or {}).get("node")
    if not spec:
        raise ValueError(f"no engines.node in {package_json.name}")
    return engines_floor_text(str(spec))


def _current_note(ctx: Context) -> str:
    """Name the newest Current release for the row detail, if it is known.

    Args:
        ctx: Task context.

    Returns:
        str: ``newest Current (not LTS): v26.12.1``, or empty when unavailable.

    Raises:
        None
    """
    found, _ = attempt(lambda: ctx.upstream.node(lambda d: node_latest(d, lts=False), "node:cur"))
    return f"newest Current (not LTS): {found.version.text}" if found else ""


def check_node(ctx: Context, cfg: Mapping[str, Any]) -> list[Result]:
    """Compare the Node pin, the ``engines`` floor, and the binary with the newest LTS.

    Args:
        ctx: Task context.
        cfg: The ``[node]`` table.

    Returns:
        list[Result]: Rows for the version file, ``engines.node``, and the
        installed binary, as configured.

    Raises:
        None
    """
    latest = attempt(lambda: ctx.upstream.node(node_latest, "node:lts"))
    note, rows = _current_note(ctx), []
    if name := cfg.get("version_file"):
        pin = ctx.root / name
        current = read_local(lambda: local.read_version_file(pin))
        rows.append(make_row(f"Node ({name})", "node", current, latest, note=note))
    if name := cfg.get("engines"):
        package = ctx.root / name
        current = read_local(lambda: engines_floor(package), "engines.node has no lower bound")
        item = f"Node (engines.node in {name})"
        rows.append(make_row(item, "node", current, latest, precision=_LINE, note=note))
    if argv := cfg.get("binary"):
        current = read_local(lambda: command_version(ctx, argv))
        rows.append(make_row("Node (installed)", "node", current, latest, note=note))
    return rows
