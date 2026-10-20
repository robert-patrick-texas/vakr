"""Convert between PySNMP/pyasn1 values and the port's plain varbinds.

PySNMP returns pyasn1 objects whose Python class does not always say what
the agent sent: ``Gauge32`` and ``Unsigned32`` share one wire type, and
``Bits`` is an ``OCTET STRING`` on the wire. Matching on the ASN.1 tag set
is exact, so each value maps to one ``ValueKind`` by its tag. Nothing from
PySNMP leaves this module (ARCH-001).

Dependencies:
    pyasn1, pysnmp.proto: The ASN.1 types PySNMP decodes into.
    vakr.adapters.snmp.pdu, vakr.ports.snmp: Port types and failures.

Safety:
    A value of an unknown type or out of range becomes an explicit
    ``MALFORMED`` failure, never a guessed value.

Example:
    >>> from pysnmp.proto import rfc1902
    >>> decode_varbind(rfc1902.ObjectName("1.3.6.1.2.1.1.3.0"), rfc1902.TimeTicks(42)).kind.name
    'TIMETICKS'
"""

from typing import Any

from pyasn1.type import univ
from pysnmp.proto import rfc1902, rfc1905

from vakr.adapters.snmp import messages
from vakr.adapters.snmp.pdu import PduError
from vakr.domain.errors import ValidationError
from vakr.ports.snmp import Oid, ValueKind, VarBind

_KINDS = {
    rfc1902.Integer32.tagSet: ValueKind.INTEGER,
    rfc1902.OctetString.tagSet: ValueKind.OCTET_STRING,
    rfc1902.ObjectIdentifier.tagSet: ValueKind.OBJECT_ID,
    rfc1902.IpAddress.tagSet: ValueKind.IP_ADDRESS,
    rfc1902.Counter32.tagSet: ValueKind.COUNTER32,
    rfc1902.Gauge32.tagSet: ValueKind.GAUGE32,
    rfc1902.TimeTicks.tagSet: ValueKind.TIMETICKS,
    rfc1902.Opaque.tagSet: ValueKind.OPAQUE,
    rfc1902.Counter64.tagSet: ValueKind.COUNTER64,
    univ.Null.tagSet: ValueKind.NULL,
    rfc1905.NoSuchObject.tagSet: ValueKind.NO_SUCH_OBJECT,
    rfc1905.NoSuchInstance.tagSet: ValueKind.NO_SUCH_INSTANCE,
    rfc1905.EndOfMibView.tagSet: ValueKind.END_OF_MIB_VIEW,
}
_NUMERIC = frozenset(
    {
        ValueKind.INTEGER,
        ValueKind.COUNTER32,
        ValueKind.GAUGE32,
        ValueKind.TIMETICKS,
        ValueKind.COUNTER64,
    }
)
_OCTETS = frozenset({ValueKind.OCTET_STRING, ValueKind.IP_ADDRESS, ValueKind.OPAQUE})


def request_varbinds(oids: tuple[Oid, ...]) -> list[Any]:
    """Build request varbinds: each OID paired with NULL, as RFC 3416 requires.

    Plain ``(ObjectName, Null)`` pairs keep PySNMP from loading MIBs to
    resolve names; PySNMP's annotations only mention ``ObjectType``.

    Args:
        oids: OIDs to request.

    Returns:
        list[Any]: ``(ObjectName, Null)`` pairs, ready for a PySNMP call.

    Raises:
        None
    """
    return [(rfc1902.ObjectName(oid), univ.Null("")) for oid in oids]


def decode_varbind(name: Any, value: Any, label: str = "the agent") -> VarBind:
    """Convert one response varbind into a port ``VarBind``.

    Args:
        name: The PySNMP ``ObjectName``.
        value: The pyasn1 value.
        label: The agent, for the error message.

    Returns:
        VarBind: The plain varbind.

    Raises:
        PduError: ``MALFORMED`` for an unknown type or an out-of-range value.
    """
    kind = _KINDS.get(getattr(value, "tagSet", None))
    if kind is None:
        raise PduError(messages.malformed(label, "a value of an unknown SNMP type"))
    plain: int | bytes | Oid | None = None
    if kind in _NUMERIC:
        plain = int(value)
    elif kind in _OCTETS:
        plain = bytes(value.asOctets())
    elif kind is ValueKind.OBJECT_ID:
        plain = tuple(value.asTuple())
    try:
        return VarBind(tuple(name.asTuple()), kind, plain)
    except ValidationError:
        raise PduError(messages.malformed(label, f"an invalid {kind.value} value")) from None
