"""Domain errors: broken rules that callers can explain to an operator.

These exceptions describe *why* a request cannot be honoured: a record does
not exist, someone else changed it first, a configuration file owns it, a
value breaks an invariant, or the storage itself cannot serve the request
right now (``StorageError``). Ports raise them instead of driver exceptions, so
the API and CLI can map each one to a stable error code (API-007) without
knowing which database is behind the port (ARCH-001).

Dependencies:
    None beyond the standard library.

Safety:
    Messages name entities, IDs, versions, and file paths. They never include
    credential values; callers must not pass secrets into them.

Example:
    >>> err = ConflictError("device", "0190a3c4", expected_version=3, actual_version=4)
    >>> isinstance(err, DomainError), err.actual_version
    (True, 4)
"""


class DomainError(Exception):
    """Base class for every rule violation the domain or a port reports."""


class ValidationError(DomainError, ValueError):
    """A value breaks a domain invariant or a wire-format rule.

    Attributes:
        field: Name of the offending field, or ``None`` when the problem is
            not tied to one field. The API reports it as field detail.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        """Create the error.

        Args:
            message: Operator-readable explanation of what is wrong.
            field: Offending field name, if any.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message)
        self.field = field


class NotFoundError(DomainError):
    """The requested record does not exist.

    Attributes:
        entity: Entity kind, such as ``"device"``.
        key: The ID or natural key that was looked up, as text.
    """

    def __init__(self, entity: str, key: object) -> None:
        """Create the error.

        Args:
            entity: Entity kind, such as ``"device"``.
            key: The ID or natural key that was looked up.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"{entity} {key} does not exist")
        self.entity = entity
        self.key = str(key)


class ConflictError(DomainError):
    """A write was based on a stale version of the record (DB-003).

    Optimistic concurrency: every editable record carries an integer
    ``version``. A write must name the version it read; if the stored version
    differs, someone else changed the record and the write is refused rather
    than silently overwriting their change.

    Attributes:
        entity: Entity kind, such as ``"device"``.
        key: Record ID as text.
        expected_version: Version the caller based its change on.
        actual_version: Version currently stored.
    """

    def __init__(
        self, entity: str, key: object, *, expected_version: int, actual_version: int
    ) -> None:
        """Create the error.

        Args:
            entity: Entity kind, such as ``"device"``.
            key: Record ID.
            expected_version: Version the caller based its change on.
            actual_version: Version currently stored.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(
            f"{entity} {key} was changed by someone else (edited version "
            f"{expected_version}, stored version {actual_version}); reload and retry"
        )
        self.entity = entity
        self.key = str(key)
        self.expected_version = expected_version
        self.actual_version = actual_version


class DuplicateError(DomainError):
    """A new record reuses an ID or natural key that already exists.

    Attributes:
        entity: Entity kind, such as ``"device"``.
        field: The field that must be unique, such as ``"name"``.
        value: The duplicated value, as text.
    """

    def __init__(self, entity: str, field: str, value: object) -> None:
        """Create the error.

        Args:
            entity: Entity kind, such as ``"device"``.
            field: The field that must be unique.
            value: The duplicated value.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"{entity} with {field} {value!s} already exists")
        self.entity = entity
        self.field = field
        self.value = str(value)


class OwnershipError(DomainError):
    """A writer tried to change a record another source owns (META-008, 11.2).

    File-managed records can only change by editing their file; API-managed
    records are never overwritten by a file reload.

    Attributes:
        entity: Entity kind, such as ``"device"``.
        key: Record ID as text.
        owner: The owning source, such as ``"file:/etc/vakr/inventory.toml"``.
        writer: The source that attempted the write.
    """

    def __init__(self, entity: str, key: object, *, owner: str, writer: str) -> None:
        """Create the error.

        Args:
            entity: Entity kind, such as ``"device"``.
            key: Record ID.
            owner: The owning source in ``kind:source`` form.
            writer: The attempting source in ``kind:source`` form.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(
            f"{entity} {key} is managed by {owner} and cannot be changed by {writer}; "
            "edit it at its source instead"
        )
        self.entity = entity
        self.key = str(key)
        self.owner = owner
        self.writer = writer


# Appended by M2-meta (SQLite metadata adapter). The classes above are unchanged.


class StorageError(DomainError):
    """The storage backend could not complete an operation (busy, full, read-only, damaged).

    Unlike the errors above, the request itself may be fine. Adapters raise
    this instead of a driver exception, so no SQL, bound value, or driver
    message reaches an operator, and callers report a storage condition,
    never a target-down state (blueprint 4.3).

    Attributes:
        retryable: ``True`` when the same request may succeed shortly
            (lock contention); ``False`` when an operator must act first.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        """Create the error.

        Args:
            message: Operator-readable explanation, naming the database, not its contents.
            retryable: Whether retrying the same request may succeed.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message)
        self.retryable = retryable
