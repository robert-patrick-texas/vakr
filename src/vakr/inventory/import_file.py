r"""Read a ``vakr inventory import`` file: CSV or TOML, bounded, every problem located (E.38).

An import file is a device CSV, an interface-annotation CSV, or a TOML
file in the ``inventory.toml`` format (sites, devices, annotations). The
format comes from ``--format`` or the file's suffix; a CSV's kind from
``--kind`` or its header (``ifname``: annotations; ``address``: devices).
The same bounds apply as to ``inventory.toml``: 4 MiB, and 1,000 sites,
5,000 devices, and 20,000 annotations (E.26). Entries come back in file
order with the fields each row wrote (``given``), because an import changes
only those.

Dependencies:
    tomllib: TOML.
    vakr.inventory.csv_codec, csv_columns, parse: Rows and entries.

Safety:
    Reads bytes the caller supplies; parses data and executes nothing.

Example:
    >>> parse_import(b"name,address\\nr1,192.0.2.1\\n", "d.csv", None, None).devices[0].name
    'r1'
"""

import tomllib
from pathlib import Path

from vakr.inventory.csv_codec import locate, read_rows, unknown_columns
from vakr.inventory.csv_columns import ANNOTATIONS, COLUMNS, DEVICES, detect, entries
from vakr.inventory.errors import InventoryError, Issue
from vakr.inventory.files import MAX_FILE_BYTES
from vakr.inventory.model import AnnotationEntry, DeviceEntry, InventoryFile
from vakr.inventory.parse import MAX_ANNOTATIONS, MAX_DEVICES, parse_document

FORMATS = ("csv", "toml")
KINDS = (DEVICES, ANNOTATIONS)
_ROWS = {DEVICES: MAX_DEVICES, ANNOTATIONS: MAX_ANNOTATIONS}


def read_bounded(path: Path) -> bytes:
    """Read an import file of at most ``MAX_FILE_BYTES``.

    Args:
        path: The file.

    Returns:
        bytes: Its content.

    Raises:
        InventoryError: If it is missing, unreadable, or too large.
    """
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise InventoryError(path.name, [Issue("", f"cannot read: {exc.strerror}")]) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InventoryError(path.name, [Issue("", f"larger than {MAX_FILE_BYTES} bytes (E.26)")])
    return raw


def _format(name: str, fmt: str | None) -> str:
    """Choose the format: ``--format``, else the suffix.

    Args:
        name: The file name.
        fmt: ``csv``, ``toml``, or ``None``.

    Returns:
        str: ``csv`` or ``toml``.

    Raises:
        InventoryError: If neither says.
    """
    chosen = fmt or Path(name).suffix.lower().removeprefix(".")
    if chosen not in FORMATS:
        raise InventoryError(name, [Issue("", "cannot tell the format; pass --format csv|toml")])
    return chosen


def _csv(raw: bytes, name: str, kind: str | None) -> InventoryFile:
    """Read a device or annotation CSV.

    Args:
        raw: File content.
        name: File name.
        kind: ``devices``, ``annotations``, or ``None`` to read the header.

    Returns:
        InventoryFile: Devices or annotations, in row order.

    Raises:
        InventoryError: With every problem's row and column.
    """
    rows = read_rows(raw, name, max(_ROWS.values()) if kind is None else _ROWS[kind])
    chosen = detect(rows.header, kind)
    if chosen is None:
        why = "cannot tell devices from annotations by the header; pass --kind devices|annotations"
        raise InventoryError(name, [Issue("row 1", why)])
    issues = unknown_columns(rows.header, COLUMNS[chosen])
    if len(rows.rows) > _ROWS[chosen]:
        issues.append(Issue("", f"more than {_ROWS[chosen]} rows (E.26)"))
    if issues:
        raise InventoryError(name, issues)
    found = entries(rows, chosen, issues)
    if issues:
        raise InventoryError(name, locate(issues))
    if chosen == DEVICES:
        return InventoryFile(name, devices=tuple(e for e in found if isinstance(e, DeviceEntry)))
    notes = tuple(e for e in found if isinstance(e, AnnotationEntry))
    return InventoryFile(name, annotations=notes)


def parse_import(raw: bytes, name: str, fmt: str | None, kind: str | None) -> InventoryFile:
    """Parse an import file into entries.

    Args:
        raw: File content, at most ``MAX_FILE_BYTES``.
        name: File name, for messages and the plan.
        fmt: ``csv``, ``toml``, or ``None`` for the suffix.
        kind: For a CSV: ``devices``, ``annotations``, or ``None`` for the header.

    Returns:
        InventoryFile: Entries in file order; ``given`` names what each wrote.

    Raises:
        InventoryError: Listing every problem where it is.
    """
    if _format(name, fmt) == "csv":
        return _csv(raw, name, kind)
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InventoryError(name, [Issue("", f"not valid TOML: {exc}")]) from None
    return parse_document(data, name, partial=True)
