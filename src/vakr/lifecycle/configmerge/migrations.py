"""Declarative, ordered configuration migrations between schema versions.

Operators skip releases: a host may jump from config_version 1 straight to
4. Each release therefore ships every migration as data (rename, move,
remove, set, value map, unit scale, type conversion, split), and the upgrade
applies the whole chain in order (CFGM-002, CFGM-T05). The same data explains
retired keys, so a stale key in an operator file gets a "renamed to X by
migration 1->2" hint instead of a bare "unknown key" (CFGM-006).

Dependencies:
    tomllib: Parses the migrations file.

Safety:
    Loading checks that migrations form one contiguous chain ending at the
    catalog's version, so a gap is caught when the package is built.

Example:
    >>> text = '''
    ... [[migrations]]
    ... from = 1
    ... to = 2
    ... description = "Rename update.track"
    ... operations = [{ op = "rename", from = "update.track", to = "update.channel" }]
    ... '''
    >>> [m.label for m in parse_migrations(text, current=2)]
    ['1->2']
"""

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OPERATION_PARAMS: Mapping[str, tuple[str, ...]] = {
    "rename": ("from", "to"),
    "move_table": ("from", "to"),
    "remove": ("key",),
    "set": ("key", "value"),
    "map_values": ("key", "map"),
    "scale": ("key", "factor"),
    "convert": ("key", "to"),
    "split": ("from", "into"),
}
"""Required parameters of each operation; ``convert`` and ``split`` take more options."""

CONVERSIONS = ("str", "int", "float", "duration", "list")
"""Target types of the ``convert`` operation."""

BASE_VERSION = 1
"""``config_version`` assumed for an operator file that does not state one."""


class MigrationError(ValueError):
    """A migration is malformed, or cannot be applied to an operator file."""


@dataclass(frozen=True, slots=True)
class Operation:
    """One step of a migration.

    Attributes:
        op: Operation name, a key of ``OPERATION_PARAMS``.
        params: Operation parameters, such as ``{"from": ..., "to": ...}``.
    """

    op: str
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Migration:
    """Every change between two consecutive schema versions.

    Attributes:
        source: ``config_version`` the migration applies to.
        target: ``config_version`` after it has applied.
        description: Operator-facing summary, shown in hints and diffs.
        operations: Steps, applied in order.
    """

    source: int
    target: int
    description: str
    operations: tuple[Operation, ...]

    @property
    def label(self) -> str:
        """Short name such as ``"1->2"``.

        Args:
            None

        Returns:
            str: Source and target versions.

        Raises:
            None
        """
        return f"{self.source}->{self.target}"


def load_migrations(path: Path, current: int) -> tuple[Migration, ...]:
    """Read and validate a migrations file.

    Args:
        path: Migrations TOML file.
        current: The catalog's ``config_version``.

    Returns:
        tuple[Migration, ...]: Migrations in application order.

    Raises:
        MigrationError: If the file is malformed or the chain is broken.
        OSError: If the file cannot be read.
    """
    return parse_migrations(path.read_text(encoding="utf-8"), current, str(path))


def parse_migrations(text: str, current: int, origin: str = "migrations") -> tuple[Migration, ...]:
    """Parse migrations TOML and check it forms one chain ending at ``current``.

    Args:
        text: Migrations TOML.
        current: The catalog's ``config_version``.
        origin: Name used in error messages.

    Returns:
        tuple[Migration, ...]: Migrations in application order.

    Raises:
        MigrationError: If the TOML, an operation, or the chain is invalid.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MigrationError(f"{origin}: {exc}") from exc
    migrations = tuple(_parse_migration(entry, origin) for entry in data.get("migrations", []))
    previous = migrations[0].source if migrations else current
    for migration in migrations:
        if migration.source != previous or migration.target <= migration.source:
            raise MigrationError(f"{origin}: migration {migration.label} breaks the chain")
        previous = migration.target
    if previous != current:
        raise MigrationError(f"{origin}: migrations end at {previous}, catalog is at {current}")
    return migrations


def migration_path(
    migrations: Sequence[Migration], start: int, current: int
) -> tuple[Migration, ...]:
    """Select the migrations that take a file from ``start`` to ``current``.

    Args:
        migrations: The full, validated chain.
        start: The file's ``config_version``.
        current: The catalog's ``config_version``.

    Returns:
        tuple[Migration, ...]: Migrations to apply, in order; empty when the
        file is already current.

    Raises:
        MigrationError: If the file is newer than this release, or no
            migration starts at its version.
    """
    if start == current:
        return ()
    if start > current:
        raise MigrationError(
            f"config_version {start} is newer than this release supports ({current})"
        )
    for index, migration in enumerate(migrations):
        if migration.source == start:
            return tuple(migrations[index:])
    raise MigrationError(f"no config migration starts at config_version {start}")


def retired_keys(migrations: Sequence[Migration]) -> dict[str, str]:
    """Map each key a migration renamed, moved, split, or removed to an explanation.

    Args:
        migrations: The full chain.

    Returns:
        dict[str, str]: Old dotted key (``[]`` marks array elements) to a
        phrase such as ``"renamed to update.channel by config migration
        1->2 (Rename update.track)"``.

    Raises:
        None
    """
    retired: dict[str, str] = {}
    for migration in migrations:
        label = f"config migration {migration.label} ({migration.description})"
        for operation in migration.operations:
            params = operation.params
            if operation.op in {"rename", "move_table"}:
                retired[params["from"]] = f"renamed to {params['to']} by {label}"
            elif operation.op == "split":
                retired[params["from"]] = f"split into {', '.join(params['into'])} by {label}"
            elif operation.op == "remove":
                retired[params["key"]] = f"removed by {label}"
    return retired


def retirement(retired: Mapping[str, str], key: str) -> str | None:
    """Explain why ``key`` (or a table enclosing it) no longer exists.

    Args:
        retired: Result of ``retired_keys``.
        key: Unknown key from an operator file, such as ``"a.b[2].c"``.

    Returns:
        str | None: ``"<old> was <explanation>"``, or ``None`` when no
        migration retired the key.

    Raises:
        None
    """
    probe = re.sub(r"\[\d+\]", "[]", key)
    while probe:
        if probe in retired:
            return f"{probe} was {retired[probe]}"
        cut = max(probe.rfind("."), probe.rfind("["))
        probe = probe[:cut] if cut > 0 else ""
    return None


def _parse_migration(entry: Mapping[str, Any], origin: str) -> Migration:
    """Build one migration from its ``[[migrations]]`` table.

    Args:
        entry: The migration table.
        origin: File name for error messages.

    Returns:
        Migration: The migration.

    Raises:
        MigrationError: If fields or operations are missing or unknown.
    """
    source, target = entry.get("from"), entry.get("to")
    if not isinstance(source, int) or not isinstance(target, int) or source < BASE_VERSION:
        raise MigrationError(f"{origin}: every migration needs integer from and to")
    where = f"{origin}: migration {source}->{target}"
    if not entry.get("description"):
        raise MigrationError(f"{where}: description is required")
    operations = []
    for raw in entry.get("operations", []):
        params = {k: v for k, v in raw.items() if k != "op"}
        required = OPERATION_PARAMS.get(raw.get("op", ""))
        if required is None:
            raise MigrationError(f"{where}: unknown operation {raw.get('op')!r}")
        if missing := [name for name in required if name not in params]:
            raise MigrationError(f"{where}: {raw['op']} needs {', '.join(missing)}")
        if raw["op"] == "convert" and params["to"] not in CONVERSIONS:
            raise MigrationError(f"{where}: convert to must be one of {', '.join(CONVERSIONS)}")
        operations.append(Operation(raw["op"], params))
    return Migration(source, target, entry["description"], tuple(operations))
