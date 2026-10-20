"""Value encodings shared by vakr's SQLite adapters (DB-007, invariant 11).

Public IDs are stored as their 16 big-endian bytes. Byte order equals the
canonical text order the ports specify for UUIDs, so ``ORDER BY id`` on a
blob column matches the reference adapter's ``str(id)`` order. Timestamps
are integer microseconds since the Unix epoch, reusing
``vakr.domain.timestamps``, so they sort, compare, and round-trip exactly.
Decoding a stored value that does not fit is damaged storage
(``StorageError``), never the caller's ``ValidationError``.

Dependencies:
    uuid, datetime: The decoded types.
    vakr.domain.timestamps, errors: The microsecond rule and errors.

Safety:
    Pure functions; nothing here touches a connection.

Example:
    >>> blob = uuid_to_blob(UUID("0190a3c4-0000-7000-8000-000000000001"))
    >>> len(blob), str(blob_to_uuid(blob))
    (16, '0190a3c4-0000-7000-8000-000000000001')
"""

import contextlib
from datetime import datetime
from uuid import UUID

from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.timestamps import from_epoch_us, to_epoch_us


def uuid_to_blob(value: UUID) -> bytes:
    """Encode a UUID as its 16 big-endian bytes.

    Args:
        value: The UUID.

    Returns:
        bytes: 16 bytes whose ``memcmp`` order equals the canonical text order.

    Raises:
        None
    """
    return value.bytes


def blob_to_uuid(value: object) -> UUID:
    """Decode a stored 16-byte UUID blob.

    Args:
        value: Column value.

    Returns:
        UUID: The identifier.

    Raises:
        StorageError: If the stored value is not a 16-byte blob.
    """
    if not isinstance(value, bytes) or len(value) != 16:
        raise StorageError("a stored identifier is not a 16-byte UUID", retryable=False)
    return UUID(bytes=value)


def optional_blob(value: UUID | None) -> bytes | None:
    """Encode an optional UUID.

    Args:
        value: The UUID, or ``None``.

    Returns:
        bytes | None: The blob, or ``None``.

    Raises:
        None
    """
    return None if value is None else value.bytes


def optional_uuid(value: object) -> UUID | None:
    """Decode an optional stored UUID blob.

    Args:
        value: Column value, or ``None``.

    Returns:
        UUID | None: The identifier, or ``None``.

    Raises:
        StorageError: If a non-null value is not a 16-byte blob.
    """
    return None if value is None else blob_to_uuid(value)


def encode_time(value: datetime) -> int:
    """Encode an aware UTC datetime as integer microseconds since the epoch.

    Args:
        value: Aware UTC datetime.

    Returns:
        int: Microseconds, exactly.

    Raises:
        ValidationError: If ``value`` is not an aware UTC datetime.
    """
    return to_epoch_us(value)


def decode_time(value: object) -> datetime:
    """Decode a stored integer-microsecond timestamp.

    Args:
        value: Column value.

    Returns:
        datetime: Aware UTC datetime.

    Raises:
        StorageError: If the stored value is not a representable timestamp.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        with contextlib.suppress(ValidationError):
            return from_epoch_us(value)
    raise StorageError("a stored timestamp is not valid", retryable=False)
