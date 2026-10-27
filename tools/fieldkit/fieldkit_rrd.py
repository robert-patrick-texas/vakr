# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Read RRD files with ``rrdtool`` for the ``mrtg`` and ``smokeping`` profiles.

MRTG (with ``LogFormat: rrdtool``) and SmokePing keep their data in RRD
files. The kit asks ``rrdtool info`` for the step, the data sources, and
the archives, then ``rrdtool fetch`` for the finest ``AVERAGE`` rows of the
comparison window. rrdtool picks one archive for a whole fetch, and a
window older than the finest archive reaches back would come out at a
coarser step, so the window is clipped to what the finest archive still
holds. Both outputs are stored as text, exactly as rrdtool prints them,
so the development side parses recorded output.

Dependencies:
    fieldkit_context: Run context, ``capture``, and the bundle.
    fieldkit_run: Bounded commands.

Safety:
    rrdtool runs from argument lists with a timeout and only reads. Only
    allow-listed ``info`` lines are kept (``step``, ``last_update``, data
    source and archive settings), never the ``filename`` line.

Example:
    >>> finest_average({"step": "300", "rra[0].cf": "AVERAGE", "rra[0].pdp_per_row": "1",
    ...                 "rra[0].rows": "800"})
    (300, 240000)
"""

import re
import time
from pathlib import Path

from fieldkit_context import Context
from fieldkit_run import run_command

RRD_TIMEOUT = 30.0
"""Seconds one ``rrdtool`` call may take."""
_INFO_KEEP = re.compile(
    r"^(step|last_update|ds\[[\w-]+\]\.(type|min|max|minimal_heartbeat)"
    r"|rra\[\d+\]\.(cf|pdp_per_row|rows))\s*=\s*(.*)$"
)


def compare_window(hours: float, step: int = 300, now: float | None = None) -> tuple[int, int]:
    """Return the comparison window, aligned to the step.

    Args:
        hours: Window length.
        step: Alignment in seconds.
        now: Current epoch seconds; ``None`` reads the clock.

    Returns:
        tuple[int, int]: Start and end epoch seconds, multiples of ``step``.

    Raises:
        None
    """
    end = int((time.time() if now is None else now) // step * step)
    return end - int(hours * 3600) // step * step, end


def finest_average(info: dict[str, str]) -> tuple[int, int]:
    """Find the finest ``AVERAGE`` archive's resolution and how far back it reaches.

    Args:
        info: Kept ``rrdtool info`` values.

    Returns:
        tuple[int, int]: Resolution and coverage in seconds; ``(step, 0)``
        when the file has no ``AVERAGE`` archive.

    Raises:
        None
    """
    step = int(float(info.get("step", "300")))
    best: tuple[int, int] | None = None
    for key, value in info.items():
        if key.endswith(".cf") and value.strip('"') == "AVERAGE":
            prefix = key[: -len(".cf")]
            per_row = int(float(info.get(f"{prefix}.pdp_per_row", "1")))
            rows = int(float(info.get(f"{prefix}.rows", "0")))
            if best is None or per_row * step < best[0]:
                best = (per_row * step, per_row * step * rows)
    return best or (step, 0)


def rrd_info(ctx: Context, rrdtool: str, path: Path, step: str, member: str) -> dict[str, str]:
    """Run ``rrdtool info`` and keep the allow-listed lines.

    Args:
        ctx: Run context.
        rrdtool: The rrdtool program.
        path: The RRD file.
        step: Step name for the manifest.
        member: Member path for the kept lines.

    Returns:
        dict[str, str]: Kept keys and values; empty when it failed.

    Raises:
        None
    """
    kept: dict[str, str] = {}
    with ctx.steps.step(step) as record:
        result = run_command([rrdtool, "info", str(path)], RRD_TIMEOUT)
        if not result.ok:
            record["error"] = result.error or f"exit status {result.returncode}"
            return kept
        for line in result.stdout.splitlines():
            if match := _INFO_KEEP.match(line.strip()):
                kept[match[1]] = match[4].strip()
        ctx.bundle.add_text(member, "".join(f"{k} = {v}\n" for k, v in kept.items()))
    return kept


def capture_rrd(ctx: Context, rrdtool: str, path: Path, base: str,
                window: tuple[int, int]) -> str:  # fmt: skip
    """Capture ``info`` and the finest ``AVERAGE`` rows of one RRD file.

    Args:
        ctx: Run context.
        rrdtool: The rrdtool program.
        path: The RRD file.
        base: Member path without suffix, such as ``mrtg/rrd/t0001``.
        window: Start and end epoch seconds.

    Returns:
        str: The fetch member's path, or ``""`` when nothing was fetched.

    Raises:
        None
    """
    info = rrd_info(ctx, rrdtool, path, f"{base}.info", f"{base}.info.txt")
    if not info:
        return ""
    resolution, coverage = finest_average(info)
    start, end = window
    last = int(float(info.get("last_update", end)))
    if coverage:
        start = max(start, (last - coverage) // resolution * resolution + resolution)
    argv = [rrdtool, "fetch", str(path), "AVERAGE", "--resolution", str(resolution),
            "--start", str(start), "--end", str(end)]  # fmt: skip
    status = ctx.capture(f"{base}.fetch", f"{base}.fetch.txt", argv, RRD_TIMEOUT)
    return f"{base}.fetch.txt" if status == 0 else ""


def rrdtool_version(rrdtool: str) -> str:
    """Return rrdtool's version line, or ``""`` when it is not installed.

    Args:
        rrdtool: The rrdtool program.

    Returns:
        str: Such as ``RRDtool 1.7.2``.

    Raises:
        None
    """
    result = run_command([rrdtool], RRD_TIMEOUT)
    first = (result.stdout or result.stderr).splitlines()[:1]
    match = re.match(r"(RRDtool\s+\S+)", first[0]) if first else None
    return match[1] if match else ""
