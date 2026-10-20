"""Value specifications for catalog-described configuration options.

Every option in a catalog carries a value specification: its type, allowed
choices, numeric range, pattern, or textual format. ``checks`` validates
values against these specifications; this module defines them, parses
durations such as ``"15m"``, and masks credentials for display.

Dependencies:
    re, urllib.parse: Duration parsing and URL handling.

Safety:
    ``redact`` masks ``user:password@`` in URLs so effective-configuration
    output can be pasted into tickets.

Example:
    >>> parse_duration("1m30s")
    90.0
    >>> redact(ValueSpec("str", text_format="url"), "http://u:p@proxy:3128")
    'http://***@proxy:3128'
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

SCALAR_TYPES = ("str", "int", "float", "bool", "duration", "enum", "list[str]")
"""Types a single value (or an array-of-tables field) may have."""

VALUE_TYPES = (*SCALAR_TYPES, "list[table]")
"""Every type an option may declare."""

TEXT_FORMATS = ("", "url", "timezone", "pattern")
"""Extra checks for ``str`` values; ``""`` means none."""

_DURATION_PART = re.compile(r"(\d+)(ms|s|m|h|d)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field of the tables in a ``list[table]`` option.

    Attributes:
        name: Field name as written inside each table.
        spec: Type and constraints of the field value.
        required: Whether every table must set the field.
        default: Value filled in when a table omits the field; ``None`` means
            the field stays absent (TOML has no null, so this is unambiguous).
        description: Operator-facing explanation.
    """

    name: str
    spec: ValueSpec
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class ValueSpec:
    """Type and constraints of one option or field value.

    Attributes:
        kind: One of ``VALUE_TYPES``.
        choices: Allowed values for ``enum``.
        minimum: Inclusive lower bound for ``int``, ``float``, and
            ``duration`` (in seconds).
        maximum: Inclusive upper bound, same units as ``minimum``.
        pattern: Regular expression a ``str`` value must match.
        text_format: One of ``TEXT_FORMATS`` for ``str`` values.
        fields: Table fields for ``list[table]``.
    """

    kind: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    pattern: str = ""
    text_format: str = ""
    fields: tuple[FieldSpec, ...] = ()


def parse_duration(text: object) -> float:
    """Convert a duration such as ``"200ms"``, ``"15m"``, or ``"1h30m"`` to seconds.

    Args:
        text: Duration string made of ``<integer><unit>`` parts, where the
            unit is ``ms``, ``s``, ``m``, ``h``, or ``d``.

    Returns:
        float: Total seconds.

    Raises:
        ValueError: If ``text`` is not a well-formed duration string.
    """
    if not isinstance(text, str) or not text or _DURATION_PART.sub("", text):
        raise ValueError("expected a duration such as 200ms, 60s, 15m, 24h, or 1d")
    return sum(int(n) * _UNIT_SECONDS[unit] for n, unit in _DURATION_PART.findall(text))


def redact(spec: ValueSpec, value: Any) -> Any:
    """Return ``value`` safe for display, masking credentials embedded in URLs.

    Validation already rejects credentials in URLs (EGRESS-001); masking
    still matters because ``show-effective`` prints invalid configurations.

    Args:
        spec: Specification of the value; only ``url`` strings are altered.
        value: The configured value.

    Returns:
        Any: The value, or a copy with ``user:password@`` replaced by ``***@``.

    Raises:
        None
    """
    if spec.text_format != "url" or not isinstance(value, str) or "@" not in value:
        return value
    try:
        parts = urlsplit(value)
        masked = parts.username is not None or parts.password is not None
    except ValueError:
        return "<redacted>"
    if not masked:
        return value
    return parts._replace(netloc="***@" + parts.netloc.rpartition("@")[2]).geturl()
