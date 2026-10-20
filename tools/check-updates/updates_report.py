# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Render check-updates results as a table or JSON and choose the exit status.

The release checklist (TOOLS-002) needs one readable list: what is
pinned, what is newest, whether to act, and where the release notes are.
The same results are available as JSON for scripts. The report is
informational by default and exits 0; ``--fail-on-outdated`` turns any
"update available" into exit 1 for use as a gate. Items whose status could
not be determined ("unknown", usually a network failure) never fail it,
so an outage cannot block work; their reasons are listed under the table.

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

from updates_versions import CURRENT, UNKNOWN, UPDATE

STATUSES = (CURRENT, UPDATE, UNKNOWN)
_HEADERS = ("ITEM", "CURRENT", "LATEST", "STATUS", "RELEASE NOTES")


@dataclass(frozen=True, slots=True)
class Result:
    """One reported row.

    Attributes:
        item: What was checked, such as ``ruff`` or ``actions/checkout``.
        category: Source family: ``python``, ``go``, ``tool``, ``pypi``,
            ``lockfile``, ``action``, or ``asset``.
        current: Version in use; empty when unknown or not pinned.
        latest: Latest stable upstream version; empty when unavailable.
        status: ``current``, ``update available``, or ``unknown``.
        notes: Release-notes URL for ``latest``.
        detail: Where the pin lives, or why the status is unknown.
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


def render_table(results: Sequence[Result]) -> str:
    """Render rows as an aligned plain-text table with notes and a summary.

    Args:
        results: Rows in report order.

    Returns:
        str: Table, then one line per unknown or explained update, then a
        one-line summary.

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
    explained = [r for r in results if r.detail and r.status != CURRENT]
    if explained:
        lines.append("")
        lines.extend(f"{r.status}: {r.item}: {r.detail}" for r in explained)
    counts = summarize(results)
    lines.append("")
    lines.append(
        f"check-updates: {len(results)} items: "
        + ", ".join(f"{counts[name]} {name}" for name in STATUSES)
    )
    return "\n".join(lines)


def render_json(results: Sequence[Result]) -> str:
    """Render rows and per-status counts as a JSON document.

    Args:
        results: Rows in report order.

    Returns:
        str: ``{"summary": {...}, "items": [...]}``, indented for reading.

    Raises:
        None
    """
    document = {"summary": summarize(results), "items": [asdict(r) for r in results]}
    return json.dumps(document, indent=2)


def exit_code(results: Sequence[Result], fail_on_outdated: bool) -> int:
    """Choose the process exit status for a finished report.

    Args:
        results: All rows.
        fail_on_outdated: Whether an available update is a failure.

    Returns:
        int: 1 when ``fail_on_outdated`` is set and any row has an update
        available, otherwise 0.

    Raises:
        None
    """
    outdated = any(r.status == UPDATE for r in results)
    return 1 if fail_on_outdated and outdated else 0
