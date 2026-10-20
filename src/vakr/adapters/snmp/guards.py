"""Close PySNMP's secret leaks: reprs, packet debugging, and stray datagrams.

PySNMP is careful about secrets in some places and not others. This module
closes the gaps found while building the adapter (ADR-034):

- ``CommunityData.__repr__`` prints the community; ``RedactedCommunity``
  does not.
- PySNMP's and pyasn1's debug loggers hexdump raw packets, which contain
  the community; ``quiet_library_debug`` turns them off before every session.
- An undecodable datagram would surface as an asyncio callback error whose
  report includes the raw bytes; ``GuardedDispatcher`` catches it, counts
  it, and logs a fixed message.
- PySNMP matches replies by request ID only; ``GuardedDispatcher`` also
  drops datagrams that do not come from the polled agent's address and port.

Dependencies:
    pysnmp, pyasn1: The SNMP library and its ASN.1 codec.

Safety:
    Nothing here logs a packet, a varbind value, or a community.

Example:
    >>> repr(RedactedCommunity("placeholder"))
    "CommunityData(communityName='***', mpModel=1)"
"""

import ipaddress
import logging
from typing import Any

from pyasn1 import debug as asn1_debug
from pyasn1.error import PyAsn1Error
from pysnmp import debug as snmp_debug
from pysnmp.error import PySnmpError
from pysnmp.hlapi.v1arch.asyncio import CommunityData, SnmpDispatcher

_LOG = logging.getLogger(__name__)
_DECODE_ERRORS = (PyAsn1Error, PySnmpError, ValueError, TypeError, KeyError, IndexError)


class RedactedCommunity(CommunityData):
    """PySNMP's v2c credential, with a ``repr`` that hides the community."""

    def __repr__(self) -> str:
        """Render without the community.

        Args:
            None

        Returns:
            str: A fixed, redacted form.

        Raises:
            None
        """
        return "CommunityData(communityName='***', mpModel=1)"

    __str__ = __repr__


def quiet_library_debug() -> None:
    """Turn off PySNMP and pyasn1 packet debugging, which would print the community.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    if snmp_debug.logger:
        snmp_debug.set_logger(0)
        _LOG.warning("PySNMP debug logging was on; vakr turned it off because it prints packets")
    if getattr(asn1_debug, "_LOG", None):
        asn1_debug.setLogger(None)
        _LOG.warning("pyasn1 debug logging was on; vakr turned it off because it prints packets")


class GuardedDispatcher(SnmpDispatcher):
    """A dispatcher that accepts replies only from the polled agent.

    Attributes:
        unexpected: Datagrams dropped because they came from another address.
        malformed: Datagrams dropped because they could not be decoded.
    """

    def __init__(self, peer: tuple[str, int], label: str) -> None:
        """Create the dispatcher; call only inside a running event loop.

        Args:
            peer: The agent's resolved IP address and UDP port.
            label: The agent as ``address:port``, for messages.

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self._peer = (ipaddress.ip_address(peer[0]), peer[1])
        self._label = label
        self.unexpected = 0
        self.malformed = 0

    def __repr__(self) -> str:
        """Describe the dispatcher by its agent only.

        Args:
            None

        Returns:
            str: For example ``GuardedDispatcher(192.0.2.1:161)``.

        Raises:
            None
        """
        return f"GuardedDispatcher({self._label})"

    def _recv_callback(  # PySNMP's parameter names, kept for a compatible override
        self,
        snmpEngine: Any,  # noqa: N803
        transportDomain: Any,  # noqa: N803
        transportAddress: Any,  # noqa: N803
        wholeMsg: bytes,  # noqa: N803
    ) -> Any:
        """Hand a datagram to PySNMP if it came from the agent and decodes.

        Args:
            snmpEngine: PySNMP's transport dispatcher.
            transportDomain: The transport domain.
            transportAddress: The sender's address tuple.
            wholeMsg: The raw datagram.

        Returns:
            Any: PySNMP's result, or ``None`` for a dropped datagram.

        Raises:
            None
        """
        try:
            sender = (ipaddress.ip_address(transportAddress[0]), int(transportAddress[1]))
        except ValueError, TypeError, IndexError:
            sender = None
        if sender != self._peer:
            self.unexpected += 1
            _LOG.debug("ignored an SNMP datagram that did not come from %s", self._label)
            return None
        try:
            return super()._recv_callback(snmpEngine, transportDomain, transportAddress, wholeMsg)
        except _DECODE_ERRORS:
            # The datagram may echo the community, so neither it nor the
            # exception text (which can quote it) is logged.
            self.malformed += 1
            _LOG.warning("discarded an undecodable SNMP answer from %s", self._label)
            return None


def close_dispatcher(dispatcher: SnmpDispatcher) -> None:
    """Close a dispatcher and its socket, tolerating PySNMP's teardown quirks.

    Args:
        dispatcher: The session's dispatcher.

    Returns:
        None

    Raises:
        None
    """
    try:
        dispatcher.close()
    except PySnmpError, AttributeError, RuntimeError:
        _LOG.debug("PySNMP raised while closing a dispatcher; the socket is closed anyway")
