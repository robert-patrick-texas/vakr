r"""Value-changing migration operations: value maps, unit scaling, conversion, splits.

Releases sometimes change what a setting means rather than where it lives:
an enum value is renamed, seconds become milliseconds, or an integer becomes
a duration string. These operations rewrite such values in place, keeping
the key's inline comment (CFGM-002).

Dependencies:
    tree: Locating keys in ``tomlkit`` documents.
    values: Duration parsing to check converted values.

Safety:
    Error messages name the key and the conversion, never the value.

Example:
    >>> import tomlkit
    >>> doc = tomlkit.parse("[poll]\ntimeout = 2  # seconds\n")
    >>> transform(doc, {"key": "poll.timeout"}, lambda v: scaled(v, 1000))
    >>> doc.as_string()
    '[poll]\ntimeout = 2000  # seconds\n'
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any

from .tree import Node, drop_if_empty, ensure_table, locate, plain
from .values import parse_duration


class TransformError(ValueError):
    """A present value could not be transformed."""


def transform(root: Node, params: Mapping[str, Any], change: Callable[[Any], Any]) -> None:
    """Replace a present value with ``change(value)``, keeping its inline comment.

    Args:
        root: Document or table.
        params: ``key`` plus transform parameters.
        change: Function of the plain value; raises ``ValueError`` or
            ``TypeError`` when the value cannot be transformed.

    Returns:
        None

    Raises:
        TransformError: If ``change`` rejects the value.
    """
    found = locate(root, params["key"])
    if found is None:
        return
    parent, leaf = found
    try:
        parent[leaf] = change(plain(parent[leaf]))
    except (ValueError, TypeError) as exc:
        raise TransformError(f"{params['key']}: {exc}") from exc


def mapped(value: Any, mapping: Mapping[str, Any]) -> Any:
    """Translate a renamed enum value; other values pass through.

    Args:
        value: Current value.
        mapping: Old value to new value.

    Returns:
        Any: The mapped value, or ``value`` when it has no mapping.

    Raises:
        None
    """
    return mapping.get(value, value) if isinstance(value, str) else value


def scaled(value: Any, factor: float) -> Any:
    """Multiply a number for a unit change, keeping integers integral when exact.

    Args:
        value: Current value.
        factor: Multiplier, such as ``1000`` for seconds to milliseconds.

    Returns:
        Any: The scaled number.

    Raises:
        TypeError: If ``value`` is not a number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("expected a number to scale")
    result = value * factor
    return int(result) if isinstance(value, int) and float(result).is_integer() else result


def converted(value: Any, params: Mapping[str, Any]) -> Any:
    """Change a value's type, for example integer seconds to a duration string.

    Args:
        value: Current value.
        params: ``to`` (``str``, ``int``, ``float``, ``duration``, ``list``),
            optional ``unit`` for durations and ``separator`` for lists.

    Returns:
        Any: The converted value.

    Raises:
        TypeError: If the value cannot be converted.
        ValueError: If a value does not parse as the target type.
    """
    target = params["to"]
    if target == "list":
        return _as_list(value, params.get("separator"))
    if isinstance(value, list | dict | bool):
        raise TypeError(f"cannot convert this value to {target}")
    if target == "duration":
        if not isinstance(value, str) and int(value) != value:
            raise ValueError("only whole numbers convert to a duration")
        text = value if isinstance(value, str) else f"{int(value)}{params.get('unit', 's')}"
        parse_duration(text)
        return text
    return {"str": str, "int": int, "float": float}[target](value)


def split(root: Node, params: Mapping[str, Any]) -> None:
    """Split one key into several, by separator or by copying the value.

    Args:
        root: Document or table.
        params: ``from``, ``into`` (list of keys), optional ``separator``.

    Returns:
        None

    Raises:
        TransformError: If the value does not split into exactly one part
            per target, or a target is already set.
    """
    found = locate(root, params["from"])
    if found is None:
        return
    parent, leaf = found
    value, targets = plain(parent[leaf]), list(params["into"])
    parts = [value] * len(targets)
    if (separator := params.get("separator")) is not None:
        pieces = value.split(separator, len(targets) - 1) if isinstance(value, str) else []
        if len(pieces) != len(targets):
            raise TransformError(f"{params['from']}: cannot split into {len(targets)} parts")
        parts = [piece.strip() for piece in pieces]
    for target, part in zip(targets, parts, strict=True):
        table, _, target_leaf = target.rpartition(".")
        destination = ensure_table(root, table)
        if target_leaf in destination:
            raise TransformError(f"cannot split {params['from']}: {target} is already set")
        destination[target_leaf] = copy.deepcopy(part)
    del parent[leaf]
    drop_if_empty(root, params["from"].rpartition(".")[0])


def _as_list(value: Any, separator: str | None) -> list[Any]:
    """Wrap a scalar in a list, splitting strings on ``separator`` when given.

    Args:
        value: Current value.
        separator: Delimiter for strings such as ``"a,b"``; ``None`` wraps.

    Returns:
        list[Any]: The value as a list; lists pass through.

    Raises:
        None
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str) and separator:
        return [part.strip() for part in value.split(separator) if part.strip()]
    return [value]
