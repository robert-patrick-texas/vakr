"""ICMP Echo request encoding and reply decoding (ADR-023).

On a Linux ping socket (``SOCK_DGRAM``/``IPPROTO_ICMP``) the kernel fills in
the checksum and replaces the Echo identifier with the socket's own, so a
reply cannot be recognised by identifier. Every probe therefore carries a
4-byte magic and an 8-byte random token in its payload, and replies are
matched by sequence number plus token. On a raw socket (the ``CAP_NET_RAW``
fallback) vakr sets the identifier and the IPv4 checksum itself. ICMP
errors are decoded by ``vakr.adapters.icmp.wire_errors``.

Replies carry the kernel's arrival time (``SO_TIMESTAMPNS``), so a reply
read late because the event loop was busy still gets its true RTT.

Dependencies:
    socket, struct: Control-message sizes and header layouts.

Safety:
    Pure functions over bytes. Malformed or truncated input decodes to
    ``None``, never an exception, because any host can send us ICMP.

Example:
    >>> packet = echo_request(SocketKind.DATAGRAM, 4, 0, 7, b"12345678")
    >>> parse_echo(bytes([0, 0]) + packet[2:], 4)
    Echo(ident=0, seq=7, token=b'12345678')
"""

import socket
import struct
from dataclasses import dataclass
from enum import StrEnum

MAGIC = b"vakr"
TOKEN_BYTES = 8
HEADER = struct.Struct("!BBHHH")  # type, code, checksum, identifier, sequence
ECHO_REQUEST = {4: 8, 6: 128}
ECHO_REPLY = {4: 0, 6: 129}
SO_TIMESTAMPNS = 35  # asm-generic SO_TIMESTAMPNS_OLD (amd64, arm64); not exported by Python
_TIMESPEC = struct.Struct("=qq")  # struct timespec on 64-bit Linux
TIMESTAMP_ANC_BYTES = socket.CMSG_SPACE(_TIMESPEC.size)


class SocketKind(StrEnum):
    """Which Linux socket type carries the probes."""

    DATAGRAM = "datagram"  # unprivileged ping socket (default, ICMP-012)
    RAW = "raw"  # needs CAP_NET_RAW; documented fallback only


@dataclass(frozen=True, slots=True)
class Echo:
    """A decoded Echo message.

    Attributes:
        ident: Echo identifier.
        seq: Echo sequence number.
        token: Probe token from the payload, or ``None`` if absent.
    """

    ident: int
    seq: int
    token: bytes | None


def checksum(data: bytes) -> int:
    """Compute the RFC 1071 Internet checksum.

    Args:
        data: Bytes to sum; an odd length is padded with a zero byte.

    Returns:
        int: The 16-bit one's-complement checksum.

    Raises:
        None
    """
    padded = data + b"\x00" * (len(data) % 2)
    total = sum(struct.unpack(f"!{len(padded) // 2}H", padded))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def echo_request(kind: SocketKind, version: int, ident: int, seq: int, token: bytes) -> bytes:
    """Build one Echo request.

    Args:
        kind: Socket type the packet is for.
        version: IP version, 4 or 6.
        ident: Identifier; ignored by the kernel on datagram sockets.
        seq: Sequence number, 0-65535.
        token: Per-probe random token of ``TOKEN_BYTES`` bytes.

    Returns:
        bytes: ICMP header and payload. The checksum is zero (the kernel
        fills it) except for raw IPv4, where only vakr can compute it.

    Raises:
        None
    """
    typ, body = ECHO_REQUEST[version], MAGIC + token
    total = 0
    if kind is SocketKind.RAW and version == 4:
        total = checksum(HEADER.pack(typ, 0, 0, ident, seq) + body)
    return HEADER.pack(typ, 0, total, ident, seq) + body


def _token(payload: bytes) -> bytes | None:
    """Extract the probe token from an Echo payload.

    Args:
        payload: Bytes after the 8-byte ICMP header.

    Returns:
        bytes | None: The token, or ``None`` if the magic or bytes are missing.

    Raises:
        None
    """
    end = len(MAGIC) + TOKEN_BYTES
    return payload[len(MAGIC) : end] if len(payload) >= end and payload.startswith(MAGIC) else None


def parse_echo(message: bytes, version: int, typ: int | None = None) -> Echo | None:
    """Decode an ICMP Echo message of the expected type.

    Args:
        message: ICMP header and payload, without any IP header.
        version: IP version, 4 or 6.
        typ: Expected ICMP type; defaults to the Echo reply type.

    Returns:
        Echo | None: The decoded message, or ``None`` for anything else.

    Raises:
        None
    """
    if len(message) < HEADER.size or message[0] != (ECHO_REPLY[version] if typ is None else typ):
        return None
    _, _, _, ident, seq = HEADER.unpack_from(message)
    return Echo(ident, seq, _token(message[HEADER.size :]))


def arrival_ns(ancdata: list[tuple[int, int, bytes]]) -> int | None:
    """Return the kernel's wall-clock arrival time from ``SCM_TIMESTAMPNS``.

    Args:
        ancdata: Control messages from ``recvmsg``.

    Returns:
        int | None: Nanoseconds since the epoch, or ``None`` if absent.

    Raises:
        None
    """
    for level, kind, data in ancdata:
        if (level, kind) == (socket.SOL_SOCKET, SO_TIMESTAMPNS) and len(data) >= _TIMESPEC.size:
            seconds, nanos = _TIMESPEC.unpack_from(data)
            return seconds * 1_000_000_000 + nanos
    return None
