r"""JSON-compatible values and their canonical byte form (ARCH-002, blueprint 6.3).

Policy documents and observation envelopes travel between processes,
languages, and databases as JSON. Two producers that hold the same values
must emit the same bytes, or checksums and golden fixtures cannot prove
compatibility (blueprint 24). The canonical form is:

- UTF-8, with object keys sorted by code point and no insignificant
  whitespace (``","`` and ``":"`` separators);
- non-ASCII characters written raw, not as ``\u`` escapes; ``"``, ``\``,
  and control characters escaped as Python's ``json`` module does;
- integers in plain decimal, exact at any size (counters reach 2^64 - 1);
- floats in the shortest form that round-trips, as Python ``repr`` writes
  it (``1.0``, ``0.0001``, ``1e-05``, ``1e+16``); NaN and infinity are
  not allowed;
- ``true``, ``false``, and ``null``; arrays are JSON arrays.

Only ``dict`` (string keys), ``list``, ``str``, ``int``, ``float``, ``bool``,
and ``None`` are JSON values here. Tuples are refused because they would
come back as lists and break exact round trips.

Dependencies:
    json: Encoding with ``sort_keys`` and compact separators.
    vakr.domain.errors: ``ValidationError``.

Safety:
    Depth is bounded so a hostile document cannot exhaust the stack.

Example:
    >>> canonical_json({"b": [1, 2.5, None], "a": "Zürich"})
    b'{"a":"Z\xc3\xbcrich","b":[1,2.5,null]}'
"""

import json
import math
from collections.abc import Mapping
from typing import Any

from vakr.domain.errors import ValidationError

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
"""A value JSON represents exactly."""

MAX_JSON_DEPTH = 32  # far beyond any policy or payload we write; stops runaway recursion


def require_json(value: object, field: str, *, _depth: int = 0) -> None:
    """Require a JSON-compatible value tree.

    Args:
        value: Candidate value.
        field: Field name for the error message.
        _depth: Current nesting depth; callers leave it at 0.

    Returns:
        None

    Raises:
        ValidationError: If the tree contains a type JSON cannot represent
            exactly, a non-string key, a non-finite float, or is nested
            deeper than ``MAX_JSON_DEPTH``.
    """
    if _depth > MAX_JSON_DEPTH:
        raise ValidationError(f"{field} is nested too deeply", field=field)
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{field} must not contain NaN or infinity", field=field)
        return
    if isinstance(value, list):
        for item in value:
            require_json(item, field, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{field} keys must be strings", field=field)
            require_json(item, field, _depth=_depth + 1)
        return
    raise ValidationError(f"{field} contains a {type(value).__name__}, not JSON", field=field)


def require_json_object(value: object, field: str) -> None:
    """Require a ``dict`` whose values are JSON-compatible.

    Args:
        value: Candidate document.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a dict of JSON values.
    """
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be a JSON object", field=field)
    require_json(value, field)


def canonical_json(value: Mapping[str, Any] | list[Any]) -> bytes:
    """Encode a JSON value in the canonical byte form described above.

    Args:
        value: A JSON-compatible object or array.

    Returns:
        bytes: Canonical UTF-8 JSON.

    Raises:
        ValidationError: If ``value`` is not JSON-compatible or holds a lone
            surrogate that UTF-8 cannot encode.
    """
    require_json(value, "value")
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValidationError("value contains text that is not valid Unicode") from None


def parse_json_object(data: bytes) -> dict[str, Any]:
    """Parse UTF-8 JSON bytes that must hold one object.

    Args:
        data: Encoded JSON.

    Returns:
        dict[str, Any]: The parsed object.

    Raises:
        ValidationError: If ``data`` is not UTF-8, not JSON, contains NaN or
            infinity, or is not an object.
    """
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"not valid UTF-8 JSON: {exc}") from None
    if not isinstance(value, dict):
        raise ValidationError("JSON document must be an object")
    return value


def _refuse_constant(name: str) -> None:
    """Reject the non-standard ``NaN`` and ``Infinity`` literals Python accepts.

    Args:
        name: The literal the parser met.

    Returns:
        None

    Raises:
        ValidationError: Always.
    """
    raise ValidationError(f"JSON must not contain {name}")
