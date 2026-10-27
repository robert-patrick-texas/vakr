# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Render check-updates results as a table or JSON and choose the exit status.

The release checklist (TOOLS-002) needs one readable list: what is
pinned, what is newest, whether to act, and where the release notes are.
The same results are available as JSON for scripts. The report is
informational by default and exits 0; ``--fail-on-outdated`` turns any
"update available" into exit 1. Items whose status could not be determined
("unknown", usually a network failure) never fail it, so an outage cannot
block everyday work; their reasons are listed under the table.

``--gate`` is the release gate (TOOLS-005): it also fails on "unknown" and
on a recorded exception that no longer matches the report, because a
release must not claim a check that never happened. "held" rows (TOOLS-004)
and "reference" rows pass it.

Dependencies:
    json, dataclasses: Standard library only.

Safety:
    Output contains versions, URLs, and error summaries only. Fetch errors
    are built without credentials (``gh`` keeps its token), so nothing
    secret can reach the report.

Example:
    >>> row = Result("ruff", "pypi", "0.17.1", "0.17.2", "update available")
    >>> exit_code([row], fail_on_outdated=True)
    1
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from updates_versions import CURRENT, HELD, REFERENCE, UNKNOWN, UPDATE

STATUSES = (CURRENT, UPDATE, HELD, REFERENCE, UNKNOWN)
_ALWAYS_COUNTED = (CURRENT, UPDATE, UNKNOWN)  # held and reference are shown when present
_HEADERS = ("ITEM", "CURRENT", "LATEST", "STATUS", "RELEASE NOTES")
_NAMED = 8  # items named in the gate line; the rest are counted


@dataclass(frozen=True, slots=True)
class Result:
    """One reported row.

    Attributes:
        item: What was checked, such as ``ruff`` or ``actions/checkout``.
        category: Source family: ``python``, ``go``, ``node``, ``tool``,
            ``pypi``, ``lockfile``, ``npm``, ``action``, or ``asset``.
        current: Version in use; empty when unknown or not pinned.
        latest: Latest stable upstream version; empty when unavailable.
        status: One of ``STATUSES``.
        notes: Release-notes URL for ``latest``.
        detail: Where the pin lives, why the status is unknown, or the
            exception that holds the item.
    """

    item: str
    category: str
    current: str
    latest: str
    status: str
    notes: str = ""
    detail: str = ""


def summarize(results: Sequence[Result]) -> dict[str, int]:
    """Count results per status.

    Args:
        results: Rows to count.

    Returns:
        dict[str, int]: Every status in ``STATUSES`` mapped to its count.

    Raises:
        None
    """
    return {name: sum(1 for r in results if r.status == name) for name in STATUSES}


def gate_blockers(results: Sequence[Result], problems: Sequence[str]) -> list[str]:
    """Describe what stops the release gate, empty when it passes (TOOLS-005).

    Args:
        results: All rows.
        problems: Exception-file problems from ``apply_exceptions``.

    Returns:
        list[str]: One phrase per kind of blocker, such as
        ``2 update available (ruff, ty)``.

    Raises:
        None
    """
    blockers = []
    for name in (UPDATE, UNKNOWN):
        items = [r.item for r in results if r.status == name]
        if items:
            named = ", ".join(items[:_NAMED])
            more = f" and {len(items) - _NAMED} more" if len(items) > _NAMED else ""
            blockers.append(f"{len(items)} {name} ({named}{more})")
    if problems:
        blockers.append(f"{len(problems)} exception problem(s)")
    return blockers


def render_table(
    results: Sequence[Result], problems: Sequence[str] = (), gate: bool = False
) -> str:
    """Render rows as an aligned plain-text table with notes and a summary.

    Args:
        results: Rows in report order.
        problems: Exception-file problems, listed under the table.
        gate: Add the release gate's verdict as the last line.

    Returns:
        str: Table, then one line per unknown, held, or explained update,
        then exception problems, then a one-line summary (and the verdict).

    Raises:
        None
    """
    rows = [_HEADERS] + [
        (r.item, r.current or "-", r.latest or "-", r.status, r.notes or "-") for r in results
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_HEADERS) - 1)]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row[:-1], widths, strict=True))
        + "  "
        + row[-1]
        for row in rows
    ]
    explained = [r for r in results if r.detail and r.status not in (CURRENT, REFERENCE)]
    if explained or problems:
        lines.append("")
        lines.extend(f"{r.status}: {r.item}: {r.detail}" for r in explained)
        lines.extend(f"exception: {problem}" for problem in problems)
    counts = summarize(results)
    shown = [name for name in STATUSES if counts[name] or name in _ALWAYS_COUNTED]
    lines.append("")
    lines.append(
        f"check-updates: {len(results)} items: "
        + ", ".join(f"{counts[name]} {name}" for name in shown)
    )
    if gate:
        blockers = gate_blockers(results, problems)
        verdict = "gate failed: " + "; ".join(blockers) if blockers else "gate passed"
        lines.append(f"check-updates: {verdict}")
    return "\n".join(lines)


def render_json(results: Sequence[Result], problems: Sequence[str] = ()) -> str:
    """Render rows, per-status counts, and exception problems as a JSON document.

    Args:
        results: Rows in report order.
        problems: Exception-file problems.

    Returns:
        str: ``{"summary": {...}, "items": [...], "problems": [...]}``,
        indented for reading.

    Raises:
        None
    """
    document = {
        "summary": summarize(results),
        "items": [asdict(r) for r in results],
        "problems": list(problems),
    }
    return json.dumps(document, indent=2)


def exit_code(
    results: Sequence[Result],
    fail_on_outdated: bool,
    gate: bool = False,
    problems: Sequence[str] = (),
) -> int:
    """Choose the process exit status for a finished report.

    Args:
        results: All rows.
        fail_on_outdated: Whether an available update is a failure.
        gate: Whether this is the release gate (``--gate``).
        problems: Exception-file problems; they fail only the gate.

    Returns:
        int: 1 when ``fail_on_outdated`` is set and any row has an update
        available, or when ``gate`` is set and ``gate_blockers`` finds
        anything; otherwise 0.

    Raises:
        None
    """
    if gate and gate_blockers(results, problems):
        return 1
    outdated = any(r.status == UPDATE for r in results)
    return 1 if fail_on_outdated and outdated else 0
