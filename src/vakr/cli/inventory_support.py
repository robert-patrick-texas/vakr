"""Shared plumbing for ``vakr inventory`` and ``vakr interface`` (exit codes, files, storage).

Both commands load the effective configuration, the inventory file, and
the validation checks the same way, open the installed metadata database
the same way, and turn the same expected failures into the same exit
codes: 1 invalid input or a refused change, 2 usage, 4 the metadata
database is unavailable.

Dependencies:
    vakr.inventory: Files, settings, and validation.
    vakr.adapters.sqlite.metadata: The installed metadata database.

Safety:
    Messages name files, keys, and credential names; never secrets.

Example:
    >>> local_actor().startswith("cli:")
    True
"""

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, sqlite_uow_factory
from vakr.config.loader import ConfigError, load_effective_config
from vakr.domain.errors import DomainError, StorageError
from vakr.inventory.errors import InventoryError
from vakr.inventory.files import read_inventory
from vakr.inventory.model import InventoryFile
from vakr.inventory.settings import checks_for, configured_path
from vakr.inventory.snapshot import EMPTY, load_snapshot
from vakr.inventory.validate import Checks, Findings, validate
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError

EXIT_OK, EXIT_INVALID, EXIT_USAGE, EXIT_STORAGE = 0, 1, 2, 4

type Handler = Callable[[argparse.Namespace], int]


class StorageUnavailableError(Exception):
    """The metadata database cannot be used; exit status 4."""


def local_actor() -> str:
    """Name the operator running the command, for audit records (SEC-018).

    Args:
        None

    Returns:
        str: ``cli:<login name>``, or ``cli:unknown``.

    Raises:
        None
    """
    try:
        name = getpass.getuser().strip()
    except OSError:
        name = ""
    clean = "".join(c for c in name if c.isprintable() and not c.isspace())[:64]
    return f"cli:{clean or 'unknown'}"


def load_file(args: argparse.Namespace) -> tuple[InventoryFile | None, Path, Checks]:
    """Load configuration, the inventory file, and the validation checks.

    Args:
        args: ``config_dir``, ``file``, and ``dev``.

    Returns:
        tuple[InventoryFile | None, Path, Checks]: The file (``None`` when
        absent), its path, and the checks.

    Raises:
        ConfigError: If the configuration is invalid.
        InventoryError: If the file is invalid.
        ValueError: If a guardrail setting is invalid.
    """
    paths = InstallPaths.from_environment()
    config_dir = args.config_dir or paths.config_dir
    config = load_effective_config(config_dir)
    path = args.file or configured_path(config, config_dir)
    inv = read_inventory(path)
    checks = checks_for(config, paths.secrets_dir, development=args.dev) if inv else Checks()
    return inv, path, checks


def open_store(*, required: bool = True) -> SqliteMetadataStore | None:
    """Open the installed metadata database.

    Args:
        required: Whether a missing database is an error.

    Returns:
        SqliteMetadataStore | None: The store, or ``None`` when absent and not required.

    Raises:
        StorageUnavailableError: If it is missing and required, or cannot be opened.
    """
    path = InstallPaths.from_environment().state_dir / METADATA_FILE
    if not path.exists():
        if required:
            raise StorageUnavailableError(f"{path} does not exist; run `vakr db migrate` first")
        return None
    try:
        return SqliteMetadataStore(path)
    except (MigrationError, StorageError) as exc:
        raise StorageUnavailableError(f"{path}: {exc}") from None


def require_store() -> SqliteMetadataStore:
    """Open the installed metadata database, which must exist.

    Args:
        None

    Returns:
        SqliteMetadataStore: The store.

    Raises:
        StorageUnavailableError: If it is missing or cannot be opened.
    """
    store = open_store(required=True)
    if store is None:  # open_store raises instead; kept for the type checker
        raise StorageUnavailableError("metadata database missing")
    return store


def guarded(run: Handler) -> Handler:
    """Wrap a handler so expected failures become messages and exit codes.

    Args:
        run: Handler taking the parsed arguments and returning an exit code.

    Returns:
        Handler: The wrapped handler.

    Raises:
        None
    """

    def handler(args: argparse.Namespace) -> int:
        """Run the command and translate failures.

        Args:
            args: Parsed arguments.

        Returns:
            int: Exit status.

        Raises:
            None
        """
        try:
            return run(args)
        except InventoryError as exc:
            print("\n".join([*exc.lines, f"{exc.source}: invalid"]), file=sys.stderr)
            return EXIT_INVALID
        except StorageUnavailableError as exc:
            print(f"vakr: metadata database unavailable: {exc}", file=sys.stderr)
            return EXIT_STORAGE
        except (ConfigError, ValueError, DomainError) as exc:
            print(f"vakr: {exc}", file=sys.stderr)
            return EXIT_STORAGE if isinstance(exc, (MigrationError, StorageError)) else EXIT_INVALID

    return handler


def emit(args: argparse.Namespace, lines: Sequence[str], data: object) -> None:
    """Print text lines, or JSON with ``--json``.

    Args:
        args: Parsed arguments with ``json``.
        lines: Text output.
        data: JSON output.

    Returns:
        None

    Raises:
        None
    """
    print(json.dumps(data, indent=2, sort_keys=True) if args.json else "\n".join(lines))


def findings_for(inv: InventoryFile, checks: Checks) -> tuple[Findings, list[str]]:
    """Validate against storage if the database exists, else against nothing.

    Args:
        inv: The file.
        checks: Credential names and guardrails.

    Returns:
        tuple[Findings, list[str]]: Findings and a note when storage was absent.

    Raises:
        StorageUnavailableError: If the database exists but cannot be opened.
    """
    store = open_store(required=False)
    if store is None:
        note = "note: no metadata database yet; storage conflicts not checked"
        return validate(inv, EMPTY, checks), [note]
    try:
        with sqlite_uow_factory(store)() as uow:
            return validate(inv, load_snapshot(uow, inv), checks), []
    finally:
        store.close()
