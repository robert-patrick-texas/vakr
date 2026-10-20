"""The encoded form of one native row, and helpers every row codec shares.

An observation is encoded *before* the writer takes the write lock, so the
lock is held only for dictionary lookups and inserts. The encoded form
therefore still names its subject, collector, and (for interface samples)
device by UUID; the writer swaps those for dictionary keys under the lock.

Dependencies:
    vakr.adapters.sqlite.encoding: UUID blobs and microsecond timestamps.
    vakr.domain: Errors and the observation types.

Safety:
    Decoding never trusts a row: a value that does not rebuild a valid
    domain object is reported as damaged storage (``StorageError``).

Example:
    >>> from uuid import UUID
    >>> u = UUID(int=1)
    >>> EncodedRow("obs_health", "collector", u, u, 0, u.bytes, ()).subject_kind
    'collector'
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid
from vakr.domain.errors import StorageError, ValidationError

type UuidOf = Callable[[str, int], UUID]
"""Resolves ``(dictionary, key)`` to the UUID a dictionary row holds."""


@dataclass(frozen=True, slots=True)
class EncodedRow:
    """One observation, encoded except for its dictionary keys.

    Attributes:
        family: Native family, such as ``obs_iface``.
        subject_kind: Dictionary of the subject: ``interface``, ``device``,
            or ``collector``.
        subject: Subject UUID, stored as ``series_key``.
        collector: Collector UUID, stored as ``collector_key``.
        ts_us: Observation timestamp in microseconds.
        observation: Observation UUID as 16 bytes.
        values: Column values after the key columns, in ``layout.COLUMNS``
            order; ``obs_iface`` holds ``None`` where ``device_key`` goes.
        device: Device of an interface sample; ``None`` for other families.
    """

    family: str
    subject_kind: str
    subject: UUID
    collector: UUID
    ts_us: int
    observation: bytes
    values: tuple[object, ...]
    device: UUID | None = None


def int_enum[E: IntEnum](kind: type[E], value: object) -> E | None:
    """Decode an optional MIB integer such as ``ifOperStatus``.

    Args:
        kind: The integer enum.
        value: Column value, or ``None``.

    Returns:
        E | None: The member, or ``None``.

    Raises:
        StorageError: If the stored integer is not a member.
    """
    if value is None:
        return None
    try:
        return kind(value)
    except ValueError:
        raise StorageError(f"a stored {kind.__name__} is not valid", retryable=False) from None


def optional_bytes(value: UUID | None) -> bytes | None:
    """Encode an optional UUID as 16 bytes.

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
        UUID | None: The UUID, or ``None``.

    Raises:
        StorageError: If a non-null value is not a 16-byte blob.
    """
    return None if value is None else blob_to_uuid(value)


def rebuild[T](build: Callable[..., T], **fields: object) -> T:
    """Construct a domain object from stored fields, treating rule breaks as damage.

    Args:
        build: The domain class.
        **fields: Its constructor arguments.

    Returns:
        T: The object.

    Raises:
        StorageError: If the stored values break a domain rule.
    """
    try:
        return build(**fields)
    except ValidationError:
        name = getattr(build, "__name__", "observation")
        raise StorageError(f"a stored {name} is not valid", retryable=False) from None


def stored_int(value: object) -> int:
    """Return a stored integer that must be present, such as a key or flags.

    Args:
        value: Column value.

    Returns:
        int: The integer.

    Raises:
        StorageError: If the value is missing or not an integer.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise StorageError("a stored key or flag field is not an integer", retryable=False)
