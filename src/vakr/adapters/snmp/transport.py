"""The PySNMP v2c transport: one guarded UDP socket per session (SNMP-015).

Each session gets its own PySNMP ``SnmpDispatcher`` (the asyncio ``v1arch``
API) and so its own UDP socket, which the session closes even when a request
times out or the poll is cancelled. PySNMP's own retries are off: vakr
retries whole attempts itself so it can count them (SNMP-018).

The dispatcher, community wrapper, and debug guard that keep PySNMP from
leaking the community live in ``vakr.adapters.snmp.guards``.

Dependencies:
    pysnmp, pyasn1: The SNMP library and its ASN.1 codec.
    vakr.adapters.snmp: Codec, guards, messages, and the transport seam.

Safety:
    Nothing here logs a packet, a varbind value, or a community.

Example:
    >>> _reason(OSError(101, "Network is unreachable"))
    'Network is unreachable'
"""

import asyncio
from typing import Any

from pyasn1.error import PyAsn1Error
from pysnmp.error import PySnmpError
from pysnmp.hlapi.v1arch.asyncio import (
    Udp6TransportTarget,
    UdpTransportTarget,
    bulk_cmd,
    get_cmd,
    next_cmd,
)
from pysnmp.proto import errind

from vakr.adapters.snmp import messages
from vakr.adapters.snmp.codec import decode_varbind, request_varbinds
from vakr.adapters.snmp.guards import GuardedDispatcher, RedactedCommunity
from vakr.adapters.snmp.pdu import Pdu, PduError, PduKind, Reply
from vakr.ports.snmp import SnmpErrorKind

GRACE_S = 1.0  # E.23: extra wait past the timeout before vakr stops waiting for PySNMP

type Endpoint = UdpTransportTarget | Udp6TransportTarget


class PysnmpTransport:
    """Sends single PDU attempts through PySNMP (``PduTransport``)."""

    def __init__(
        self, dispatcher: GuardedDispatcher, auth: RedactedCommunity, endpoint: Endpoint, label: str
    ) -> None:
        """Bind the dispatcher, credential, and agent endpoint together.

        Args:
            dispatcher: The session's dispatcher.
            auth: The community, held only until ``forget``.
            endpoint: PySNMP's endpoint for the agent.
            label: The agent as ``address:port``.

        Returns:
            None

        Raises:
            None
        """
        self._dispatcher = dispatcher
        self._auth: RedactedCommunity | None = auth
        self._endpoint = endpoint
        self._label = label

    def __repr__(self) -> str:
        """Describe the transport by its agent only.

        Args:
            None

        Returns:
            str: For example ``PysnmpTransport(192.0.2.1:161)``.

        Raises:
            None
        """
        return f"PysnmpTransport({self._label})"

    def forget(self) -> None:
        """Drop the credential; later sends fail as a collector problem.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._auth = None

    async def send(self, pdu: Pdu, timeout_s: float) -> Reply | None:
        """Send one attempt and wait for its reply.

        Args:
            pdu: The request.
            timeout_s: Seconds to wait.

        Returns:
            Reply | None: The decoded reply, or ``None`` on timeout.

        Raises:
            PduError: ``TRANSPORT`` when the request could not be sent or
                the socket never opened; ``MALFORMED`` for an unusable reply.
        """
        self._endpoint.timeout = timeout_s
        self._endpoint.retries = 0  # vakr retries whole attempts itself (SNMP-018)
        failed: str | None = None
        try:
            async with asyncio.timeout(timeout_s + GRACE_S):
                indication, status, index, varbinds = await self._call(pdu)
        except TimeoutError:
            self._require_socket()
            return None
        except (PySnmpError, PyAsn1Error, OSError) as exc:
            failed = f"the SNMP request could not be sent ({_reason(exc)})"
        if failed is not None:
            # Raised outside the handler so the library exception, whose frames
            # hold the encoded packet and community, is not kept as context.
            raise PduError(messages.collector(SnmpErrorKind.TRANSPORT, self._label, failed))
        if isinstance(indication, errind.RequestTimedOut):
            self._require_socket()
            return None
        if indication is not None:
            what = "the SNMP library stopped the request"
            raise PduError(messages.collector(SnmpErrorKind.TRANSPORT, self._label, what))
        decoded = tuple(decode_varbind(name, value, self._label) for name, value in varbinds)
        return Reply(int(status), int(index), decoded)

    async def _call(self, pdu: Pdu) -> tuple[Any, Any, Any, Any]:
        """Call the PySNMP command for the PDU kind.

        Args:
            pdu: The request.

        Returns:
            tuple[Any, Any, Any, Any]: PySNMP's error indication, status,
            index, and varbinds.

        Raises:
            PduError: If the credential was already forgotten.
        """
        auth = self._auth
        if auth is None:
            what = "the session was already closed"
            raise PduError(messages.collector(SnmpErrorKind.TRANSPORT, self._label, what))
        binds = request_varbinds(pdu.oids)
        dispatcher, endpoint = self._dispatcher, self._endpoint
        if pdu.kind is PduKind.GETBULK:
            return await bulk_cmd(dispatcher, auth, endpoint, 0, pdu.max_repetitions, *binds)
        if pdu.kind is PduKind.GETNEXT:
            return await next_cmd(dispatcher, auth, endpoint, *binds)
        return await get_cmd(dispatcher, auth, endpoint, *binds)

    def _require_socket(self) -> None:
        """Turn a timeout into a collector problem if the socket never opened.

        PySNMP opens its socket in the background; a failure there would
        otherwise look like an agent that never answers (invariant 2).

        Args:
            None

        Returns:
            None

        Raises:
            PduError: ``TRANSPORT`` when the UDP socket is not open.
        """
        try:
            socket = self._dispatcher.transport_dispatcher.get_transport(
                self._endpoint.TRANSPORT_DOMAIN
            )
        except PySnmpError:
            socket = None
        if getattr(socket, "transport", None) is None:
            what = "the collector could not open its UDP socket"
            raise PduError(messages.collector(SnmpErrorKind.TRANSPORT, self._label, what))


def _reason(exc: BaseException) -> str:
    """Name the operating-system error behind a library failure, if there is one.

    Args:
        exc: The exception PySNMP raised.

    Returns:
        str: For example ``Network is unreachable``, or ``socket error``.

    Raises:
        None
    """
    seen: BaseException | None = exc
    for _ in range(4):  # the chain is short; bound the walk anyway
        if seen is None:
            break
        if isinstance(seen, OSError) and seen.strerror:
            return seen.strerror
        seen = seen.__cause__ or seen.__context__
    return "socket error"
