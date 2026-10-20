# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Value conversion for walk2rec: one net-snmp walk value to a ``.snmprec`` tag.

Each walk type label has one converter. ``convert`` drops the records an
agent uses to say "nothing here" and raises ``ConversionError`` for types
``.snmprec`` cannot hold (such as ``Opaque: Float``), so the caller can
report them with file and line.

Dependencies:
    re: Parsing value text.

Safety:
    Pure functions over strings.

Example:
    >>> convert("1.3.6.1.2.1.2.2.1.8.1", "INTEGER", "up(1)", ())
    ('2', '1')
    >>> convert("1.3.6.1.2.1.2.2.1.6.1", "STRING", "2:5e:1:0:0:1", (".1.3.6.1.2.1.2.2.1.6",))
    ('4x', '025e01000001')
"""

import re
from collections.abc import Callable, Sequence
from functools import partial

_DROP = ("No Such Object", "No Such Instance", "No more variables")
_NUMERIC = {
    "INTEGER": "2",
    "Counter32": "65",
    "Gauge32": "66",
    "Counter64": "70",
    "Unsigned32": "66",
}
_PRINTABLE = re.compile(r"[\x20-\x7b\x7d\x7e]*")  # printable ASCII except "|"
_HEX_OCTETS = re.compile(r"(?:[0-9A-Fa-f]{2}(?:\s+|$))+")


class ConversionError(ValueError):
    """A walk record that has no ``.snmprec`` form."""


def _octets_hex(text: str) -> str:
    """Normalise ``Hex-STRING`` digits to lower-case hex without spaces.

    Args:
        text: Such as ``00 1A 2B`` across one or more lines.

    Returns:
        str: Such as ``001a2b``.

    Raises:
        ConversionError: If the text is not hex octets.
    """
    digits = "".join(text.split()).lower()
    if len(digits) % 2 or re.fullmatch(r"[0-9a-f]*", digits) is None:
        raise ConversionError(f"not hex octets: {text[:40]!r}")
    return digits


def _hint_hex(text: str) -> str:
    """Turn a hint-formatted octet string such as ``2:5e:1:0:0:1`` into hex.

    Args:
        text: Colon-separated hex octets, one or two digits each.

    Returns:
        str: Such as ``025e01000001``.

    Raises:
        ConversionError: If a part is not a hex octet.
    """
    if not text:
        return ""
    parts = text.split(":")
    if not all(re.fullmatch(r"[0-9A-Fa-f]{1,2}", part) for part in parts):
        raise ConversionError(f"not a colon-hex octet string: {text[:40]!r}")
    return "".join(f"{int(part, 16):02x}" for part in parts)


def _string(oid: str, value: str, hex_hints: Sequence[str]) -> tuple[str, str]:
    """Convert a ``STRING`` value.

    Args:
        oid: The record's OID, without a leading dot.
        value: The value text.
        hex_hints: OID prefixes whose strings are DISPLAY-HINT octets.

    Returns:
        tuple[str, str]: The tag (``4`` or ``4x``) and value.

    Raises:
        ConversionError: If a hinted value is not colon hex.
    """
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1].replace('\\"', '"')
    if any(f".{oid}".startswith(f"{prefix}.") for prefix in hex_hints):
        return "4x", _hint_hex(value)
    if _PRINTABLE.fullmatch(value):
        return "4", value
    return "4x", value.encode("utf-8").hex()


def _number(value: str, kind: str) -> str:
    """Extract an integer from ``1`` or ``up(1)``.

    Args:
        value: The value text.
        kind: The type label, for the error message.

    Returns:
        str: The integer, as text.

    Raises:
        ConversionError: If there is no integer.
    """
    number = re.search(r"\((-?\d+)\)\s*$", value) or re.fullmatch(r"\s*(-?\d+)\s*", value)
    if number is None:
        raise ConversionError(f"{kind} value is not a number: {value[:40]!r}")
    return number[1]


def _numeric(tag: str, kind: str, _oid: str, value: str, _hints: Sequence[str]) -> tuple[str, str]:
    """Convert an INTEGER, counter, or gauge value.

    Args:
        tag: The ``.snmprec`` tag for ``kind``.
        kind: The type label.
        _oid: Unused.
        value: The value text.
        _hints: Unused.

    Returns:
        tuple[str, str]: The tag and the integer.

    Raises:
        ConversionError: If there is no integer.
    """
    return tag, _number(value, kind)


def _ticks(_oid: str, value: str, _hints: Sequence[str]) -> tuple[str, str]:
    """Convert TimeTicks, bare (``-Ot``) or as ``(123) 0:00:01.23``.

    Args:
        _oid: Unused.
        value: The value text.
        _hints: Unused.

    Returns:
        tuple[str, str]: Tag ``67`` and the ticks.

    Raises:
        ConversionError: If there are no ticks.
    """
    ticks = re.search(r"\((\d+)\)", value) or re.fullmatch(r"\s*(\d+)\s*", value)
    if ticks is None:
        raise ConversionError(f"untyped value is not TimeTicks: {value[:40]!r}")
    return "67", ticks[1]


def _bits(_oid: str, value: str, _hints: Sequence[str]) -> tuple[str, str]:
    """Convert ``BITS: 40 00 rx(1)``: the octets, then the bit names.

    Args:
        _oid: Unused.
        value: The value text.
        _hints: Unused.

    Returns:
        tuple[str, str]: Tag ``4x`` and the octets.

    Raises:
        ConversionError: If there are no octets.
    """
    octets = _HEX_OCTETS.match(value)
    return "4x", _octets_hex(octets[0] if octets else value)


type Converter = Callable[[str, str, Sequence[str]], tuple[str, str]]
_CONVERTERS: dict[str, Converter] = {
    **{kind: partial(_numeric, tag, kind) for kind, tag in _NUMERIC.items()},
    "": _ticks,
    "Timeticks": _ticks,
    "STRING": _string,
    "Hex-STRING": lambda _o, v, _h: ("4x", _octets_hex(v)),
    "BITS": _bits,
    "OID": lambda _o, v, _h: ("6", v.strip().lstrip(".")),
    "IpAddress": lambda _o, v, _h: ("64", v.strip()),
    "Network Address": lambda _o, v, _h: ("64", v.strip()),
}  # fmt: skip


def convert(oid: str, kind: str, value: str, hex_hints: Sequence[str]) -> tuple[str, str] | None:
    """Convert one walk record to a ``.snmprec`` tag and value.

    Args:
        oid: The record's OID, without a leading dot.
        kind: The walk's type label, or empty for a bare value.
        value: The value text.
        hex_hints: OID prefixes whose strings are DISPLAY-HINT octets.

    Returns:
        tuple[str, str] | None: ``(tag, value)``, or ``None`` for records to
        drop (no such object or instance, end of MIB view).

    Raises:
        ConversionError: If the record has no ``.snmprec`` form.
    """
    if value.startswith(_DROP) or kind == "NULL":
        return None
    if not kind and value == '""':
        return "4", ""  # net-snmp's rendering of an empty octet string
    converter = _CONVERTERS.get(kind)
    if converter is None:
        raise ConversionError(f"unsupported type {kind!r}")
    return converter(oid, value, hex_hints)
