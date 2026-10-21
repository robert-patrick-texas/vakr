"""JSON values back into query view models: the inverse of ``to_jsonable`` (TUI-007, E.29).

``vakr status`` and ``vakr tui`` read the API's answers as the very views the
in-process services return, so the renderers, the exit codes, and the
screens cannot tell the two data paths apart. ``from_jsonable(kind, value)``
rebuilds a view from its type hints with the standard library only:

- a dataclass from an object of its fields. Unknown keys are ignored, so an
  answer may carry more than the view (``page``, or a field a later release
  adds); a missing field without a default is an error;
- a tuple, list, or frozenset from an array; ``tuple[A, B]`` takes exactly
  two items;
- ``X | None`` and other unions by trying each arm in order;
- an enum from its value, a time from ISO 8601 with an offset (``Z``), and
  a UUID, or a ``NewType`` over one, from its text;
- ``int`` from an integer and ``float`` from any number, never from a
  boolean. Numbers are kept exactly as sent, so an ``int`` stays an ``int``.

Anything else raises ``ContractError``, naming where in the answer it went
wrong; callers treat that as an API they cannot use.

Dependencies:
    dataclasses, enum, functools, types, typing: Standard library only.
    vakr.query.from_json_leaves: Enums, scalars, times, and UUIDs.

Safety:
    Bounded to ``MAX_DEPTH`` levels and ``MAX_NODES`` values per answer
    (E.29), on top of the transport's byte limit. Messages name the path and
    the expected type, never the value received.

Example:
    >>> from vakr.query.views import Reading
    >>> from_jsonable(Reading, {"text": "12 s", "value": 12, "later": "ignored"})
    Reading(text='12 s', value=12, reason=None, stale=False)
"""

import dataclasses
import functools
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, NewType, TypeAliasType, cast

from vakr.domain.errors import ValidationError
from vakr.query.from_json_leaves import ContractError, decode_enum, decode_scalar

__all__ = ["MAX_DEPTH", "MAX_NODES", "ContractError", "from_jsonable"]

MAX_DEPTH = 32
"""Deepest nesting decoded; the status views nest six levels (E.29)."""
MAX_NODES = 1_000_000
"""Most values one answer may hold; a 2,000-interface device screen holds about 112,000 (E.29)."""
_SEQUENCES = (tuple, list, frozenset)
_UNIONS = (typing.Union, types.UnionType)


class _BoundError(ContractError):
    """An E.29 bound was broken; no union arm may try again."""


@dataclass(slots=True)
class _Walk:
    """How far one decoding has got.

    Attributes:
        nodes: Values decoded so far.
    """

    nodes: int = 0


def from_jsonable[T](kind: type[T], value: object) -> T:
    """Rebuild a value of ``kind`` from what ``to_jsonable`` and ``json.loads`` made of it.

    Args:
        kind: A view dataclass, or any type its fields use.
        value: Parsed JSON.

    Returns:
        T: The value.

    Raises:
        ContractError: If ``value`` does not fit ``kind`` or breaks a bound.
        TypeError: If ``kind`` uses a type this module does not know (a programming error).
    """
    return cast(T, _decode(kind, value, "$", 0, _Walk()))


def _decode(kind: Any, value: object, path: str, depth: int, walk: _Walk) -> object:
    """Decode one value by its type.

    Args:
        kind: The expected type (a class, alias, ``NewType``, or generic).
        value: Parsed JSON.
        path: Where ``value`` sits in the answer, such as ``$.rows[3].status``.
        depth: Nesting level.
        walk: The node count.

    Returns:
        object: The decoded value.

    Raises:
        ContractError: If the value does not fit or a bound is broken.
        TypeError: If the type is not supported.
    """
    if depth > MAX_DEPTH:
        raise _BoundError(f"{path}: nested deeper than {MAX_DEPTH} levels")
    walk.nodes += 1
    if walk.nodes > MAX_NODES:
        raise _BoundError(f"the answer holds more than {MAX_NODES} values")
    while isinstance(kind, TypeAliasType | NewType):
        kind = kind.__value__ if isinstance(kind, TypeAliasType) else kind.__supertype__
    origin = typing.get_origin(kind)
    if origin in _UNIONS:
        return _union(typing.get_args(kind), value, path, depth, walk)
    if origin is not None:
        return _generic(origin, typing.get_args(kind), value, path, depth, walk)
    if dataclasses.is_dataclass(kind) and isinstance(kind, type):
        return _dataclass(kind, value, path, depth, walk)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return decode_enum(kind, value, path)
    return decode_scalar(kind, value, path)


def _generic(
    origin: Any, args: tuple[Any, ...], value: object, path: str, depth: int, walk: _Walk
) -> object:
    """Decode a parametrised container: a sequence or a mapping with text keys.

    Args:
        origin: ``tuple``, ``list``, ``frozenset``, ``dict``, or ``Mapping``.
        args: Its type arguments.
        value: Parsed JSON.
        path: Where it sits.
        depth: Nesting level.
        walk: The node count.

    Returns:
        object: The container.

    Raises:
        ContractError: If the value does not fit.
        TypeError: If the container is not supported.
    """
    if origin in _SEQUENCES:
        return _sequence(origin, args, value, path, depth, walk)
    if origin is dict or origin is Mapping:
        return _mapping(args[1], value, path, depth, walk)
    raise TypeError(f"{path}: {origin!r} is not a container from_jsonable decodes")


def _union(arms: tuple[Any, ...], value: object, path: str, depth: int, walk: _Walk) -> object:
    """Decode by the first arm of a union that fits.

    Args:
        arms: The union's types, in declaration order.
        value: Parsed JSON.
        path: Where it sits.
        depth: Nesting level.
        walk: The node count.

    Returns:
        object: The value; ``None`` for ``null`` when the union allows it.

    Raises:
        ContractError: If no arm fits.
    """
    if value is None:
        if type(None) in arms:
            return None
        raise ContractError(f"{path}: null where a value is required")
    for arm in arms:
        if arm is type(None):
            continue
        try:
            return _decode(arm, value, path, depth, walk)
        except _BoundError:
            raise
        except ContractError:
            continue
    names = " | ".join(getattr(a, "__name__", str(a)) for a in arms)
    raise ContractError(f"{path}: expected {names}, got {type(value).__name__}")


def _sequence(
    origin: type, args: tuple[Any, ...], value: object, path: str, depth: int, walk: _Walk
) -> object:
    """Decode an array into a tuple, list, or frozenset.

    Args:
        origin: ``tuple``, ``list``, or ``frozenset``.
        args: The item types; ``(X, ...)`` for a variable-length tuple.
        value: Parsed JSON.
        path: Where it sits.
        depth: Nesting level.
        walk: The node count.

    Returns:
        object: The container.

    Raises:
        ContractError: If the value is not an array or a fixed tuple has the wrong length.
        TypeError: If the generic has no item type.
    """
    if not args:
        raise TypeError(f"{path}: a {origin.__name__} needs an item type")
    if not isinstance(value, list):
        raise ContractError(f"{path}: expected an array, got {type(value).__name__}")
    if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
        if len(value) != len(args):
            raise ContractError(f"{path}: expected {len(args)} items, got {len(value)}")
        return tuple(
            _decode(a, v, f"{path}[{i}]", depth + 1, walk)
            for i, (a, v) in enumerate(zip(args, value, strict=True))
        )
    items = [_decode(args[0], v, f"{path}[{i}]", depth + 1, walk) for i, v in enumerate(value)]
    return origin(items)


def _mapping(item: Any, value: object, path: str, depth: int, walk: _Walk) -> object:
    """Decode an object with text keys into a dict.

    Args:
        item: The value type.
        value: Parsed JSON.
        path: Where it sits.
        depth: Nesting level.
        walk: The node count.

    Returns:
        object: The dict.

    Raises:
        ContractError: If the value is not an object.
    """
    if not isinstance(value, dict):
        raise ContractError(f"{path}: expected an object, got {type(value).__name__}")
    return {str(k): _decode(item, v, f"{path}.{k}", depth + 1, walk) for k, v in value.items()}


@functools.cache
def _hints(kind: type) -> dict[str, Any]:
    """Return a dataclass's resolved field types, once per class.

    Args:
        kind: The dataclass.

    Returns:
        dict[str, Any]: Field name to type.

    Raises:
        NameError: If an annotation cannot be resolved (a programming error).
    """
    return typing.get_type_hints(kind)


def _dataclass(kind: type, value: object, path: str, depth: int, walk: _Walk) -> object:
    """Build a dataclass from an object of its fields; unknown keys are ignored.

    Args:
        kind: The dataclass.
        value: Parsed JSON.
        path: Where it sits.
        depth: Nesting level.
        walk: The node count.

    Returns:
        object: The instance.

    Raises:
        ContractError: If the value is not an object, a required field is
            missing, or the class refuses the values.
    """
    if not isinstance(value, dict):
        raise ContractError(f"{path}: expected an object, got {type(value).__name__}")
    hints = _hints(kind)
    values: dict[str, object] = {}
    for field in dataclasses.fields(cast(Any, kind)):  # is_dataclass was checked by the caller
        if not field.init:
            continue
        if field.name in value:
            where, item = f"{path}.{field.name}", value[field.name]
            values[field.name] = _decode(hints[field.name], item, where, depth + 1, walk)
        elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            raise ContractError(f"{path}.{field.name}: missing")
    try:
        return kind(**values)
    except TypeError, ValueError, ValidationError:
        raise ContractError(f"{path}: not a valid {kind.__name__}") from None
