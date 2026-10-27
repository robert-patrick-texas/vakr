"""CSV columns for devices and interface annotations, and rows to entries (META-010, E.38).

A device CSV has one row per device and the columns of a ``[[devices]]``
table; an annotation CSV has one row per interface and the columns of an
``[[interface_annotations]]`` table (META-007, META-011's ``high_priority``
alias, META-013 tags). Each cell becomes the value a TOML table would hold
and then goes through the same parser as ``inventory.toml``, so both
formats are validated alike. The cell rules (E.38):

- an empty cell is a value: it clears the field (no site, no tags,
  ``false``, ``secondary``, the inherited interval); a column left out of
  the header keeps every stored value;
- lists (``tags``, ``groups``) are separated by ``;``;
- booleans are ``true`` or ``false`` in any case;
- whole numbers are plain digits, at most 18 of them;
- ``managed_by`` states the owner an export saw; an import compares it and
  never changes ownership. ``alias`` is the interface description an export
  shows for classification; an import does not read it.

Dependencies:
    vakr.inventory.parse: The table parsers.
    vakr.inventory.csv_codec: Rows.

Safety:
    Pure. Messages name rows and columns.

Example:
    >>> detect(("name", "address"), None)
    'devices'
"""

import re
from dataclasses import replace
from enum import StrEnum

from vakr.domain.enums import ManagedByKind
from vakr.domain.errors import ValidationError
from vakr.domain.ownership import ManagedBy
from vakr.inventory.csv_codec import CsvRows
from vakr.inventory.errors import Issue
from vakr.inventory.model import AnnotationEntry, DeviceEntry
from vakr.inventory.parse import parse_annotation, parse_device


class Cell(StrEnum):
    """How one column's cells are read."""

    TEXT = "text"
    LIST = "list"
    BOOL = "bool"
    INT = "int"
    OWNER = "owner"
    INFO = "info"


DEVICES, ANNOTATIONS = "devices", "annotations"

DEVICE_COLUMNS: dict[str, Cell] = {
    "name": Cell.TEXT, "uuid": Cell.TEXT, "managed_by": Cell.OWNER, "address": Cell.TEXT,
    "site": Cell.TEXT, "credential": Cell.TEXT, "admin_state": Cell.TEXT, "interval": Cell.TEXT,
    "discovery_interval": Cell.TEXT, "reachability": Cell.TEXT, "address_family": Cell.TEXT,
    "tags": Cell.LIST, "groups": Cell.LIST, "comment": Cell.TEXT,
}  # fmt: skip
"""Device columns, in the order an export writes them."""

ANNOTATION_COLUMNS: dict[str, Cell] = {
    "device": Cell.TEXT, "ifname": Cell.TEXT, "uuid": Cell.TEXT, "managed_by": Cell.OWNER,
    "alias": Cell.INFO, "interest": Cell.TEXT, "wan_circuit": Cell.BOOL, "sticky": Cell.BOOL,
    "provider": Cell.TEXT, "circuit_id": Cell.TEXT, "contracted_bps": Cell.INT,
    "capacity_override_bps": Cell.INT, "display_rank": Cell.INT,
    "public_dashboard_eligible": Cell.BOOL, "tags": Cell.LIST, "comment": Cell.TEXT,
    "high_priority": Cell.BOOL,
}  # fmt: skip
"""Annotation columns; an export writes all but ``high_priority``, the import-only alias."""

COLUMNS = {DEVICES: DEVICE_COLUMNS, ANNOTATIONS: ANNOTATION_COLUMNS}
MAX_DIGITS = 18  # below 2**63, so a stored integer always fits a 64-bit column
_DIGITS = re.compile(r"[0-9]+")
_SCIENTIFIC = re.compile(r"[0-9.]+[eE][+-]?[0-9]+")


def detect(header: tuple[str, ...], kind: str | None) -> str | None:
    """Tell what a CSV holds: ``ifname`` means annotations, ``address`` devices.

    Args:
        header: Column names.
        kind: The kind the operator named, if any; it wins.

    Returns:
        str | None: ``devices`` or ``annotations``; ``None`` when unclear.

    Raises:
        None
    """
    if kind is not None:
        return kind
    if "ifname" in header:
        return ANNOTATIONS
    return DEVICES if "address" in header else None


def _number(text: str, at: str, issues: list[Issue]) -> int | None:
    """Read a whole number cell.

    Args:
        text: The cell.
        at: Its path.
        issues: Problems are appended here.

    Returns:
        int | None: The number, or ``None`` when invalid.

    Raises:
        None
    """
    if _DIGITS.fullmatch(text) and len(text) <= MAX_DIGITS:
        return int(text)
    if _SCIENTIFIC.fullmatch(text):
        why = "is in scientific notation; format the column as text so every digit is kept"
    elif _DIGITS.fullmatch(text):
        why = f"at most {MAX_DIGITS} digits"
    else:
        why = "expected a whole number, such as 100000000"
    issues.append(Issue(at, why))
    return None


def _owner(text: str, at: str, issues: list[Issue]) -> ManagedBy | None:
    """Read a ``managed_by`` cell such as ``api:api`` or ``file:inventory.toml``.

    Args:
        text: The cell.
        at: Its path.
        issues: Problems are appended here.

    Returns:
        ManagedBy | None: The owner, or ``None`` when invalid.

    Raises:
        None
    """
    kind, _, source = text.partition(":")
    try:
        return ManagedBy(ManagedByKind(kind), source)
    except ValueError, ValidationError:
        issues.append(Issue(at, "expected an owner such as api:api or file:inventory.toml"))
        return None


def cell_value(kind: Cell, text: str, at: str, issues: list[Issue]) -> object | None:
    """Convert one non-empty cell to the value a TOML table would hold.

    Args:
        kind: How the column is read.
        text: The cell, trimmed and unguarded.
        at: Its path, such as ``row 4.tags``.
        issues: Problems are appended here.

    Returns:
        object | None: The value, or ``None`` when invalid.

    Raises:
        None
    """
    if kind is Cell.LIST:
        return [part.strip() for part in text.split(";") if part.strip()]
    if kind is Cell.BOOL:
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        issues.append(Issue(at, "expected true or false"))
        return None
    if kind is Cell.INT:
        return _number(text, at, issues)
    return _owner(text, at, issues) if kind is Cell.OWNER else text


def _row(
    rows: CsvRows, number: int, cells: tuple[str, ...], kind: str, issues: list[Issue]
) -> DeviceEntry | AnnotationEntry | None:
    """Turn one row into an entry through the TOML table parser.

    Args:
        rows: The file's header and rows.
        number: The row number.
        cells: The row's cells.
        kind: ``devices`` or ``annotations``.
        issues: Problems are appended here.

    Returns:
        DeviceEntry | AnnotationEntry | None: The entry, or ``None`` when it has problems.

    Raises:
        None
    """
    before, at, columns = len(issues), f"row {number}", COLUMNS[kind]
    table: dict[str, object] = {}
    owner: object | None = None
    for column, text in zip(rows.header, cells, strict=False):
        if (
            text
            and (value := cell_value(columns[column], text, f"{at}.{column}", issues)) is not None
        ):
            if columns[column] is Cell.OWNER:
                owner = value
            elif columns[column] is not Cell.INFO:
                table[column] = value
    keys = [c for c in rows.header if columns[c] not in (Cell.OWNER, Cell.INFO)]
    if kind == DEVICES:
        entry = parse_device(table, at, issues, keys=keys, partial=True)
    else:
        entry = parse_annotation(table, at, issues, keys=keys)
    if entry is None or len(issues) > before:
        return None
    return replace(entry, managed_by=owner) if isinstance(owner, ManagedBy) else entry


def entries(rows: CsvRows, kind: str, issues: list[Issue]) -> list[DeviceEntry | AnnotationEntry]:
    """Turn every row of a checked CSV into entries.

    Args:
        rows: The header and rows from ``read_rows``.
        kind: ``devices`` or ``annotations``.
        issues: Problems are appended here.

    Returns:
        list[DeviceEntry | AnnotationEntry]: Valid entries in row order.

    Raises:
        None
    """
    return [e for n, cells in rows.rows if (e := _row(rows, n, cells, kind, issues)) is not None]
