"""Record ownership: whether a configuration file or the API owns a record (META-008).

vakr accepts inventory from two places: files under ``/etc/vakr`` that
operators keep in version control, and the web UI/API. A record belongs to
exactly one of them. A file reload must never overwrite an edit someone made
in the UI, and the UI must never edit a record whose truth lives in a file,
because the next reload would silently undo it (blueprint 11.2).

Dependencies:
    dataclasses: Frozen, slotted value objects.
    vakr.domain.checks: Shared invariant checks.

Safety:
    ``source`` holds a file path or channel name, never credentials.

Example:
    >>> owner = ManagedBy.file("/etc/vakr/inventory/devices.toml")
    >>> owner.permits(API_MANAGED), owner.permits(owner), str(API_MANAGED)
    (False, True, 'api:api')
"""

from dataclasses import dataclass

from vakr.domain.checks import require_enum, require_instance, require_int, require_name
from vakr.domain.enums import ManagedByKind


@dataclass(frozen=True, slots=True)
class ManagedBy:
    """Which source owns a configurable record (META-008, blueprint 11.2).

    Attributes:
        kind: ``FILE`` for records loaded from configuration, ``API`` for
            records created through the UI or API.
        source: For ``FILE``, the configuration file path; for ``API``, the
            channel that created the record (``"api"`` by default).
    """

    kind: ManagedByKind
    source: str = "api"

    def __post_init__(self) -> None:
        """Validate the owner.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If ``kind`` is not a ``ManagedByKind`` or
                ``source`` is not a valid name.
        """
        require_enum(self.kind, ManagedByKind, "managed_by.kind")
        require_name(self.source, "managed_by.source")

    @classmethod
    def file(cls, path: str) -> ManagedBy:
        """Build the owner for a record loaded from a configuration file.

        Args:
            path: The file the record came from.

        Returns:
            ManagedBy: ``file:<path>``.

        Raises:
            ValidationError: If ``path`` is not a valid name.
        """
        return cls(ManagedByKind.FILE, path)

    def permits(self, writer: ManagedBy) -> bool:
        """Tell whether ``writer`` may change a record this source owns.

        A file may only change records it created, and the API may only
        change API-managed records, so a file reload never overwrites an API
        edit and the UI never edits a file-managed record (11.2).

        Args:
            writer: The source attempting the write.

        Returns:
            bool: ``True`` when the write is allowed.

        Raises:
            None
        """
        if self.kind is not writer.kind:
            return False
        return self.kind is ManagedByKind.API or self.source == writer.source

    def __str__(self) -> str:
        """Render as ``kind:source``, the form used in messages and exports.

        Args:
            None

        Returns:
            str: For example ``file:/etc/vakr/inventory/devices.toml``.

        Raises:
            None
        """
        return f"{self.kind}:{self.source}"


API_MANAGED = ManagedBy(ManagedByKind.API)
"""The owner of records created through the UI or API."""

RECORD_FIXED_FIELDS = ("id", "managed_by")
"""Fields ``update`` may never change on a site, tag, group, policy, or collector."""


def require_record(version: object, managed_by: object) -> None:
    """Check the fields every versioned, owned record shares.

    Args:
        version: Optimistic-concurrency version; starts at 1.
        managed_by: The owning source.

    Returns:
        None

    Raises:
        ValidationError: If ``version`` is not a positive integer or
            ``managed_by`` is not a ``ManagedBy``.
    """
    require_int(version, "version", minimum=1)
    require_instance(managed_by, ManagedBy, "managed_by")
