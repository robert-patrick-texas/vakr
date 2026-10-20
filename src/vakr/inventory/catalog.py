"""Find or create the tags and groups that inventory and discovery refer to by name.

Operators write tag and group *names* in ``inventory.toml``, and
description keywords produce tag names (META-012), but device and interface
records hold tag and group UUIDs. These helpers resolve names inside a unit
of work and create any that do not exist yet, owned by the caller's
source. An existing record is only referenced, never taken over, so a tag
the UI created stays API-managed even when a file mentions it.

Dependencies:
    vakr.domain, vakr.ports.metadata: Records and repositories.

Safety:
    Names are validated by the domain records; nothing is deleted.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> with memory_uow_factory()() as uow:
    ...     len(ensure_tags(uow, ("wan", "isp", "wan"), API_MANAGED, "example"))
    2
"""

from collections.abc import Iterable

from vakr.domain.catalog import Group, Tag
from vakr.domain.ids import GroupId, TagId, new_id
from vakr.domain.ownership import API_MANAGED, ManagedBy
from vakr.ports.metadata import MetadataUnitOfWork

__all__ = ["API_MANAGED", "ensure_groups", "ensure_tags", "group_names", "tag_names"]


def ensure_tags(
    uow: MetadataUnitOfWork, names: Iterable[str], owner: ManagedBy, why: str
) -> frozenset[TagId]:
    """Return the IDs of tags by name, creating missing ones.

    Args:
        uow: The open unit of work.
        names: Tag names; repeats are ignored.
        owner: Owner of any tag created here.
        why: Description stored on created tags.

    Returns:
        frozenset[TagId]: One ID per distinct name.

    Raises:
        ValidationError: If a name is not a valid tag name.
        DuplicateError: If another unit of work created the same tag first.
    """
    found: set[TagId] = set()
    for name in sorted(set(names)):
        tag = uow.tags.get_by_name(name)
        if tag is None:
            tag = uow.tags.add(Tag(new_id(TagId), name, owner, why))
        found.add(tag.id)
    return frozenset(found)


def ensure_groups(
    uow: MetadataUnitOfWork, names: Iterable[str], owner: ManagedBy, why: str
) -> frozenset[GroupId]:
    """Return the IDs of groups by name, creating missing ones.

    Args:
        uow: The open unit of work.
        names: Group names; repeats are ignored.
        owner: Owner of any group created here.
        why: Description stored on created groups.

    Returns:
        frozenset[GroupId]: One ID per distinct name.

    Raises:
        ValidationError: If a name is not a valid group name.
        DuplicateError: If another unit of work created the same group first.
    """
    found: set[GroupId] = set()
    for name in sorted(set(names)):
        group = uow.groups.get_by_name(name)
        if group is None:
            group = uow.groups.add(Group(new_id(GroupId), name, owner, why))
        found.add(group.id)
    return frozenset(found)


def tag_names(uow: MetadataUnitOfWork, ids: Iterable[TagId]) -> tuple[str, ...]:
    """Return the names of tags by ID, sorted; unknown IDs are skipped.

    Args:
        uow: The open unit of work.
        ids: Tag IDs.

    Returns:
        tuple[str, ...]: Names in code-point order.

    Raises:
        None
    """
    return tuple(sorted(t.name for i in ids if (t := uow.tags.get(i)) is not None))


def group_names(uow: MetadataUnitOfWork, ids: Iterable[GroupId]) -> tuple[str, ...]:
    """Return the names of groups by ID, sorted; unknown IDs are skipped.

    Args:
        uow: The open unit of work.
        ids: Group IDs.

    Returns:
        tuple[str, ...]: Names in code-point order.

    Raises:
        None
    """
    return tuple(sorted(g.name for i in ids if (g := uow.groups.get(i)) is not None))
