"""Find and read ``inventory.toml`` with a size bound (11.1, E.26).

The file lives at ``/etc/vakr/inventory.toml`` (relocated by ``VAKR_ROOT``)
unless ``inventory.file`` names another absolute path. A missing file is
not an error: a node may keep its whole inventory in the API. A file that
exists but cannot be read, is larger than ``MAX_FILE_BYTES``, or is not
TOML is an error naming the file.

Dependencies:
    tomllib: The TOML parser.
    vakr.inventory.parse: Checks the document.

Safety:
    The file is read once, bounded, and parsed as data. Messages never echo
    file content beyond the TOML parser's own position report.

Example:
    >>> inventory_path(Path("/etc/vakr"), "")
    PosixPath('/etc/vakr/inventory.toml')
"""

import tomllib
from pathlib import Path

from vakr.inventory.errors import InventoryError, Issue
from vakr.inventory.model import InventoryFile
from vakr.inventory.parse import parse_document

INVENTORY_FILE = "inventory.toml"
"""Default file name inside the configuration directory (11.1)."""

MAX_FILE_BYTES = 4 * 1024 * 1024
"""Largest inventory file read (E.26), the same bound as policy files (E.18)."""


def inventory_path(config_dir: Path, configured: str) -> Path:
    """Return the inventory file location.

    Args:
        config_dir: The configuration directory, normally ``/etc/vakr``.
        configured: ``inventory.file``; empty means the default location.

    Returns:
        Path: The file to read.

    Raises:
        None
    """
    return Path(configured) if configured else config_dir / INVENTORY_FILE


def read_inventory(path: Path) -> InventoryFile | None:
    """Read and check an inventory file.

    Args:
        path: The file.

    Returns:
        InventoryFile | None: The checked entries, or ``None`` when the file
        does not exist.

    Raises:
        InventoryError: If the file is unreadable, too large, not TOML, or
            has invalid entries.
    """
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InventoryError(path.name, [Issue("", f"cannot read: {exc.strerror}")]) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InventoryError(path.name, [Issue("", f"larger than {MAX_FILE_BYTES} bytes (E.26)")])
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InventoryError(path.name, [Issue("", f"not valid TOML: {exc}")]) from None
    return parse_document(data, path.name)
