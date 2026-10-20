"""One SNMP request PDU and its reply, between the request engine and a transport.

The request engine (batching, retries, tooBig handling) is the same for every
SNMP version and library; only "send this PDU and wait for its reply" is
specific. ``PduTransport`` is that seam. The PySNMP v2c transport implements
it today, an SNMPv3 transport can later, and unit tests drive the engine
with a scripted fake.

Dependencies:
    dataclasses, enum, typing: Values and the transport protocol.
    vakr.ports.snmp: OIDs, varbinds, and errors.

Safety:
    ``PduError`` messages are written by vakr and never include secrets.

Example:
    >>> Pdu(PduKind.GETBULK, ((1, 3, 6, 1, 2, 1, 2, 2, 1, 2),), max_repetitions=25).kind.value
    'getbulk'
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from vakr.ports.snmp import Oid, SnmpError, VarBind


class PduKind(StrEnum):
    """The request PDU types vakr sends; it never sends SET."""

    GET = "get"
    GETNEXT = "getnext"
    GETBULK = "getbulk"


@dataclass(frozen=True, slots=True)
class Pdu:
    """A request to send.

    Attributes:
        kind: The PDU type.
        oids: The OIDs in the request, in order.
        max_repetitions: GETBULK max-repetitions; 0 for GET and GETNEXT.
    """

    kind: PduKind
    oids: tuple[Oid, ...]
    max_repetitions: int = 0


@dataclass(frozen=True, slots=True)
class Reply:
    """A response PDU, decoded into port types.

    Attributes:
        status: RFC 3416 error-status; 0 is ``noError``.
        index: RFC 3416 error-index (1-based), or 0.
        varbinds: The response varbinds, in order.
    """

    status: int
    index: int
    varbinds: tuple[VarBind, ...]


class PduError(Exception):
    """A request failed in a way that is not a plain timeout.

    Collector-side kinds (transport, credential) mean nothing reached the
    agent; ``MALFORMED`` means the agent answered with something unusable.

    Attributes:
        error: The explicit error.
    """

    def __init__(self, error: SnmpError) -> None:
        """Wrap the error.

        Args:
            error: The explicit error.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(error.message)
        self.error = error


class PduTransport(Protocol):
    """Sends one PDU attempt and waits for its reply."""

    async def send(self, pdu: Pdu, timeout_s: float) -> Reply | None:
        """Send one attempt of ``pdu``.

        Args:
            pdu: The request.
            timeout_s: Seconds to wait for the reply.

        Returns:
            Reply | None: The reply, or ``None`` when none arrived in time.

        Raises:
            PduError: When the collector could not send the request or the
                reply could not be decoded.
        """
        ...
