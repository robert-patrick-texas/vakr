"""Wire value converters for observation payload fields (schema version 1).

``vakr.domain.codec_fields`` lists each type's fields and their wire kinds;
this module turns one value of each kind into JSON and back. Decoding checks
JSON types exactly: ``true`` is never accepted as ``1``, a string is never
accepted as a number, and ``null`` is accepted only for optional fields.

Dependencies:
    vakr.domain.ids, timestamps: UUID and timestamp parsing.
    vakr.domain.errors: ``ValidationError``.

Safety:
    Pure functions with no I/O.

Example:
    >>> f = Field("started_at", "time", optional=True)
    >>> f.key, encode_value(f, None), decode_value(f, 0).year
    ('started_at_us', None, 1970)
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from vakr.domain.errors import ValidationError
from vakr.domain.ids import parse_uuid
from vakr.domain.json_values import JsonValue
from vakr.domain.timestamps import from_epoch_us, to_epoch_us


@dataclass(frozen=True, slots=True)
class Field:
    """One payload field: attribute name, wire kind, and nullability.

    Attributes:
        attr: Attribute name on the observation class.
        kind: Wire kind (see the module table).
        optional: Whether ``null`` is allowed.
        enum: Enum class for ``enum`` fields.
    """

    attr: str
    kind: str
    optional: bool = False
    enum: type[Enum] | None = None

    @property
    def key(self) -> str:
        """Return the wire key, which adds ``_us`` to time fields.

        Args:
            None

        Returns:
            str: The JSON object key.

        Raises:
            None
        """
        return f"{self.attr}_us" if self.kind == "time" else self.attr


def encode_value(field: Field, value: Any) -> JsonValue:
    """Encode one domain value in its wire form.

    Args:
        field: The field being encoded.
        value: Domain value, or ``None``.

    Returns:
        JsonValue: The JSON-compatible value.

    Raises:
        None
    """
    if value is None:
        return None
    encoder = _ENCODERS.get(field.kind)
    return value if encoder is None else encoder(value)


_ENCODERS: dict[str, Callable[[Any], JsonValue]] = {
    "uuid": str,
    "time": to_epoch_us,
    "enum": lambda member: member.value,
    "rtts": list,
    "float": float,
}


def decode_value(field: Field, raw: object) -> Any:
    """Decode one wire value into its domain form, checking its JSON type.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        Any: The domain value, or ``None`` for an allowed ``null``.

    Raises:
        ValidationError: If ``raw`` is ``null`` for a required field or has
            the wrong JSON type for the field's kind.
    """
    if raw is None:
        if field.optional:
            return None
        raise ValidationError(f"{field.key} must not be null", field=field.key)
    return _DECODERS[field.kind](field, raw)


def _int(field: Field, raw: object) -> int:
    """Decode an integer, refusing booleans and floats.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        int: The value.

    Raises:
        ValidationError: If ``raw`` is not an integer.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError(f"{field.key} must be an integer", field=field.key)
    return raw


def _float(field: Field, raw: object) -> float:
    """Decode a number as a float; another producer may write ``2`` for ``2.0``.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        float: The value.

    Raises:
        ValidationError: If ``raw`` is not a number that fits a float.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValidationError(f"{field.key} must be a number", field=field.key)
    try:
        return float(raw)
    except OverflowError:
        raise ValidationError(f"{field.key} is too large", field=field.key) from None


def _bool(field: Field, raw: object) -> bool:
    """Decode a boolean.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        bool: The value.

    Raises:
        ValidationError: If ``raw`` is not ``true`` or ``false``.
    """
    if not isinstance(raw, bool):
        raise ValidationError(f"{field.key} must be true or false", field=field.key)
    return raw


def _str(field: Field, raw: object) -> str:
    """Decode a string.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        str: The value.

    Raises:
        ValidationError: If ``raw`` is not a string.
    """
    if not isinstance(raw, str):
        raise ValidationError(f"{field.key} must be a string", field=field.key)
    return raw


def _enum(field: Field, raw: object) -> Enum:
    """Decode an enum from its stored value, which must have the value's JSON type.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        Enum: The member.

    Raises:
        ValidationError: If ``raw`` is not a value of the field's enum.
    """
    kind = field.enum
    if kind is None or type(raw) is not type(next(iter(kind)).value):
        raise ValidationError(f"{field.key} has the wrong JSON type", field=field.key)
    try:
        return kind(raw)
    except ValueError:
        message = f"{field.key} {raw!r} is not a {kind.__name__}"
        raise ValidationError(message, field=field.key) from None


def _rtts(field: Field, raw: object) -> tuple[int | None, ...]:
    """Decode per-probe RTTs, where ``null`` means no reply.

    Args:
        field: The field being decoded.
        raw: Parsed JSON value.

    Returns:
        tuple[int | None, ...]: The RTTs in probe order.

    Raises:
        ValidationError: If ``raw`` is not an array of integers and nulls.
    """
    if not isinstance(raw, list):
        raise ValidationError(f"{field.key} must be an array", field=field.key)
    return tuple(None if r is None else _int(field, r) for r in raw)


_DECODERS: dict[str, Callable[[Field, Any], Any]] = {
    "uuid": lambda f, raw: parse_uuid(raw, f.key),
    "time": lambda _f, raw: from_epoch_us(raw),
    "int": _int,
    "float": _float,
    "str": _str,
    "enum": _enum,
    "rtts": _rtts,
    "bool": _bool,
}
