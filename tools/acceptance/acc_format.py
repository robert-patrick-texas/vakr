# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Formatting helpers for the comparison report: numbers, times, tables, verdicts.

Dependencies:
    collections, datetime: Standard library only.

Safety:
    ``cell`` escapes ``|`` and line breaks, so a name from a bundle cannot
    break a Markdown table.

Example:
    >>> rate(412_300_000.0), percent(0.0042), cell("a|b")
    ('412.3 Mb/s', '+0.42 %', 'a\\|b')
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

_PREFIXES = ((1e9, "G"), (1e6, "M"), (1e3, "k"))


def cell(text: object) -> str:
    """Make text safe inside a Markdown table cell.

    Args:
        text: Any value.

    Returns:
        str: The text with ``|`` escaped and line breaks removed.

    Raises:
        None
    """
    return str(text).replace("|", "\\|").replace("\n", " ")


def rate(bps: float | None) -> str:
    """Format bits per second with an SI prefix.

    Args:
        bps: Rate, or ``None``.

    Returns:
        str: Such as ``412.3 Mb/s``, or ``—``.

    Raises:
        None
    """
    if bps is None:
        return "—"
    factor, prefix = next(((f, p) for f, p in _PREFIXES if abs(bps) >= f), (1.0, ""))
    return f"{bps / factor:.1f} {prefix}b/s"


def percent(share: float | None) -> str:
    """Format a signed relative difference.

    Args:
        share: Such as ``0.0042``, or ``None``.

    Returns:
        str: Such as ``+0.42 %``, or ``—``.

    Raises:
        None
    """
    return "—" if share is None else f"{share * 100:+.2f} %"


def when(epoch: float) -> str:
    """Format epoch seconds as UTC.

    Args:
        epoch: Seconds.

    Returns:
        str: ``YYYY-MM-DD HH:MM``.

    Raises:
        None
    """
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M")


def overall(verdicts: Iterable[str]) -> str:
    """Combine subject verdicts into one criterion verdict.

    Args:
        verdicts: Per-subject verdicts.

    Returns:
        str: ``fail`` if any failed, ``pass`` if all passed, ``no data``
        without subjects, else ``review``.

    Raises:
        None
    """
    found = Counter(verdicts)
    if not found:
        return "no data"
    if found["fail"]:
        return "fail"
    return "pass" if set(found) == {"pass"} else "review"


def table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    """Render a Markdown table.

    Args:
        header: Column titles.
        rows: Cells.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


def categories(counts: Counter[str], meanings: dict[str, str]) -> list[str]:
    """Render the category table for the categories that occurred.

    Args:
        counts: Windows per category.
        meanings: Category explanations.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    rows = [(f"`{name}`", counts[name], text) for name, text in meanings.items() if counts[name]]
    return table(("Category", "Windows", "Meaning"), rows) if rows else ["None.", ""]


def summary(verdicts: Iterable[str]) -> str:
    """Count verdicts, such as ``12 pass, 1 review``.

    Args:
        verdicts: Per-subject verdicts.

    Returns:
        str: The counts, or ``none``.

    Raises:
        None
    """
    found = Counter(verdicts)
    order = ("pass", "review", "fail", "insufficient")
    return ", ".join(f"{found[v]} {v}" for v in order if found[v]) or "none"
