"""Catalog records: tags, groups, and collectors (blueprint 7.1).

Tags and groups let operators slice inventory for navigation, dashboards,
and policy (META-005). Collectors are polling positions; every observation
names the collector that made it so two positions never blur into one
answer (STATE-004). Like every configurable record they carry a version and
an owner.

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, ownership: Shared invariant checks.

Safety:
    No field holds credentials.

Example:
    >>> from vakr.domain.ids import TagId, new_id
    >>> from vakr.domain.ownership import API_MANAGED
    >>> Tag(new_id(TagId), "wan", API_MANAGED).version
    1
"""

from dataclasses import dataclass

from vakr.domain.checks import require_bool, require_name, require_text, require_uuid
from vakr.domain.ids import CollectorId, GroupId, TagId
from vakr.domain.ownership import ManagedBy, require_record


@dataclass(frozen=True, slots=True)
class Tag:
    """A label for navigation, dashboards, policy, and filtering (META-005).

    Attributes:
        id: Immutable tag UUID.
        name: Unique tag name; the natural key.
        managed_by: Owning source.
        description: Free-form text.
        version: Optimistic-concurrency version.
    """

    id: TagId
    name: str
    managed_by: ManagedBy
    description: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the tag.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_name(self.name, "name")
        require_text(self.description, "description")
        require_record(self.version, self.managed_by)


@dataclass(frozen=True, slots=True)
class Group:
    """A named set of devices; membership is recorded on each device (META-005).

    Attributes:
        id: Immutable group UUID.
        name: Unique group name; the natural key.
        managed_by: Owning source.
        description: Free-form text.
        version: Optimistic-concurrency version.
    """

    id: GroupId
    name: str
    managed_by: ManagedBy
    description: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the group.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_name(self.name, "name")
        require_text(self.description, "description")
        require_record(self.version, self.managed_by)


@dataclass(frozen=True, slots=True)
class Collector:
    """A polling position whose observations are kept separately (STATE-004, 27.5).

    Attributes:
        id: Immutable collector UUID; every observation carries it.
        name: Unique collector name; the natural key.
        position: Position label, such as ``east``, shown beside its data.
        managed_by: Owning source.
        enabled: Whether the collector is expected to poll.
        version: Optimistic-concurrency version.
    """

    id: CollectorId
    name: str
    position: str
    managed_by: ManagedBy
    enabled: bool = True
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the collector.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_name(self.name, "name")
        require_name(self.position, "position")
        require_bool(self.enabled, "enabled")
        require_record(self.version, self.managed_by)
