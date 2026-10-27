"""Refuse an edit of a file-managed record, naming the file and each field (META-008, CONF-008).

A record ``inventory.toml`` manages changes only by editing the file and
running ``vakr inventory apply``; an edit through the web UI, API, or CLI
would be undone by the next apply, so it is refused, never applied
silently. The refusal is an ``OwnershipError`` (409 ``managed_by_file`` in
the API) whose message names the managing file and the command to run,
and which lists every field the edit would have changed, so the web UI can
say so beside each one.

Dependencies:
    vakr.domain: Ownership and the error.

Safety:
    Messages name records and files only.

Example:
    >>> from vakr.inventory.model import FILE_OWNER
    >>> err = FileManagedError("device", "edge1", FILE_OWNER, ("address",))
    >>> err.fields, err.source
    (('address',), 'inventory.toml')
"""

from collections.abc import Sequence

from vakr.domain.enums import ManagedByKind
from vakr.domain.errors import OwnershipError
from vakr.domain.ownership import API_MANAGED, ManagedBy


class FileManagedError(OwnershipError):
    """An edit would change fields of a record a configuration file manages.

    Attributes:
        fields: The fields the edit would have changed, as the edit names them.
        source: The managing file, such as ``inventory.toml``.
    """

    def __init__(self, entity: str, name: str, owner: ManagedBy, fields: Sequence[str]) -> None:
        """Build the refusal.

        Args:
            entity: ``device`` or ``interface``.
            name: The record as operators name it, such as ``edge1 Gi0/0/0``.
            owner: The managing source.
            fields: The fields the edit would have changed.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(entity, name, owner=str(owner), writer=str(API_MANAGED))
        self.fields = tuple(fields)
        self.source = owner.source
        where = "its [[interface_annotations]] entry" if entity == "interface" else "it"
        self.args = (f"{entity} {name} is managed by {owner.source}: change {where} there and "
                     "run `vakr inventory apply` (META-008)",)  # fmt: skip


def require_api_owner(entity: str, name: str, owner: ManagedBy, fields: Sequence[str]) -> None:
    """Refuse when a record is not API-managed and the edit would change something.

    Args:
        entity: ``device`` or ``interface``.
        name: The record as operators name it.
        owner: The record's owner.
        fields: The fields the edit would change; empty changes nothing.

    Returns:
        None

    Raises:
        FileManagedError: If a file manages the record and ``fields`` is not empty.
    """
    if fields and owner.kind is not ManagedByKind.API:
        raise FileManagedError(entity, name, owner, fields)


def owner_text(owner: ManagedBy) -> str:
    """Describe an owner in operator words.

    Args:
        owner: The owning source.

    Returns:
        str: ``API-managed`` or ``managed by inventory.toml``.

    Raises:
        None
    """
    return "API-managed" if owner.kind is ManagedByKind.API else f"managed by {owner.source}"
