"""SNMP values as plain Python: object identifiers, value kinds, and varbinds.

An SNMP agent answers with *variable bindings*: an object identifier (OID)
and a typed value, such as ``ifHCInOctets.3 = Counter64: 9123``. The value's
type matters as much as the number. A ``Counter64`` is a cumulative counter
that only ever grows until it wraps, a ``Gauge32`` can go up and down, and
``noSuchInstance`` means the agent does not have that object at all. That
is missing data, never zero (blueprint 4.3).

These types carry SNMP data across the port boundary, so collectors and
discovery never see a PySNMP or pyasn1 object. OIDs are tuples of integers,
which sort in the same order an agent walks them.

Dependencies:
    dataclasses, enum: Immutable values and kinds.
    vakr.domain.errors: ``ValidationError``.

Safety:
    OIDs are validated against RFC 2578 limits before use (SNMP-019). A
    varbind never holds a credential.

Example:
    >>> vb = VarBind(parse_oid("1.3.6.1.2.1.1.3.0"), ValueKind.TIMETICKS, 4200)
    >>> format_oid(vb.oid), vb.integer(), vb.present
    ('1.3.6.1.2.1.1.3.0', 4200, True)
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.errors import ValidationError

type Oid = tuple[int, ...]
"""An object identifier, such as ``(1, 3, 6, 1, 2, 1, 1, 3, 0)``."""

MAX_OID_ARCS = 128  # RFC 2578 section 3.5: at most 128 sub-identifiers
MAX_ARC = 2**32 - 1  # each sub-identifier is an unsigned 32-bit number
_DOTTED = re.compile(r"\.?(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))+")


class ValueKind(StrEnum):
    """The SMIv2 type of a varbind value, or the exception an agent returned."""

    INTEGER = "integer"
    OCTET_STRING = "octet_string"
    OBJECT_ID = "object_id"
    IP_ADDRESS = "ip_address"
    COUNTER32 = "counter32"
    GAUGE32 = "gauge32"
    TIMETICKS = "timeticks"
    OPAQUE = "opaque"
    COUNTER64 = "counter64"
    NULL = "null"
    NO_SUCH_OBJECT = "no_such_object"
    NO_SUCH_INSTANCE = "no_such_instance"
    END_OF_MIB_VIEW = "end_of_mib_view"


EXCEPTION_KINDS = frozenset(
    {ValueKind.NO_SUCH_OBJECT, ValueKind.NO_SUCH_INSTANCE, ValueKind.END_OF_MIB_VIEW}
)
"""Kinds that mean "no value here" (RFC 3416 section 4.2.1)."""

_INTEGER_RANGES = {
    ValueKind.INTEGER: (-(2**31), 2**31 - 1),
    ValueKind.COUNTER32: (0, 2**32 - 1),
    ValueKind.GAUGE32: (0, 2**32 - 1),
    ValueKind.TIMETICKS: (0, 2**32 - 1),
    ValueKind.COUNTER64: (0, 2**64 - 1),
}
_BYTES_KINDS = frozenset({ValueKind.OCTET_STRING, ValueKind.IP_ADDRESS, ValueKind.OPAQUE})


def require_oid(value: object, field: str = "oid") -> Oid:
    """Check that ``value`` is a valid OID tuple.

    Args:
        value: Candidate OID.
        field: Field name for the error message.

    Returns:
        Oid: The value, unchanged.

    Raises:
        ValidationError: If it is not a tuple of 2 to 128 integers in
            0..2^32-1 whose first arc is 0, 1, or 2.
    """
    if (
        not isinstance(value, tuple)
        or not 2 <= len(value) <= MAX_OID_ARCS
        or not all(type(arc) is int and 0 <= arc <= MAX_ARC for arc in value)
        or value[0] > 2
    ):
        raise ValidationError(f"{field} must be a valid object identifier", field=field)
    return value


def parse_oid(text: object) -> Oid:
    """Parse dotted-decimal text such as ``1.3.6.1.2.1.1.3.0``; a leading dot is allowed.

    Args:
        text: Candidate OID text, as an operator or a walk file writes it.

    Returns:
        Oid: The OID as a tuple of integers.

    Raises:
        ValidationError: If the text is not a valid dotted-decimal OID.
    """
    if not isinstance(text, str) or not _DOTTED.fullmatch(text):
        raise ValidationError("expected a numeric OID such as 1.3.6.1.2.1.1.3.0", field="oid")
    return require_oid(tuple(int(arc) for arc in text.lstrip(".").split(".")))


def format_oid(oid: Oid) -> str:
    """Render an OID as dotted decimal without a leading dot.

    Args:
        oid: The OID.

    Returns:
        str: For example ``1.3.6.1.2.1.1.3.0``.

    Raises:
        None
    """
    return ".".join(str(arc) for arc in oid)


def in_subtree(oid: Oid, root: Oid) -> bool:
    """Report whether ``oid`` lies strictly below ``root``.

    Args:
        oid: The OID an agent returned.
        root: The subtree being walked.

    Returns:
        bool: True when ``oid`` starts with ``root`` and is longer.

    Raises:
        None
    """
    return len(oid) > len(root) and oid[: len(root)] == root


@dataclass(frozen=True, slots=True)
class VarBind:
    """One object identifier and its typed value, as the agent returned it.

    Attributes:
        oid: The object identifier.
        kind: The SMIv2 type, or the exception the agent returned.
        value: ``int`` for numeric kinds, ``bytes`` for strings, addresses,
            and opaque values, an ``Oid`` for object identifiers, and
            ``None`` for ``NULL`` and the exception kinds.
    """

    oid: Oid
    kind: ValueKind
    value: int | bytes | Oid | None = None

    def __post_init__(self) -> None:
        """Check that the value fits its kind.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the OID is invalid or the value does not fit
                the kind's type and range.
        """
        require_oid(self.oid)
        if not isinstance(self.kind, ValueKind):
            raise ValidationError("kind must be a ValueKind", field="kind")
        if not self._value_fits():
            raise ValidationError(f"value does not fit an SNMP {self.kind.value}", field="value")

    def _value_fits(self) -> bool:
        """Check the value's type and range against the kind.

        Args:
            None

        Returns:
            bool: Whether the value is acceptable for the kind.

        Raises:
            None
        """
        value = self.value
        if self.kind in _INTEGER_RANGES:
            low, high = _INTEGER_RANGES[self.kind]
            return type(value) is int and low <= value <= high
        if self.kind is ValueKind.IP_ADDRESS:
            return isinstance(value, bytes) and len(value) == 4
        if self.kind in _BYTES_KINDS:
            return isinstance(value, bytes)
        if self.kind is ValueKind.OBJECT_ID:
            try:
                require_oid(value)
            except ValidationError:
                return False
            return True
        return value is None

    @property
    def present(self) -> bool:
        """Whether the agent returned a value, rather than NULL or an exception.

        Args:
            None

        Returns:
            bool: False for ``noSuchObject``, ``noSuchInstance``,
            ``endOfMibView``, and ``NULL``.

        Raises:
            None
        """
        return self.kind not in EXCEPTION_KINDS and self.kind is not ValueKind.NULL

    def integer(self) -> int | None:
        """Return a numeric value, or ``None`` for any other kind.

        Args:
            None

        Returns:
            int | None: The number for integer, counter, gauge, and tick kinds.

        Raises:
            None
        """
        return self.value if type(self.value) is int else None

    def octets(self) -> bytes | None:
        """Return a string, address, or opaque value as bytes.

        Args:
            None

        Returns:
            bytes | None: The raw octets, or ``None`` for other kinds.

        Raises:
            None
        """
        return self.value if isinstance(self.value, bytes) else None

    def text(self) -> str | None:
        """Return an octet string decoded as a DisplayString.

        Args:
            None

        Returns:
            str | None: UTF-8 text with undecodable bytes replaced and
            trailing NUL padding removed, or ``None`` for other kinds.

        Raises:
            None
        """
        raw = self.octets()
        return None if raw is None else raw.decode("utf-8", "replace").rstrip("\x00")

    def object_id(self) -> Oid | None:
        """Return an OID value.

        Args:
            None

        Returns:
            Oid | None: The OID for ``OBJECT_ID`` values, else ``None``.

        Raises:
            None
        """
        value = self.value
        return value if self.kind is ValueKind.OBJECT_ID and isinstance(value, tuple) else None
