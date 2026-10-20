"""A bounded set of open ICMP channels, one per (address family, source).

Most collectors need two sockets, IPv4 and IPv6, shared by every target.
A pinned source address needs its own socket because the kernel binds the
source per socket, so the number of sockets grows with distinct sources,
never with targets. The pool caps it, closes an idle channel to make room,
and refuses with ``SOCKET_LIMIT`` when every channel is busy.

When ``net.ipv4.ping_group_range`` refuses a datagram socket and raw
fallback is allowed, the pool tries a raw socket, which works only if the
collector unit was granted ``CAP_NET_RAW`` (ICMP-012). If that fails too,
the original refusal is reported, because the fix it points to (the
sysctl) is the one operators should apply.

Dependencies:
    vakr.adapters.icmp: Channels and socket helpers.
    vakr.ports: ``Clock`` and ``CollectorFault``.

Safety:
    Bounded by ``max_sockets``; ``close_all`` closes everything.

Example:
    >>> from vakr.ports.clock import SystemClock
    >>> ChannelPool(max_sockets=2, capacity=4, clock=SystemClock(), tally=Tally()).open_count()
    0
"""

from collections.abc import Callable

from vakr.adapters.icmp.base import IcmpSocket, SocketOpenError, Tally
from vakr.adapters.icmp.channel import Channel
from vakr.adapters.icmp.sockets import open_socket
from vakr.adapters.icmp.wire import SocketKind
from vakr.ports.clock import Clock
from vakr.ports.icmp import CollectorFault

type Opener = Callable[[int, SocketKind, str | None], IcmpSocket]


class ChannelPool:
    """Open channels keyed by (IP version, source)."""

    def __init__(
        self,
        *,
        max_sockets: int,
        capacity: int,
        clock: Clock,
        tally: Tally,
        opener: Opener = open_socket,
        raw_fallback: bool = True,
    ) -> None:
        """Create an empty pool.

        Args:
            max_sockets: Most sockets open at once.
            capacity: Pending-reply capacity of each channel.
            clock: Clock handed to each channel.
            tally: Shared running totals.
            opener: Socket factory, replaced by a fake in unit tests.
            raw_fallback: Try a raw socket when a datagram socket is refused.

        Returns:
            None

        Raises:
            None
        """
        self._max, self._capacity = max_sockets, capacity
        self._clock, self._tally = clock, tally
        self._opener, self._raw_fallback = opener, raw_fallback
        self._channels: dict[tuple[int, str | None], Channel] = {}

    def open_count(self) -> int:
        """Return the number of open sockets.

        Args:
            None

        Returns:
            int: Channels not yet closed.

        Raises:
            None
        """
        return sum(not c.closed for c in self._channels.values())

    def get(self, version: int, source: str | None) -> Channel:
        """Return the open channel for a family and source, opening it if needed.

        Args:
            version: IP version, 4 or 6.
            source: Pinned source address, or ``None``.

        Returns:
            Channel: An open channel.

        Raises:
            SocketOpenError: If every socket is busy or opening fails.
        """
        key = (version, source)
        channel = self._channels.get(key)
        if channel is not None and not channel.closed:
            return channel
        self._channels.pop(key, None)
        if len(self._channels) >= self._max:
            idle = next((k for k, c in self._channels.items() if _idle(c)), None)
            if idle is None:
                raise SocketOpenError(CollectorFault.SOCKET_LIMIT, "all ICMP sockets are busy")
            self._channels.pop(idle).close("closed to make room for another source")
        sock, kind = self._open(version, source)
        channel = Channel(
            sock, version, kind, capacity=self._capacity, clock=self._clock, tally=self._tally
        )
        self._channels[key] = channel
        return channel

    def close_all(self) -> None:
        """Abort waiting probes and close every socket.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for channel in self._channels.values():
            channel.close()
        self._channels.clear()

    def _open(self, version: int, source: str | None) -> tuple[IcmpSocket, SocketKind]:
        """Open a datagram socket, falling back to raw when that is refused.

        Args:
            version: IP version, 4 or 6.
            source: Pinned source address, or ``None``.

        Returns:
            tuple[IcmpSocket, SocketKind]: The socket and its kind.

        Raises:
            SocketOpenError: The datagram error when no fallback works.
        """
        try:
            return self._opener(version, SocketKind.DATAGRAM, source), SocketKind.DATAGRAM
        except SocketOpenError as refused:
            if refused.fault is not CollectorFault.PERMISSION_DENIED or not self._raw_fallback:
                raise
            try:
                return self._opener(version, SocketKind.RAW, source), SocketKind.RAW
            except SocketOpenError:
                raise refused from None


def _idle(channel: Channel) -> bool:
    """Report whether a channel can be closed without losing a waiting probe.

    Args:
        channel: A pooled channel.

    Returns:
        bool: True when it is closed or has no probes waiting.

    Raises:
        None
    """
    return channel.closed or not len(channel.book.table)
