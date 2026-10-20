"""Decode ICMP errors about our Echo requests: error queue and raw packets.

When a router answers a probe with "host unreachable" or "time exceeded",
the probe is lost, and saying *who* reported *what* helps the operator
more than a bare timeout. On a datagram ping socket with ``IP_RECVERR``,
the kernel queues such errors on the socket error queue with the original
request quoted and the reporter in ``sock_extended_err``. On a raw socket
the error arrives as an ordinary ICMP packet quoting the request.

Redirects, source quench, and "packet too big" do not mean the probe
failed, so they decode to ``None``.

Dependencies:
    os, socket, struct: ``strerror``, address rendering, C layouts.
    vakr.adapters.icmp.wire: Echo decoding.

Safety:
    Pure functions over bytes. Truncated or unrelated input decodes to
    ``None``, never an exception, because any router can send us ICMP.

Example:
    >>> error_reason(4, 3, 1), error_reason(6, 3, 0), error_reason(4, 5, 1)
    ('host unreachable', 'hop limit exceeded in transit', None)
"""

import os
import socket
import struct
from dataclasses import dataclass

from vakr.adapters.icmp.wire import ECHO_REPLY, ECHO_REQUEST, HEADER, Echo, parse_echo

_EXT_ERR = struct.Struct("=IBBBBII")  # struct sock_extended_err (linux/errqueue.h)
_ORIGIN_LOCAL, _ORIGIN_ICMP, _ORIGIN_ICMP6 = 1, 2, 3
_RECVERR = {4: (socket.IPPROTO_IP, socket.IP_RECVERR), 6: (socket.IPPROTO_IPV6, 25)}
_V4_UNREACH = {
    0: "network unreachable",
    1: "host unreachable",
    9: "network administratively prohibited",
    10: "host administratively prohibited",
    13: "communication administratively prohibited",
}
_V6_UNREACH = {
    0: "no route to destination",
    1: "communication administratively prohibited",
    3: "address unreachable",
    5: "source address failed policy",
    6: "reject route to destination",
}


@dataclass(frozen=True, slots=True)
class IcmpError:
    """An ICMP error (or local error) about one of our Echo requests.

    Attributes:
        seq: Sequence number of the request it is about.
        token: The request's token, if the quote was long enough.
        target: Packed destination address of the request, if known.
        reason: Operator-readable explanation, such as "host unreachable".
        reporter: Address of the router or host that reported it, if any.
        local: True when the local kernel, not the network, raised it.
        ident: Identifier of the quoted request (raw sockets check it).
    """

    seq: int
    token: bytes | None
    target: bytes | None
    reason: str
    reporter: str | None = None
    local: bool = False
    ident: int = 0


def error_reason(version: int, typ: int, code: int) -> str | None:
    """Explain an ICMP error type and code in operator language.

    Args:
        version: IP version, 4 or 6.
        typ: ICMP type.
        code: ICMP code.

    Returns:
        str | None: The explanation, or ``None`` for messages that do not
        mean the probe failed (redirect, source quench, packet too big).

    Raises:
        None
    """
    if version == 4:
        if typ == 3:
            return _V4_UNREACH.get(code, f"destination unreachable (code {code})")
        return {11: "time exceeded in transit", 12: "parameter problem"}.get(typ)
    if typ == 1:
        return _V6_UNREACH.get(code, f"destination unreachable (code {code})")
    return {3: "hop limit exceeded in transit", 4: "parameter problem"}.get(typ)


def _sockaddr(raw: bytes) -> str | None:
    """Render the offender ``sockaddr`` that follows ``sock_extended_err``.

    Args:
        raw: Bytes of a ``sockaddr_in`` or ``sockaddr_in6``.

    Returns:
        str | None: The address, or ``None`` when absent or unrecognised.

    Raises:
        None
    """
    family = struct.unpack_from("=H", raw)[0] if len(raw) >= 2 else None
    if family == socket.AF_INET and len(raw) >= 8:
        return socket.inet_ntop(socket.AF_INET, raw[4:8])
    if family == socket.AF_INET6 and len(raw) >= 24:
        return socket.inet_ntop(socket.AF_INET6, raw[8:24])
    return None


def parse_errqueue(
    data: bytes, ancdata: list[tuple[int, int, bytes]], target: bytes | None, version: int
) -> IcmpError | None:
    """Decode one ``MSG_ERRQUEUE`` read from a datagram ping socket.

    Args:
        data: The quoted original request (ICMP header first), maybe truncated.
        ancdata: Control messages from ``recvmsg``.
        target: Packed destination address the kernel returned, if any.
        version: IP version of the socket.

    Returns:
        IcmpError | None: The error, or ``None`` when it is not about an
        Echo request or does not mean the probe failed.

    Raises:
        None
    """
    level, kind = _RECVERR[version]
    raw = next((d for lvl, t, d in ancdata if (lvl, t) == (level, kind)), None)
    request = parse_echo(data, version, ECHO_REQUEST[version])
    if raw is None or len(raw) < _EXT_ERR.size or request is None:
        return None
    err, origin, typ, code, _, _, _ = _EXT_ERR.unpack_from(raw)
    reporter = _sockaddr(raw[_EXT_ERR.size :])
    if origin in (_ORIGIN_ICMP, _ORIGIN_ICMP6):
        reason = error_reason(version, typ, code)
        if reason is None:
            return None
        return IcmpError(request.seq, request.token, target, reason, reporter)
    if origin == _ORIGIN_LOCAL:
        reason = f"local error: {os.strerror(err)}"
        return IcmpError(request.seq, request.token, target, reason, None, local=True)
    return None


def parse_raw(data: bytes, version: int, source: str | None) -> Echo | IcmpError | None:
    """Decode one packet read from a raw ICMP or ICMPv6 socket.

    Args:
        data: Bytes read; IPv4 reads start with the IP header.
        version: IP version of the socket.
        source: Address the packet came from.

    Returns:
        Echo | IcmpError | None: A reply, an error about an Echo request,
        or ``None`` for unrelated traffic (raw sockets see every ICMP packet).

    Raises:
        None
    """
    if version == 4:
        if len(data) < 20 or data[0] >> 4 != 4:
            return None
        data = data[(data[0] & 0x0F) * 4 :]
    if len(data) < HEADER.size:
        return None
    if data[0] == ECHO_REPLY[version]:
        return parse_echo(data, version)
    reason = error_reason(version, data[0], data[1])
    quoted = _quoted_request(data[HEADER.size :], version)
    if reason is None or quoted is None:
        return None
    target, request = quoted
    return IcmpError(request.seq, request.token, target, reason, source, ident=request.ident)


def _quoted_request(quoted: bytes, version: int) -> tuple[bytes, Echo] | None:
    """Find our Echo request inside the packet an ICMP error quotes.

    Args:
        quoted: The quoted IP header and the start of its payload.
        version: IP version.

    Returns:
        tuple[bytes, Echo] | None: Packed destination and the request, or
        ``None`` if the quote is not an Echo request.

    Raises:
        None
    """
    if version == 4:
        if len(quoted) < 20 or quoted[0] >> 4 != 4 or quoted[9] != socket.IPPROTO_ICMP:
            return None
        header, target = (quoted[0] & 0x0F) * 4, quoted[16:20]
    else:
        if len(quoted) < 40 or quoted[6] != socket.IPPROTO_ICMPV6:
            return None
        header, target = 40, quoted[24:40]
    request = parse_echo(quoted[header:], version, ECHO_REQUEST[version])
    return None if request is None else (target, request)
