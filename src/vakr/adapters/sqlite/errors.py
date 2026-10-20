"""Translate ``sqlite3`` exceptions into domain errors (5.2 rule 2, storage.md).

No driver exception may cross a port, and no SQL text, bound value, or
driver message may reach an operator, because SQLite messages quote
columns and constraint expressions. ``translate_error`` therefore keeps only
the SQLite *error name* (such as ``SQLITE_FULL``) and builds the domain
error from what the caller says it was doing (``ErrorContext``):

- unique or primary-key violations become ``DuplicateError``;
- foreign-key violations become ``NotFoundError``;
- ``SQLITE_BUSY_SNAPSHOT`` (another connection committed after this
  transaction's snapshot began) becomes ``ConflictError``;
- other constraint failures, integers beyond 64 bits, and text that is not
  valid Unicode become ``ValidationError``;
- busy, locked, full, read-only, damaged, and unopenable databases become
  ``StorageError``, retryable only for lock contention.

Dependencies:
    sqlite3: Error classes and result-code constants.
    vakr.domain.errors: The errors returned.

Safety:
    Tracebacks drop the driver exception (``raise ... from None``) so the
    driver message cannot leak through logs either.

Example:
    >>> exc = sqlite3.OperationalError("database is locked")
    >>> exc.sqlite_errorcode, exc.sqlite_errorname = sqlite3.SQLITE_BUSY, "SQLITE_BUSY"
    >>> err = translate_error(exc)
    >>> type(err).__name__, err.retryable
    ('StorageError', True)
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from vakr.domain.errors import (
    ConflictError,
    DomainError,
    DuplicateError,
    NotFoundError,
    StorageError,
    ValidationError,
)

_PRIMARY_MASK = 0xFF  # extended result codes keep the primary code in the low byte
_DUPLICATE = frozenset({sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY})
_BUSY = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
_STORAGE_REASONS = {
    sqlite3.SQLITE_FULL: "the disk or the database is full",
    sqlite3.SQLITE_READONLY: "the database is read-only for this process",
    sqlite3.SQLITE_CORRUPT: "the database file is damaged",
    sqlite3.SQLITE_NOTADB: "the file is not a SQLite database",
    sqlite3.SQLITE_CANTOPEN: "the database file cannot be opened",
    sqlite3.SQLITE_IOERR: "a disk I/O error occurred",
    sqlite3.SQLITE_PERM: "access to the database was denied",
}


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """What the adapter was doing, so a translated error can name it.

    Attributes:
        entity: Entity kind, such as ``"device"``, or ``"database"``.
        key: Record ID or name, or ``"-"`` when there is none.
        version: Version the write was based on; 0 when not versioned.
    """

    entity: str = "database"
    key: object = "-"
    version: int = 0


DATABASE = ErrorContext()
"""The context of an operation on no particular record."""


def translate_error(exc: Exception, context: ErrorContext = DATABASE) -> DomainError:
    """Return the domain error for a driver exception.

    Args:
        exc: Exception raised by ``sqlite3`` or while binding a value.
        context: What was being done.

    Returns:
        DomainError: The error to raise instead; ``exc`` itself if it already
        is one.

    Raises:
        None
    """
    if isinstance(exc, DomainError):
        return exc
    if isinstance(exc, OverflowError):
        return ValidationError(f"{context.entity} has a number too large to store (64-bit limit)")
    if isinstance(exc, UnicodeError):
        return ValidationError(f"{context.entity} has text that is not valid Unicode")
    code = getattr(exc, "sqlite_errorcode", None)
    if not isinstance(code, int):
        # ProgrammingError and friends carry no result code: an adapter bug.
        return StorageError(f"unexpected database error ({type(exc).__name__})", retryable=False)
    return _from_code(code, str(getattr(exc, "sqlite_errorname", "SQLITE_ERROR")), context)


def _from_code(code: int, name: str, context: ErrorContext) -> DomainError:
    """Map an extended SQLite result code to a domain error.

    Args:
        code: Extended result code.
        name: Its symbolic name, such as ``SQLITE_CONSTRAINT_UNIQUE``.
        context: What was being done.

    Returns:
        DomainError: The translated error.

    Raises:
        None
    """
    if code == sqlite3.SQLITE_BUSY_SNAPSHOT:
        # The stored version cannot be read from a stale snapshot, so both
        # version fields carry the caller's; the remedy (reload) is the same.
        version = context.version
        return ConflictError(
            context.entity, context.key, expected_version=version, actual_version=version
        )
    if code in _DUPLICATE:
        return DuplicateError(context.entity, "id or name", context.key)
    if code == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
        return NotFoundError(f"record referenced by {context.entity}", context.key)
    primary = code & _PRIMARY_MASK
    if primary == sqlite3.SQLITE_CONSTRAINT:
        return ValidationError(f"{context.entity} {context.key} breaks a storage rule ({name})")
    if primary in _BUSY:
        return StorageError(f"the database is busy ({name}); retry shortly", retryable=True)
    reason = _STORAGE_REASONS.get(primary, "the database reported an error")
    return StorageError(f"{reason} ({name})", retryable=False)


@contextmanager
def translated(context: ErrorContext = DATABASE) -> Generator[None]:
    """Run a block, re-raising driver exceptions as domain errors.

    Args:
        context: What the block does, for the translated message.

    Returns:
        Generator[None]: A context manager.

    Raises:
        DomainError: In place of any ``sqlite3.Error``, ``OverflowError``,
            or ``UnicodeError`` raised by the block.
    """
    try:
        yield
    except (sqlite3.Error, OverflowError, UnicodeError) as exc:
        raise translate_error(exc, context) from None
