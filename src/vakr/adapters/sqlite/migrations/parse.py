r"""Migration files: their format, parsing, and loading from package data (5.6).

A migration is one SQL file shipped as package data, so it lands in the
wheel and the ``.deb`` with the code that expects it. Its name is
``NNNN_name.sql``, where ``NNNN`` is the schema version it produces.
Versions run 1, 2, 3, ... without gaps. A header of ``-- key: value``
lines, ending at the first empty line, carries what 5.6 requires of every
migration::

    -- description: What it changes.
    -- destructive: false
    -- transactional: true
    -- recovery: How to recover if it fails part-way.
    -- data-volume: How its duration grows with the data.
    -- test-impact: Which tests cover it.

A value continues on lines that start with ``--`` and three spaces.
``transactional`` is optional and defaults to ``true``; set it to ``false``
only for statements SQLite refuses inside a transaction, such as ``VACUUM``.
The runner owns transactions, so a file must not contain ``BEGIN``,
``COMMIT``, ``ROLLBACK``, or savepoints. Released files never change: the
journal stores each file's checksum, and a different file at an applied
version means another build wrote the schema.

Dependencies:
    hashlib, re: Checksums and header parsing.
    importlib.resources: Reading packaged files.
    vakr.adapters.sqlite.migrations.model: ``Migration`` and the version rule.

Safety:
    Parsing never executes SQL. Errors name the file and the offending key.

Example:
    >>> text = (
    ...     "-- description: Add a table.\n-- destructive: false\n-- recovery: Rerun.\n"
    ...     "-- data-volume: Instant.\n-- test-impact: MIG-002.\n\nCREATE TABLE t (x INT);\n"
    ... )
    >>> m = parse_migration("0002_add_table.sql", text)
    >>> m.version, m.name, m.transactional
    (2, 'add_table', True)
"""

import hashlib
import re
from importlib.resources.abc import Traversable

from vakr.adapters.sqlite.migrations.model import Migration, require_sequence
from vakr.domain.errors import ValidationError

_FILE_NAME = re.compile(r"(?P<version>[0-9]{4})_(?P<name>[a-z0-9]+(?:_[a-z0-9]+)*)\.sql")
_HEADER_KEY = re.compile(r"-- (?P<key>[a-z][a-z-]*): (?P<value>\S.*)")
_CONTINUATION = re.compile(r"-- {3,}(?P<value>\S.*)")
_REQUIRED = ("description", "destructive", "recovery", "data-volume", "test-impact")
_KNOWN = frozenset({*_REQUIRED, "transactional"})
# Statement-initial transaction control. A trigger body's BEGIN is followed by
# statements, not by ";" or a transaction mode, so triggers stay allowed.
_TX_CONTROL = re.compile(
    r"^\s*(?:BEGIN(?:\s+(?:DEFERRED|IMMEDIATE|EXCLUSIVE))?(?:\s+TRANSACTION)?\s*;"
    r"|COMMIT\b|ROLLBACK\b|END\s+TRANSACTION\b|SAVEPOINT\b|RELEASE\b)",
    re.IGNORECASE | re.MULTILINE,
)


def parse_migration(file_name: str, text: str) -> Migration:
    """Parse one migration file.

    Args:
        file_name: ``NNNN_name.sql``.
        text: The file's content.

    Returns:
        Migration: The parsed migration.

    Raises:
        ValidationError: If the name, header, or body breaks the rules above.
    """
    match = _FILE_NAME.fullmatch(file_name)
    if match is None or int(match["version"]) < 1:
        raise ValidationError(f"{file_name}: migration files are named NNNN_name.sql from 0001")
    header, _, sql = text.partition("\n\n")
    fields = _parse_header(file_name, header)
    missing = [key for key in _REQUIRED if key not in fields]
    if missing:
        raise ValidationError(f"{file_name}: header lacks {', '.join(missing)}", field=missing[0])
    if not sql.strip():
        raise ValidationError(f"{file_name}: no SQL after the header")
    if _TX_CONTROL.search(sql):
        raise ValidationError(f"{file_name}: the runner owns transactions; remove BEGIN/COMMIT")
    return Migration(
        version=int(match["version"]),
        name=match["name"],
        description=fields["description"],
        destructive=_flag(file_name, fields, "destructive"),
        transactional=_flag(file_name, fields, "transactional"),
        recovery=fields["recovery"],
        data_volume=fields["data-volume"],
        test_impact=fields["test-impact"],
        sql=sql,
        checksum="sha256:" + hashlib.sha256(sql.encode("utf-8")).hexdigest(),
    )


def _parse_header(file_name: str, header: str) -> dict[str, str]:
    """Collect ``-- key: value`` lines and their continuations.

    Args:
        file_name: For messages.
        header: Text before the first empty line.

    Returns:
        dict[str, str]: Key to value, continuations joined with spaces.

    Raises:
        ValidationError: If a line is neither a known key nor a continuation,
            or a key repeats.
    """
    fields: dict[str, str] = {}
    key: str | None = None
    for line in header.splitlines():
        if (found := _HEADER_KEY.fullmatch(line)) is not None:
            key = found["key"]
            if key not in _KNOWN or key in fields:
                raise ValidationError(f"{file_name}: unknown or repeated header key {key}")
            fields[key] = found["value"]
        elif (more := _CONTINUATION.fullmatch(line)) is not None and key is not None:
            fields[key] += " " + more["value"]
        else:
            raise ValidationError(f"{file_name}: header line is not '-- key: value': {line[:60]}")
    return fields


def _flag(file_name: str, fields: dict[str, str], key: str) -> bool:
    """Read a ``true``/``false`` header value; ``transactional`` defaults to true.

    Args:
        file_name: For messages.
        fields: Parsed header.
        key: The key.

    Returns:
        bool: The flag.

    Raises:
        ValidationError: If the value is neither ``true`` nor ``false``.
    """
    value = fields.get(key, "true")
    if value not in {"true", "false"}:
        raise ValidationError(f"{file_name}: {key} must be true or false", field=key)
    return value == "true"


def load_migrations(directory: Traversable) -> tuple[Migration, ...]:
    """Load every ``*.sql`` file in a packaged directory, in version order.

    Args:
        directory: Package-data directory, such as
            ``importlib.resources.files(pkg) / "sql"``, or a ``Path``.

    Returns:
        tuple[Migration, ...]: Versions 1..N.

    Raises:
        ValidationError: If a file breaks the rules or the versions have gaps.
    """
    entries = sorted((e for e in directory.iterdir() if e.name.endswith(".sql")), key=_name)
    migrations = tuple(parse_migration(e.name, e.read_text(encoding="utf-8")) for e in entries)
    require_sequence(migrations)
    return migrations


def _name(entry: Traversable) -> str:
    """Sort key: the file name, whose zero-padded prefix orders versions.

    Args:
        entry: A directory entry.

    Returns:
        str: Its name.

    Raises:
        None
    """
    return entry.name
