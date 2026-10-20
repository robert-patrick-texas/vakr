"""Open ICMP sockets and translate their errors into collector evidence.

``socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)`` and its ICMPv6 twin work
without privilege when ``net.ipv4.ping_group_range`` includes one of the
process's groups (ICMP-012); otherwise the kernel answers ``EACCES``. Raw
sockets need ``CAP_NET_RAW`` and are used only as the documented fallback.
Every socket is non-blocking and close-on-exec, and datagram sockets enable
``IP_RECVERR`` so routers' unreachable messages reach the probe they are
about instead of looking like silence.

Local errors are never target evidence: a refused send, a missing route on
this host, or a full buffer is the collector's problem (invariant 2), so
each errno maps to a ``CollectorFault``.

Dependencies:
    errno, ipaddress, socket, struct: Socket creation and option layouts.
    vakr.adapters.icmp.base: Socket seam and ``SocketOpenError``.

Safety:
    No blocking calls: sockets are created with ``SOCK_NONBLOCK``. Option
    failures close the socket before raising, so nothing leaks.

Example:
    >>> open_fault(errno.EACCES).value, send_fault(errno.ENETUNREACH).value
    ('permission_denied', 'local_no_route')
    >>> sockaddr("192.0.2.1")
    ('192.0.2.1', 0)
"""

import contextlib
import errno
import ipaddress
import socket
import struct
from typing import Any

from vakr.adapters.icmp.base import IcmpSocket, SocketOpenError
from vakr.adapters.icmp.wire import SO_TIMESTAMPNS, SocketKind
from vakr.ports.icmp import CollectorFault

RECEIVE_BUFFER_BYTES = 1 << 20
"""Requested ``SO_RCVBUF``; the kernel caps it at ``net.core.rmem_max`` (E.22)."""

_SO_MEMINFO = 55  # asm-generic value (amd64, arm64); Python does not export it
_SK_MEMINFO = struct.Struct("=9I")  # SK_MEMINFO_VARS; index 8 is SK_MEMINFO_DROPS
_IPV6_RECVERR = 25

# The kernel reports an ICMP error queued on an IP_RECVERR socket on the
# *next* call, so a send to one target can fail with an error about another.
ASYNC_ERRNOS = frozenset(
    {
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.EHOSTDOWN,
        errno.ENONET,
        errno.ECONNREFUSED,
        errno.ENOPROTOOPT,
        errno.EOPNOTSUPP,
        errno.EMSGSIZE,
        errno.EACCES,
        errno.EPROTO,
        errno.EREMOTEIO,
    }
)
"""Errnos that an ICMP error can leave pending on a datagram ping socket."""

_OPEN_FAULTS = {
    errno.EACCES: CollectorFault.PERMISSION_DENIED,
    errno.EPERM: CollectorFault.PERMISSION_DENIED,
    errno.EAFNOSUPPORT: CollectorFault.FAMILY_UNSUPPORTED,
    errno.EPROTONOSUPPORT: CollectorFault.FAMILY_UNSUPPORTED,
    errno.ESOCKTNOSUPPORT: CollectorFault.FAMILY_UNSUPPORTED,
    errno.EADDRNOTAVAIL: CollectorFault.SOURCE_UNAVAILABLE,
}
_SEND_FAULTS = {
    errno.ENETUNREACH: CollectorFault.LOCAL_NO_ROUTE,
    errno.EHOSTUNREACH: CollectorFault.LOCAL_NO_ROUTE,
    errno.EACCES: CollectorFault.LOCAL_POLICY,
    errno.EPERM: CollectorFault.LOCAL_POLICY,
    errno.EAGAIN: CollectorFault.OVERLOADED,
    errno.ENOBUFS: CollectorFault.OVERLOADED,
    errno.ENOMEM: CollectorFault.OVERLOADED,
    errno.EADDRNOTAVAIL: CollectorFault.SOURCE_UNAVAILABLE,
}


def open_fault(err: int | None) -> CollectorFault:
    """Classify an errno from ``socket()``, ``setsockopt()``, or ``bind()``.

    Args:
        err: The errno, or ``None`` if unknown.

    Returns:
        CollectorFault: ``PERMISSION_DENIED`` for EACCES/EPERM, and so on.

    Raises:
        None
    """
    return _OPEN_FAULTS.get(err or 0, CollectorFault.SOCKET_ERROR)


def send_fault(err: int | None) -> CollectorFault:
    """Classify an errno from ``sendto()``.

    Args:
        err: The errno, or ``None`` if unknown.

    Returns:
        CollectorFault: ``LOCAL_NO_ROUTE``, ``LOCAL_POLICY``, ``OVERLOADED``,
        ``SOURCE_UNAVAILABLE``, or ``SEND_ERROR``.

    Raises:
        None
    """
    return _SEND_FAULTS.get(err or 0, CollectorFault.SEND_ERROR)


def sockaddr(address: str) -> tuple[Any, ...]:
    """Build the socket address tuple for an IP literal.

    Args:
        address: IPv4 or IPv6 literal; IPv6 may carry a ``%zone``.

    Returns:
        tuple[Any, ...]: ``(host, 0)`` or ``(host, 0, 0, scope_id)``.

    Raises:
        OSError: If an IPv6 zone names no interface on this host.
        ValueError: If ``address`` is not an IP literal.
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv4Address):
        return (str(ip), 0)
    zone = ip.scope_id or ""
    scope = int(zone) if zone.isdigit() else (socket.if_nametoindex(zone) if zone else 0)
    return (str(ipaddress.IPv6Address(ip.packed)), 0, 0, scope)


def open_socket(version: int, kind: SocketKind, source: str | None) -> IcmpSocket:
    """Open and configure one non-blocking ICMP socket.

    Args:
        version: IP version, 4 or 6.
        kind: Datagram (unprivileged) or raw (needs ``CAP_NET_RAW``).
        source: Address to bind as the probe source, or ``None``.

    Returns:
        IcmpSocket: The configured socket.

    Raises:
        SocketOpenError: If creation, an option, or the bind fails.
    """
    v4 = version == 4
    family = socket.AF_INET if v4 else socket.AF_INET6
    proto = socket.IPPROTO_ICMP if v4 else socket.IPPROTO_ICMPV6
    base = socket.SOCK_DGRAM if kind is SocketKind.DATAGRAM else socket.SOCK_RAW
    try:
        sock = socket.socket(family, base | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC, proto)
    except OSError as exc:
        raise SocketOpenError(open_fault(exc.errno), exc.strerror or "socket failed") from None
    try:
        if kind is SocketKind.DATAGRAM:
            level = socket.IPPROTO_IP if v4 else socket.IPPROTO_IPV6
            sock.setsockopt(level, socket.IP_RECVERR if v4 else _IPV6_RECVERR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER_BYTES)
        if source is not None:
            sock.bind(sockaddr(source))
    except OSError as exc:
        sock.close()
        raise SocketOpenError(open_fault(exc.errno), exc.strerror or "setup failed") from None
    # Optional: without arrival stamps, event-loop lag adds to RTTs (E.22).
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
    return sock


def receive_drops(sock: IcmpSocket) -> int | None:
    """Read how many datagrams the kernel dropped on this socket's full buffer.

    Args:
        sock: An ICMP socket.

    Returns:
        int | None: The kernel's drop counter, or ``None`` if unavailable.

    Raises:
        None
    """
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, _SO_MEMINFO, _SK_MEMINFO.size)
    except OSError:
        return None
    return _SK_MEMINFO.unpack(raw)[8] if len(raw) == _SK_MEMINFO.size else None
