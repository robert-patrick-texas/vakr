"""Scripted SNMP transport fakes shared by the adapter unit tests.

``FakeTransport`` answers each PDU from a script and records what it saw,
so the request engine can be tested exactly and without sockets.

Dependencies:
    asyncio: The transport is async.
    vakr.adapters.snmp.pdu, vakr.ports.snmp: PDUs, replies, and varbinds.

Example:
    >>> len(oids(3))
    3
"""

import asyncio
from collections.abc import Callable

from vakr.adapters.snmp.pdu import Pdu, Reply
from vakr.ports.snmp import Oid, ValueKind, VarBind

COLUMN = (1, 3, 6, 1, 2, 1, 2, 2, 1, 2)
type Script = Callable[[Pdu], Reply | None]


def oids(count: int) -> list[Oid]:
    """Return ``count`` distinct instance OIDs under ``COLUMN``.

    Args:
        count: How many.

    Returns:
        list[Oid]: ``COLUMN.1`` to ``COLUMN.count``.

    Raises:
        None
    """
    return [(*COLUMN, i) for i in range(1, count + 1)]


def answer(pdu: Pdu) -> Reply:
    """Answer a GET with one Counter32 per OID.

    Args:
        pdu: The request.

    Returns:
        Reply: ``noError`` with the OIDs echoed.

    Raises:
        None
    """
    return Reply(0, 0, tuple(VarBind(oid, ValueKind.COUNTER32, oid[-1]) for oid in pdu.oids))


class FakeTransport:
    """A ``PduTransport`` that follows a script and records what it saw.

    Attributes:
        sent: Every PDU attempt.
        in_flight: Attempts currently waiting.
        max_in_flight: The most attempts ever waiting at once.
    """

    def __init__(self, script: Script, delay: float = 0.0) -> None:
        """Bind the script.

        Args:
            script: Maps a PDU to its reply, ``None`` for a timeout, or raises.
            delay: Seconds each attempt waits, to expose concurrency.

        Returns:
            None

        Raises:
            None
        """
        self.script, self.delay = script, delay
        self.sent: list[Pdu] = []
        self.in_flight = self.max_in_flight = 0

    async def send(self, pdu: Pdu, timeout_s: float) -> Reply | None:
        """Answer one attempt from the script.

        Args:
            pdu: The request.
            timeout_s: Ignored; the script decides timeouts.

        Returns:
            Reply | None: The scripted reply.

        Raises:
            PduError: When the script raises it.
        """
        del timeout_s  # the script decides what times out
        self.sent.append(pdu)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return self.script(pdu)
        finally:
            self.in_flight -= 1
