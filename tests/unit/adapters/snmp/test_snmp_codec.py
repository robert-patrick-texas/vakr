"""SNMP-015 and SEC-003: the PySNMP codec and the guards around PySNMP.

The codec maps each ASN.1 tag to exactly one port value kind and refuses
anything else. The guards keep PySNMP from leaking the community: a redacted
``repr``, packet debugging turned off, and a dispatcher that drops datagrams
from strangers and swallows undecodable ones without logging their bytes.

Dependencies:
    pysnmp, pyasn1: Values and messages to decode.
    vakr.adapters.snmp: Codec, guards, and transport helpers.

Example:
    $ uv run pytest tests/unit/adapters/snmp/test_snmp_codec.py
"""

import asyncio
import logging

import pytest
from pyasn1 import debug as asn1_debug
from pyasn1.codec.ber import encoder
from pyasn1.type import univ
from pysnmp import debug as snmp_debug
from pysnmp.proto import rfc1902, rfc1905
from pysnmp.proto.api import v2c

from tests.simulators.snmp.simkit import fake_community
from vakr.adapters.snmp.codec import decode_varbind, request_varbinds
from vakr.adapters.snmp.guards import (
    GuardedDispatcher,
    RedactedCommunity,
    close_dispatcher,
    quiet_library_debug,
)
from vakr.adapters.snmp.pdu import PduError
from vakr.adapters.snmp.transport import _reason
from vakr.ports.snmp import SnmpErrorKind, ValueKind

NAME = rfc1902.ObjectName("1.3.6.1.2.1.1.3.0")


@pytest.mark.parametrize(
    ("value", "kind", "plain"),
    [
        (rfc1902.Integer32(-5), ValueKind.INTEGER, -5),
        (rfc1902.OctetString(b"Gi0/0/0"), ValueKind.OCTET_STRING, b"Gi0/0/0"),
        (rfc1902.Bits(b"\x80"), ValueKind.OCTET_STRING, b"\x80"),
        (rfc1902.ObjectIdentifier("1.3.6.1.4.1.9"), ValueKind.OBJECT_ID, (1, 3, 6, 1, 4, 1, 9)),
        (rfc1902.IpAddress("192.0.2.1"), ValueKind.IP_ADDRESS, bytes([192, 0, 2, 1])),
        (rfc1902.Counter32(7), ValueKind.COUNTER32, 7),
        (rfc1902.Gauge32(8), ValueKind.GAUGE32, 8),
        (rfc1902.Unsigned32(9), ValueKind.GAUGE32, 9),
        (rfc1902.TimeTicks(10), ValueKind.TIMETICKS, 10),
        (rfc1902.Counter64(2**64 - 1), ValueKind.COUNTER64, 2**64 - 1),
        (rfc1902.Opaque(b"\x01"), ValueKind.OPAQUE, b"\x01"),
        (univ.Null(""), ValueKind.NULL, None),
        (rfc1905.noSuchObject, ValueKind.NO_SUCH_OBJECT, None),
        (rfc1905.noSuchInstance, ValueKind.NO_SUCH_INSTANCE, None),
        (rfc1905.endOfMibView, ValueKind.END_OF_MIB_VIEW, None),
    ],
)
def test_decode_maps_each_tag_to_one_kind(value: object, kind: ValueKind, plain: object) -> None:
    """SNMP-015: PySNMP values become plain port varbinds, by ASN.1 tag.

    Args:
        value: A pyasn1 value as PySNMP decodes it.
        kind: The expected kind.
        plain: The expected plain value.

    Returns:
        None

    Raises:
        None
    """
    vb = decode_varbind(NAME, value)
    assert (vb.oid, vb.kind, vb.value) == ((1, 3, 6, 1, 2, 1, 1, 3, 0), kind, plain)


@pytest.mark.parametrize("value", [univ.Boolean(True), rfc1902.ObjectIdentifier("5.1"), 42])
def test_decode_refuses_unknown_or_invalid_values(value: object) -> None:
    """An unknown type or an invalid value is MALFORMED, never a guessed value.

    Args:
        value: Something no agent should send.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(PduError) as caught:
        decode_varbind(NAME, value, "sim")
    assert caught.value.error.kind is SnmpErrorKind.MALFORMED


def test_request_varbinds_pair_each_oid_with_null() -> None:
    """RFC 3416: request varbinds carry NULL values.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ((name, value),) = request_varbinds(((1, 3, 6, 1),))
    assert tuple(name) == (1, 3, 6, 1)
    assert value.tagSet == univ.Null.tagSet


def test_redacted_community_and_debug_guard(caplog: pytest.LogCaptureFixture) -> None:
    """SEC-003: the community never appears in a repr, and packet debugging is turned off.

    Args:
        caplog: Captures the guard's warnings.

    Returns:
        None

    Raises:
        None
    """
    community = fake_community()
    auth = RedactedCommunity(community)
    assert community not in repr(auth)
    assert community not in str(auth)
    snmp_debug.set_logger(snmp_debug.Debug("io"))
    asn1_debug.setLogger(asn1_debug.Debug("all"))
    with caplog.at_level(logging.WARNING):
        quiet_library_debug()
    assert not snmp_debug.logger
    assert not asn1_debug._LOG
    assert caplog.text.count("turned it off") == 2


def _message(community: str) -> bytes:
    """Encode an SNMPv2c response carrying ``community``.

    Args:
        community: The community to embed.

    Returns:
        bytes: The BER-encoded message.

    Raises:
        None
    """
    message = v2c.Message()
    v2c.apiMessage.set_defaults(message)
    v2c.apiMessage.set_community(message, community)
    pdu = v2c.ResponsePDU()
    v2c.apiPDU.set_defaults(pdu)
    v2c.apiPDU.set_varbinds(pdu, [(NAME, rfc1902.TimeTicks(1))])
    v2c.apiMessage.set_pdu(message, pdu)
    return encoder.encode(message)


def test_guarded_dispatcher_drops_strangers_and_undecodable_answers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Replies are accepted only from the agent, and broken ones are logged without bytes.

    Args:
        caplog: Captures the dispatcher's logs.

    Returns:
        None

    Raises:
        None
    """
    community = fake_community()
    wire = _message(community)

    async def scenario() -> GuardedDispatcher:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            GuardedDispatcher: What the assertions check.

        Raises:
            None
        """
        dispatcher = GuardedDispatcher(("192.0.2.1", 161), "192.0.2.1:161")
        engine = dispatcher.transport_dispatcher
        dispatcher._recv_callback(engine, (1,), ("192.0.2.9", 161), wire)
        dispatcher._recv_callback(engine, (1,), ("192.0.2.1", 1161), wire)
        dispatcher._recv_callback(engine, (1,), ("not-an-ip",), wire)
        dispatcher._recv_callback(engine, (1,), ("192.0.2.1", 161), wire[:-3])
        dispatcher._recv_callback(engine, (1,), ("192.0.2.1", 161), wire)  # no pending request
        close_dispatcher(dispatcher)
        return dispatcher

    with caplog.at_level(logging.DEBUG):
        dispatcher = asyncio.run(scenario())
    assert (dispatcher.unexpected, dispatcher.malformed) == (3, 1)
    assert repr(dispatcher) == "GuardedDispatcher(192.0.2.1:161)"
    assert "undecodable" in caplog.text
    assert community not in caplog.text


def test_reason_names_the_os_error_behind_a_library_failure() -> None:
    """Transport failures report the OS reason, found through the exception chain.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    outer = RuntimeError("wrapped")
    outer.__cause__ = OSError(101, "Network is unreachable")
    assert _reason(outer) == "Network is unreachable"
    assert _reason(RuntimeError("no cause")) == "socket error"
