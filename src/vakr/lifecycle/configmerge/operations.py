r"""Apply migration operations to comment-preserving TOML documents.

Operators annotate their configuration with comments that explain why a
circuit or threshold is set the way it is; an upgrade that drops them
destroys institutional knowledge. These operations edit ``tomlkit``
documents so values keep their inline comments, renames keep their position,
and untouched text stays byte-for-byte identical (CFGM-002).

Paths are dotted keys. ``[]`` addresses every element of an array of tables,
for example ``devices[].community`` (CFGM-007 inventory files); with ``[]``
every path of the operation must share the same array prefix.

Dependencies:
    tomlkit: Round-trip TOML editing (through ``tree``).

Safety:
    Operations only mutate the in-memory document. Error messages name keys,
    never values, because values may be secrets typed into the wrong place.

Example:
    >>> import tomlkit
    >>> doc = tomlkit.parse('[update]\ntrack = "stable"  # why\n')
    >>> apply_operation(doc, Operation("rename", {"from": "update.track", "to": "update.channel"}))
    >>> doc.as_string()
    '[update]\nchannel = "stable"  # why\n'
"""

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tomlkit.container import Container

from .migrations import Migration, MigrationError, Operation
from .transforms import converted, mapped, scaled, split, transform
from .tree import (
    Node,
    container_of,
    drop_if_empty,
    ensure_table,
    find_node,
    invalidate,
    locate,
    rename_in_place,
)

_PATH_PARAMS = ("from", "key")


def apply_migrations(doc: Node, migrations: Sequence[Migration]) -> None:
    """Apply every operation of every migration, in order.

    Args:
        doc: A ``tomlkit`` document, modified in place.
        migrations: Migrations from ``migration_path``.

    Returns:
        None

    Raises:
        MigrationError: Naming the migration whose operation failed.
    """
    for migration in migrations:
        for operation in migration.operations:
            try:
                apply_operation(doc, operation)
            except (ValueError, TypeError) as exc:
                raise MigrationError(f"config migration {migration.label} failed: {exc}") from exc


def apply_operation(root: Node, operation: Operation) -> None:
    """Apply one operation; keys that are absent are left alone.

    Args:
        root: Document or table the operation's paths are relative to.
        operation: The operation.

    Returns:
        None

    Raises:
        ValueError: If the operation conflicts with existing keys or the value
            cannot be transformed (``MigrationError`` is a ``ValueError``).
        TypeError: If a path crosses a value that is not a table.
    """
    paths = _paths(operation)
    if not any("[]." in path for path in paths):
        _HANDLERS[operation.op](root, operation.params)
        return
    prefix = paths[0].rpartition("[].")[0]
    if not all(path.startswith(f"{prefix}[].") for path in paths):
        raise MigrationError("paths with [] must share the same array prefix")
    elements = find_node(root, prefix)
    for element in elements if isinstance(elements, list) else []:
        apply_operation(element, _rebase(operation, len(prefix) + 3))


def _moves(operation: Operation) -> bool:
    """Report whether ``to`` is a destination path (rather than a type name).

    Args:
        operation: The operation.

    Returns:
        bool: ``True`` for ``rename`` and ``move_table``.

    Raises:
        None
    """
    return operation.op in {"rename", "move_table"}


def _paths(operation: Operation) -> list[str]:
    """List the key paths an operation reads or writes.

    Args:
        operation: The operation.

    Returns:
        list[str]: Dotted paths.

    Raises:
        None
    """
    params = operation.params
    found = [params[name] for name in _PATH_PARAMS if name in params]
    if _moves(operation):
        found.append(params["to"])
    return [*found, *params.get("into", ())]


def _rebase(operation: Operation, cut: int) -> Operation:
    """Strip a shared ``prefix[].`` from every path of an operation.

    Args:
        operation: Operation whose paths share the prefix.
        cut: Length of ``prefix[].``.

    Returns:
        Operation: The same operation relative to one array element.

    Raises:
        None
    """
    params = dict(operation.params)
    for name in (*_PATH_PARAMS, *(("to",) if _moves(operation) else ())):
        if name in params:
            params[name] = params[name][cut:]
    if "into" in params:
        params["into"] = [path[cut:] for path in params["into"]]
    return Operation(operation.op, params)


def _conflict(source: str, target: str) -> MigrationError:
    """Build the error for a destination key that is already set.

    Args:
        source: Key being moved.
        target: Destination key.

    Returns:
        MigrationError: Error naming both keys.

    Raises:
        None
    """
    return MigrationError(f"cannot move {source}: {target} is already set")


def _rename(root: Node, params: Mapping[str, Any]) -> None:
    """Rename or move one key, keeping its value, comment, and (in-table) position.

    Args:
        root: Document or table.
        params: ``from`` and ``to`` dotted keys.

    Returns:
        None

    Raises:
        MigrationError: If ``to`` is already set.
        TypeError: If ``to`` lies inside a non-table.
    """
    found = locate(root, params["from"])
    if found is None:
        return
    parent, leaf = found
    source_table = params["from"].rpartition(".")[0]
    table, _, target_leaf = params["to"].rpartition(".")
    item = parent[leaf]
    body = container_of(parent)
    if table == source_table and isinstance(body, Container):
        if target_leaf in parent:
            raise _conflict(params["from"], params["to"])
        rename_in_place(body, leaf, target_leaf)
    else:
        target = ensure_table(root, table)
        if target_leaf in target:
            raise _conflict(params["from"], params["to"])
        del parent[leaf]
        target[target_leaf] = item
        drop_if_empty(root, source_table)
    invalidate(item)


def _move_table(root: Node, params: Mapping[str, Any]) -> None:
    """Move a whole table, merging into the destination when it already exists.

    Args:
        root: Document.
        params: ``from`` and ``to`` dotted table names.

    Returns:
        None

    Raises:
        MigrationError: If either side is not a table or keys collide.
    """
    source, target = find_node(root, params["from"]), find_node(root, params["to"])
    if source is None:
        return
    if not isinstance(source, Mapping) or (target is not None and not isinstance(target, Mapping)):
        raise MigrationError(f"move_table needs tables: {params['from']} -> {params['to']}")
    if target is None:
        _rename(root, params)
        return
    for key in list(source):
        _rename(root, {"from": f"{params['from']}.{key}", "to": f"{params['to']}.{key}"})


def _remove(root: Node, params: Mapping[str, Any]) -> None:
    """Delete a key or table if present.

    Args:
        root: Document or table.
        params: ``key``.

    Returns:
        None

    Raises:
        None
    """
    found = locate(root, params["key"])
    if found is not None:
        del found[0][found[1]]
        drop_if_empty(root, params["key"].rpartition(".")[0])


def _set(root: Node, params: Mapping[str, Any]) -> None:
    """Set a key to a value, creating tables as needed.

    Args:
        root: Document or table.
        params: ``key`` and ``value``.

    Returns:
        None

    Raises:
        TypeError: If the key's table path crosses a non-table.
    """
    table, _, leaf = params["key"].rpartition(".")
    ensure_table(root, table)[leaf] = copy.deepcopy(params["value"])


_HANDLERS: Mapping[str, Callable[[Node, Mapping[str, Any]], None]] = {
    "rename": _rename,
    "move_table": _move_table,
    "remove": _remove,
    "set": _set,
    "map_values": lambda root, p: transform(root, p, lambda v: mapped(v, p["map"])),
    "scale": lambda root, p: transform(root, p, lambda v: scaled(v, p["factor"])),
    "convert": lambda root, p: transform(root, p, lambda v: converted(v, p)),
    "split": split,
}
