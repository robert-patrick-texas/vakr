"""Generic repository protocols: the five operations most records share (5.2).

``EntityRepository`` gives get, list, add, and update for any versioned,
owned record; ``NamedRepository`` adds lookup by the unique ``name``
natural key. The concrete repositories in ``vakr.ports.metadata.repository``
specialise them, and that module's docstring states the error and ordering
rules every backend must follow.

Dependencies:
    vakr.domain: Ownership and paging types.

Safety:
    Implementations return domain records, never driver rows (rule 2).

Example:
    >>> sorted(n for n in dir(NamedRepository) if not n.startswith("_"))
    ['add', 'get', 'get_by_name', 'list_page', 'update']
"""

from typing import Protocol

from vakr.domain.ownership import ManagedBy
from vakr.domain.paging import Page, PageRequest


class EntityRepository[E, I, F](Protocol):
    """Get, list, add, and update one kind of versioned, owned record."""

    def get(self, entity_id: I) -> E | None:
        """Return the record with this ID, retired or not.

        Args:
            entity_id: The record's UUID.

        Returns:
            E | None: The record, or ``None`` if it does not exist.

        Raises:
            None
        """
        ...

    def list_page(self, page: PageRequest, where: F | None = None) -> Page[E]:
        """Return one page of matching records in the repository's stable order.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None`` for the default filter.

        Returns:
            Page[E]: The records, the next cursor, and the total match count.

        Raises:
            ValidationError: If ``page.cursor`` is not a cursor this
                repository issued.
        """
        ...

    def add(self, entity: E) -> E:
        """Store a new record.

        Args:
            entity: The record, with ``version == 1``.

        Returns:
            E: The stored record.

        Raises:
            DuplicateError: If the ID or natural key is already used.
            NotFoundError: If a referenced record does not exist.
            ValidationError: If ``version`` is not 1.
        """
        ...

    def update(self, entity: E, *, writer: ManagedBy) -> E:
        """Replace a record, checking ownership and the optimistic version.

        Args:
            entity: The new content; ``version`` is the version it was based on.
            writer: The source making the change (META-008).

        Returns:
            E: The stored record, with ``version`` incremented.

        Raises:
            NotFoundError: If the record does not exist, or a referenced one does not.
            OwnershipError: If ``writer`` does not own the record.
            ConflictError: If the record changed since ``entity.version``.
            ValidationError: If a fixed field (``id``, ``managed_by``, ...) changed.
            DuplicateError: If a rename collides with another record's name.
        """
        ...


class NamedRepository[E, I, F](EntityRepository[E, I, F], Protocol):
    """A repository whose records have a unique ``name`` natural key."""

    def get_by_name(self, name: str) -> E | None:
        """Return the record with this exact name.

        Args:
            name: The natural key, compared exactly.

        Returns:
            E | None: The record, or ``None``.

        Raises:
            None
        """
        ...
