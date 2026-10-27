"""Read and write inventory CSV text: UTF-8, one header row, located problems (META-010, E.38).

NetOps keep device lists and interface classifications in spreadsheets, so
``vakr inventory import`` accepts CSV and ``vakr inventory export`` writes
it. This module handles only the text: decoding (UTF-8, an optional BOM as
Excel's "CSV UTF-8" writes it), RFC 4180 quoting, the header row (names
trimmed and compared without case; unknown, repeated, or unnamed columns
refused), a row count bound, and rows whose cell count differs from the
header. Rows are numbered as a spreadsheet shows them, the header being
row 1, so ``row 4, column address`` is where the operator looks.

Spreadsheets run any cell that starts with ``=``, ``+``, ``-``, ``@``, a
tab, or a carriage return as a formula, and an interface description comes
from the device, not from a trusted author. Export prefixes such cells
with ``'`` (as E.30 decision 10 does for rankings); import removes that
prefix again, and a value that itself starts with ``'`` before one of those
characters gets one more, so every value survives an export and re-import.

Dependencies:
    csv, io: Parsing and writing.
    vakr.inventory.errors: Located problems.

Safety:
    Pure: no I/O. Cells are data; nothing is evaluated. Messages name rows
    and columns, never cell contents.

Example:
    >>> guard("=1+1"), unguard(guard("=1+1")), guard("Gi0/0/0")
    ("'=1+1", '=1+1', 'Gi0/0/0')
"""

import csv
import io
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from vakr.inventory.errors import InventoryError, Issue

_FORMULA = ("=", "+", "-", "@", "\t", "\r")
_ESCAPED = (*_FORMULA, "'")
_AT = re.compile(r"row (\d+)\.(.+)")
_ROW = re.compile(r"row (\d+)\b")


@dataclass(frozen=True, slots=True)
class CsvRows:
    """The header and data rows of one CSV file.

    Attributes:
        header: Column names, lower-cased, in file order.
        rows: ``(row number, cells)`` per data row; cells are trimmed and
            unguarded, and rows of empty cells are left out.
    """

    header: tuple[str, ...]
    rows: tuple[tuple[int, tuple[str, ...]], ...]


def guard(text: str) -> str:
    """Prefix ``'`` to a cell a spreadsheet would run as a formula.

    Args:
        text: The value.

    Returns:
        str: The cell text; ``unguard`` returns the value.

    Raises:
        None
    """
    risky = text.startswith(_FORMULA) or (text.startswith("'") and text[1:2] in _ESCAPED)
    return f"'{text}" if risky else text


def unguard(text: str) -> str:
    """Remove the ``'`` that ``guard`` added.

    Args:
        text: The cell text, trimmed.

    Returns:
        str: The value.

    Raises:
        None
    """
    return text[1:] if text.startswith("'") and text[1:2] in _ESCAPED else text


def _row_of(issue: Issue) -> int:
    """Return the row number an issue names, or 0.

    Args:
        issue: A problem.

    Returns:
        int: Its row, so problems sort in file order; 0 for file-level ones.

    Raises:
        None
    """
    match = _ROW.match(issue.path)
    return int(match[1]) if match else 0


def locate(issues: Sequence[Issue]) -> list[Issue]:
    """Rewrite ``row 4.address`` paths as ``row 4, column address``, in row order.

    Args:
        issues: Problems; paths of TOML entries are left as they are and in order.

    Returns:
        list[Issue]: The problems with CSV locations, sorted by row (stable).

    Raises:
        None
    """
    found = [Issue(f"row {m[1]}, column {m[2]}", i.reason) if (m := _AT.fullmatch(i.path)) else i
             for i in issues]  # fmt: skip
    return sorted(found, key=_row_of)


def _decode(raw: bytes, source: str) -> str:
    """Decode UTF-8, dropping a leading byte-order mark.

    Args:
        raw: File content.
        source: File name for messages.

    Returns:
        str: The text.

    Raises:
        InventoryError: If the bytes are not UTF-8.
    """
    try:
        return raw.decode("utf-8").removeprefix("﻿")
    except UnicodeDecodeError as exc:
        why = f"not UTF-8 at byte {exc.start}; save it as CSV UTF-8 (E.38)"
        raise InventoryError(source, [Issue("", why)]) from None


def unknown_columns(names: Collection[str], allowed: Collection[str]) -> list[Issue]:
    """Report header names that are not columns of this kind of file.

    Args:
        names: The header's names.
        allowed: Column names this kind of file may have.

    Returns:
        list[Issue]: One problem per unknown name, at row 1.

    Raises:
        None
    """
    known = ", ".join(sorted(allowed))
    return [Issue(f"row 1, column {n}", f"unknown column; expected one of: {known}")
            for n in dict.fromkeys(names) if n and n not in allowed]  # fmt: skip


def _header(cells: list[str], issues: list[Issue]) -> tuple[str, ...]:
    """Check the header row: every column named once.

    Args:
        cells: The first row.
        issues: Problems are appended here.

    Returns:
        tuple[str, ...]: The names, trimmed and lower-cased.

    Raises:
        None
    """
    names = tuple(c.strip().lower() for c in cells)
    for index, name in enumerate(names, 1):
        if not name:
            issues.append(Issue(f"row 1, column {index}", "has no name; name it or delete it"))
        elif name in names[: index - 1]:
            issues.append(Issue(f"row 1, column {name}", "repeated column"))
    return names


def read_rows(raw: bytes, source: str, max_rows: int) -> CsvRows:
    """Parse CSV text into a checked header and bounded data rows.

    Column names are checked with ``unknown_columns`` once the caller knows
    what kind of file this is.

    Args:
        raw: File content, already size-bounded by the caller.
        source: File name for messages.
        max_rows: Most data rows accepted (E.26).

    Returns:
        CsvRows: The header and the non-empty data rows.

    Raises:
        InventoryError: Listing every problem with its row and column.
    """
    issues: list[Issue] = []
    reader = csv.reader(io.StringIO(_decode(raw, source), newline=""), strict=True)
    header: tuple[str, ...] = ()
    rows: list[tuple[int, tuple[str, ...]]] = []
    number = 0
    try:
        for number, cells in enumerate(reader, 1):
            if number == 1:
                header = _header(cells, issues)
            elif any(c.strip() for c in cells):
                if len(cells) != len(header):
                    why = f"has {len(cells)} cells; the header has {len(header)}"
                    issues.append(Issue(f"row {number}", why))
                rows.append((number, tuple(unguard(c.strip()) for c in cells)))
            if len(rows) > max_rows:
                issues.append(Issue("", f"more than {max_rows} rows (E.26)"))
                break
    except csv.Error as exc:
        issues.append(Issue(f"row {number + 1}", f"not valid CSV: {exc}"))
    if not header and not issues:
        issues.append(Issue("", "empty; the first row must name the columns"))
    if issues:
        raise InventoryError(source, issues)
    return CsvRows(header, tuple(rows))


def write_rows(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Write CSV text: LF line ends, minimal quoting, every cell guarded.

    Args:
        header: Column names.
        rows: Cells per row, in order.

    Returns:
        str: The CSV text, ending with a newline.

    Raises:
        None
    """
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([guard(c) for c in row] for row in rows)
    return out.getvalue()
