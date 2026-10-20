r"""Navigate and reshape comment-preserving ``tomlkit`` documents.

Config migrations rename keys and move tables in operator files. These
helpers find, create, and tidy tables while keeping each item's ``tomlkit``
trivia (indentation and inline comment), so the operator's notes travel with
their settings (CFGM-002).

Dependencies:
    tomlkit: Round-trip TOML documents.

Safety:
    Only the in-memory document changes. A table emptied by a move is deleted
    only when it holds no comments, so operator notes are never discarded.

Example:
    >>> import tomlkit
    >>> doc = tomlkit.parse("[a]\nx = 1\n")
    >>> locate(doc, "a.x")[1], find_node(doc, "a.y") is None
    ('x', True)
"""

from collections.abc import Mapping
from typing import Any

import tomlkit
from tomlkit.items import AoT, Comment, InlineTable, Table

# tomlkit's documents, tables, and inline tables share a mutable-mapping
# interface but no common static type, so nodes are typed loosely.
type Node = Any


def find_node(root: Node, dotted: str) -> Node | None:
    """Return the node at ``dotted``, or ``None`` when any part is missing.

    Args:
        root: Starting document or table.
        dotted: Dotted path; ``""`` means ``root``.

    Returns:
        Node | None: The node found.

    Raises:
        None
    """
    node = root
    for part in dotted.split(".") if dotted else ():
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def ensure_table(root: Node, dotted: str) -> Node:
    """Return the table at ``dotted``, creating missing tables.

    Args:
        root: Starting document or table.
        dotted: Dotted table path; ``""`` means ``root``.

    Returns:
        Node: The existing or new table.

    Raises:
        TypeError: If a path component exists but is not a table.
    """
    node = root
    parts = dotted.split(".") if dotted else []
    for index, part in enumerate(parts):
        if part not in node:
            # Intermediate tables render no header of their own: [a.b], not [a] + [a.b].
            node.add(part, tomlkit.table(is_super_table=index < len(parts) - 1))
        node = node[part]
        if not isinstance(node, Mapping):
            raise TypeError(f"{'.'.join(parts[: index + 1])} is not a table")
    return node


def locate(root: Node, dotted: str) -> tuple[Node, str] | None:
    """Locate an existing key as ``(parent, leaf)``.

    Args:
        root: Starting document or table.
        dotted: Dotted key.

    Returns:
        tuple[Node, str] | None: Parent table and key, or ``None`` if absent.

    Raises:
        None
    """
    table, _, leaf = dotted.rpartition(".")
    parent = find_node(root, table)
    return (parent, leaf) if isinstance(parent, Mapping) and leaf in parent else None


def plain(item: Any) -> Any:
    """Convert a ``tomlkit`` item to the plain Python value it holds.

    Args:
        item: ``tomlkit`` item or plain value.

    Returns:
        Any: Plain value.

    Raises:
        None
    """
    return item.unwrap() if hasattr(item, "unwrap") else item


def container_of(node: Node) -> Node:
    """Return the ``Container`` holding a table's items.

    Args:
        node: Document, table, or inline table.

    Returns:
        Node: The underlying container (a document is its own).

    Raises:
        None
    """
    return node.value if isinstance(node, Table | InlineTable) else node


def rename_in_place(container: Node, old: str, new: str) -> Any:
    """Rename a key inside one ``Container`` without moving it.

    Args:
        container: A ``tomlkit`` document or a table's ``Container``.
        old: Existing key.
        new: New key, not yet present.

    Returns:
        Any: The moved item, with its trivia (inline comment) intact.

    Raises:
        None
    """
    index = next((i for i, (k, _) in enumerate(container.body) if k and k.key == old), None)
    item = container.item(old)
    container.remove(old)
    if index is None:
        container.add(new, item)
    else:
        # tomlkit has no public rename. add() would move the key to the end of
        # its table, away from the comment lines above it, and _replace leaves
        # tomlkit's key index stale for plain values, so insert at the old slot.
        container._insert_at(index, new, item)
    return item


def invalidate(item: Any) -> None:
    """Make a moved table, and the tables inside it, render under their new names.

    ``tomlkit`` caches each table's header text; without this a renamed
    ``[upgrade]`` table would still print its old header.

    Args:
        item: Moved ``tomlkit`` item.

    Returns:
        None

    Raises:
        None
    """
    if isinstance(item, Table | AoT):
        item.invalidate_display_name()
    children = [c for _, c in item.value.body] if isinstance(item, Table) else []
    for child in children + (list(item.body) if isinstance(item, AoT) else []):
        invalidate(child)


def drop_if_empty(root: Node, dotted: str) -> None:
    """Delete a table left without keys, unless it still holds operator comments.

    Args:
        root: Document.
        dotted: Table that just lost a key; ``""`` (top level) is never dropped.

    Returns:
        None

    Raises:
        None
    """
    found = locate(root, dotted) if dotted else None
    if found is None:
        return
    parent, leaf = found
    table = parent[leaf]
    if not isinstance(table, Table) or table.value.keys():
        return
    # An emptied table that carries comments keeps them; validation ignores
    # empty tables, so the operator's notes survive the move.
    if not table.trivia.comment and not any(isinstance(i, Comment) for _, i in table.value.body):
        del parent[leaf]
