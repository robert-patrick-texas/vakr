r"""Shared seams of the ICMP adapter: the socket interface, open errors, tallies.

``IcmpSocket`` is the slice of ``socket.socket`` the adapter calls. Unit
tests pass a fake that implements it, so reply ordering, duplicates, late
replies, and socket errors are exercised deterministically without a
network (ICMP-T01..T04, T06). ``Tally`` holds the running totals that
become ``IcmpCounters`` for self-observability (OBS-001).

Dependencies:
    dataclasses, ipaddress, typing: Value types and the protocol.
    vakr.ports.icmp: ``CollectorFault``.

Safety:
    ``SocketOpenError`` carries an errno description, never a secret.

Example:
    >>> packed("127.0.0.1"), len(packed("fe80::1%lo"))
    (b'\x7f\x00\x00\x01', 16)
"""

import ipaddress
from dataclasses import dataclass
from typing import Any, Protocol

from vakr.ports.icmp import CollectorFault


class IcmpSocket(Protocol):
    """The part of ``socket.socket`` the ICMP adapter uses (a seam for fakes)."""

    def fileno(self) -> int:
        """Return the file descriptor.

        Args:
            None

        Returns:
            int: The descriptor.

        Raises:
            None
        """
        ...

    def sendto(self, data: bytes, address: Any, /) -> int:
        """Send one datagram.

        Args:
            data: Packet bytes.
            address: Destination socket address tuple.

        Returns:
            int: Bytes sent.

        Raises:
            OSError: On a send failure.
        """
        ...

    def recvmsg(self, bufsize: int, ancbufsize: int = 0, flags: int = 0, /) -> tuple[Any, ...]:
        """Receive one datagram with control messages.

        Args:
            bufsize: Largest datagram to read.
            ancbufsize: Control-message buffer size.
            flags: ``MSG_*`` flags, such as ``MSG_ERRQUEUE``.

        Returns:
            tuple[Any, ...]: ``(data, ancdata, msg_flags, address)``.

        Raises:
            OSError: ``BlockingIOError`` when nothing is queued, or a pending error.
        """
        ...

    def getsockopt(self, level: int, option: int, buflen: int, /) -> bytes:
        """Read a socket option.

        Args:
            level: Option level.
            option: Option name.
            buflen: Buffer size.

        Returns:
            bytes: Raw option value.

        Raises:
            OSError: If the option is not supported.
        """
        ...

    def close(self) -> None:
        """Close the socket.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ...


class SocketOpenError(Exception):
    """A socket could not be opened or configured.

    Attributes:
        fault: Collector-side classification.
        reason: Operator-readable cause (``strerror``), never a secret.
    """

    def __init__(self, fault: CollectorFault, reason: str) -> None:
        """Record the classification.

        Args:
            fault: Collector-side classification.
            reason: Operator-readable cause.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"{fault.value}: {reason}")
        self.fault = fault
        self.reason = reason


@dataclass(slots=True)
class Tally:
    """Mutable running totals behind ``IcmpCounters`` (same field names).

    Attributes:
        sent: Echo requests handed to the kernel.
        replies: Matched replies.
        timeouts: Probes that timed out.
        unreachable: Probes answered by an ICMP error.
        late: Replies or errors for probes no longer waiting.
        duplicate: Extra replies for answered probes.
        foreign: Replies matching no probe.
        not_sent: Probes the collector could not send.
        receive_errors: Socket read errors.
        receive_overflow: Kernel receive-buffer drops.
        sockets_open: Open sockets.
        in_flight: Probes waiting now.
        in_flight_peak: Highest ``in_flight``.
    """

    sent: int = 0
    replies: int = 0
    timeouts: int = 0
    unreachable: int = 0
    late: int = 0
    duplicate: int = 0
    foreign: int = 0
    not_sent: int = 0
    receive_errors: int = 0
    receive_overflow: int = 0
    sockets_open: int = 0
    in_flight: int = 0
    in_flight_peak: int = 0


def packed(address: str) -> bytes:
    """Return the packed form of an IP literal, ignoring any IPv6 zone.

    Args:
        address: IPv4 or IPv6 literal.

    Returns:
        bytes: 4 or 16 bytes.

    Raises:
        ValueError: If ``address`` is not an IP literal.
    """
    return ipaddress.ip_address(address.partition("%")[0]).packed
