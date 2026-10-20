"""Validate configuration values against their catalog specifications.

Operators make typos such as ``chanel = "stabel"`` or ``rollup_interval =
"15 minutes"``. These checks turn them into precise validation errors before
a service reads the value, which keeps a bad edit from silently changing
collection behaviour (CONF-009, section 11.4).

Dependencies:
    re, urllib.parse, zoneinfo: Pattern, URL, and IANA timezone checks from
        the standard library only.

Safety:
    Messages never repeat the offending value, because an operator may paste
    a secret into the wrong key and validation output ends up in tickets.

Example:
    >>> problems(ValueSpec("enum", choices=("stable", "prerelease")), "beta")
    [('', 'expected one of: stable, prerelease')]
"""

import re
import zoneinfo
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from .values import ValueSpec, parse_duration


def problems(spec: ValueSpec, value: Any) -> list[tuple[str, str]]:
    """List everything wrong with ``value`` under ``spec``.

    Args:
        spec: The specification to check against.
        value: A value parsed from TOML.

    Returns:
        list[tuple[str, str]]: ``(key_suffix, message)`` pairs, where the
        suffix locates a nested problem (``"[2].tag"``) or is ``""`` for the
        value itself. Empty when the value is valid.

    Raises:
        None
    """
    if spec.kind != "list[table]":
        message = _CHECKERS[spec.kind](spec, value)
        return [("", message)] if message else []
    if not isinstance(value, list) or not all(isinstance(v, Mapping) for v in value):
        return [("", "expected an array of tables")]
    found: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        found += [(f"[{index}]{suffix}", text) for suffix, text in _item_problems(spec, item)]
    return found


def with_field_defaults(spec: ValueSpec, items: list[Any]) -> list[dict[str, Any]]:
    """Fill omitted fields of each table from the field defaults.

    Args:
        spec: A ``list[table]`` specification.
        items: Validated tables.

    Returns:
        list[dict[str, Any]]: New tables with fields in catalog order; the
        inputs are not modified.

    Raises:
        None
    """
    return [
        {
            f.name: item.get(f.name, f.default)
            for f in spec.fields
            if f.name in item or f.default is not None
        }
        for item in items
    ]


def _item_problems(spec: ValueSpec, item: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Check one table of a ``list[table]`` value.

    Args:
        spec: The ``list[table]`` specification.
        item: One table from the array.

    Returns:
        list[tuple[str, str]]: ``(".field", message)`` pairs.

    Raises:
        None
    """
    fields = {f.name: f for f in spec.fields}
    found = [
        (f".{name}", f"unknown field; expected one of: {', '.join(fields)}")
        for name in item
        if name not in fields
    ]
    for name, field in fields.items():
        if name not in item:
            if field.required:
                found.append((f".{name}", "required field is missing"))
            continue
        message = _CHECKERS[field.spec.kind](field.spec, item[name])
        if message:
            found.append((f".{name}", message))
    return found


def _check_range(spec: ValueSpec, number: float) -> str | None:
    """Apply the optional inclusive bounds.

    Args:
        spec: Specification with ``minimum`` and/or ``maximum``.
        number: Numeric value (seconds for durations).

    Returns:
        str | None: Error message, or ``None`` when within range.

    Raises:
        None
    """
    if spec.minimum is not None and number < spec.minimum:
        return f"must be at least {spec.minimum:g}"
    if spec.maximum is not None and number > spec.maximum:
        return f"must be at most {spec.maximum:g}"
    return None


def _check_str(spec: ValueSpec, value: Any) -> str | None:
    """Check a string, its pattern, and its textual format.

    Args:
        spec: String specification.
        value: Candidate value.

    Returns:
        str | None: Error message, or ``None`` when valid.

    Raises:
        None
    """
    if not isinstance(value, str):
        return "expected a string"
    if spec.pattern and not re.search(spec.pattern, value):
        return f"must match {spec.pattern}"
    return _FORMATS[spec.text_format](value) if value and spec.text_format else None


def _check_number(spec: ValueSpec, value: Any) -> str | None:
    """Check an ``int`` or ``float``; booleans are rejected though Python treats them as ints.

    Args:
        spec: Number specification; ``float`` also accepts integers.
        value: Candidate value.

    Returns:
        str | None: Error message, or ``None`` when valid.

    Raises:
        None
    """
    allowed = int if spec.kind == "int" else int | float
    if isinstance(value, bool) or not isinstance(value, allowed):
        return "expected an integer" if spec.kind == "int" else "expected a number"
    return _check_range(spec, value)


def _check_duration(spec: ValueSpec, value: Any) -> str | None:
    """Check a duration string and its bounds in seconds.

    Args:
        spec: Duration specification.
        value: Candidate value.

    Returns:
        str | None: Error message, or ``None`` when valid.

    Raises:
        None
    """
    try:
        seconds = parse_duration(value)
    except ValueError as exc:
        return str(exc)
    return _check_range(spec, seconds)


def _check_url(value: str) -> str | None:
    """Require an http(s) URL without embedded credentials (EGRESS-001).

    Args:
        value: Non-empty string.

    Returns:
        str | None: Error message, or ``None`` when valid.

    Raises:
        None
    """
    try:
        parts = urlsplit(value)
        has_credentials = parts.username is not None or parts.password is not None
    except ValueError:
        return "expected an http:// or https:// URL"
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "expected an http:// or https:// URL"
    if has_credentials:
        return "must not contain credentials; use the credential provider"
    return None


def _check_timezone(value: str) -> str | None:
    """Require an IANA timezone name known to the host's tz database.

    Args:
        value: Non-empty string.

    Returns:
        str | None: Error message, or ``None`` when valid.

    Raises:
        None
    """
    try:
        zoneinfo.ZoneInfo(value)
    except ValueError, zoneinfo.ZoneInfoNotFoundError:
        return "expected an IANA timezone such as America/New_York"
    return None


def _check_pattern(value: str) -> str | None:
    """Require ``re:`` patterns to compile; other values are plain keywords.

    Args:
        value: Non-empty string.

    Returns:
        str | None: Error message, or ``None`` when valid.

    Raises:
        None
    """
    if not value.startswith("re:"):
        return None
    try:
        re.compile(value[3:])
    except re.error:
        return "invalid regular expression after re:"
    return None


_FORMATS: Mapping[str, Callable[[str], str | None]] = {
    "url": _check_url,
    "timezone": _check_timezone,
    "pattern": _check_pattern,
}

_CHECKERS: Mapping[str, Callable[[ValueSpec, Any], str | None]] = {
    "str": _check_str,
    "int": _check_number,
    "float": _check_number,
    "bool": lambda _spec, v: None if isinstance(v, bool) else "expected true or false",
    "duration": _check_duration,
    "enum": lambda s, v: None if v in s.choices else f"expected one of: {', '.join(s.choices)}",
    "list[str]": lambda _spec, v: (
        None
        if isinstance(v, list) and all(isinstance(x, str) for x in v)
        else "expected a list of strings"
    ),
}
