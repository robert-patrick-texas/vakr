r"""A fake ICMP socket layer for deterministic adapter tests (ICMP-T01..T04, T06).

``FakeSocket`` implements the ``IcmpSocket`` seam. Readiness comes from an
``eventfd`` (not a network socket), so the real asyncio loop calls the
channel's reader exactly as it would for a kernel socket, while the test
decides what arrives, in which order, and when. RTTs are exact because the
prober reads a ``ManualClock`` the test advances before each delivery. The
module name is unique on purpose: helpers are never imported from a
``conftest``.

Dependencies:
    asyncio, os, socket, struct: Event loop, eventfd, kernel layouts.
    vakr.adapters.icmp: Wire constants and the socket seam.

Example:
    >>> echo_reply(bytes([8, 0, 0, 0, 0, 0, 0, 7]) + b"vakr" + bytes(8))[:2]
    b'\x00\x00'
"""

import asyncio
import contextlib
import errno
import os
import socket
import struct
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from vakr.adapters.icmp import wire
from vakr.adapters.icmp.base import SocketOpenError
from vakr.adapters.icmp.wire import SocketKind
from vakr.ports.clock import ManualClock
from vakr.ports.icmp import CollectorFault

START = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
KERNEL_IDENT = 0x4D2  # what a ping socket would write over our identifier


def echo_reply(request: bytes, ident: int = KERNEL_IDENT) -> bytes:
    """Build the Echo reply a target would send for one of our requests.

    Args:
        request: The ICMP request bytes the prober sent.
        ident: Identifier the kernel put in the request.

    Returns:
        bytes: The reply: same sequence and payload, reply type.

    Raises:
        None
    """
    version = 4 if request[0] == wire.ECHO_REQUEST[4] else 6
    _, code, _, _, seq = wire.HEADER.unpack_from(request)
    reply = wire.HEADER.pack(wire.ECHO_REPLY[version], code, 0, ident, seq)
    return reply + request[wire.HEADER.size :]


def ext_err(origin: int, typ: int, code: int, offender: str | None, err: int = 0) -> bytes:
    """Build ``sock_extended_err`` plus the offender address, as the kernel does.

    Args:
        origin: ``SO_EE_ORIGIN_*`` (1 local, 2 ICMP, 3 ICMPv6).
        typ: ICMP type.
        code: ICMP code.
        offender: Reporting router, or ``None``.
        err: ``ee_errno``.

    Returns:
        bytes: The control-message payload.

    Raises:
        None
    """
    head = struct.pack("=IBBBBII", err, origin, typ, code, 0, 0, 0)
    if offender is None:
        return head + bytes(16)
    if ":" in offender:
        packed = socket.inet_pton(socket.AF_INET6, offender)
        return head + struct.pack("=HHI16sI", socket.AF_INET6, 0, 0, packed, 0)
    packed = socket.inet_pton(socket.AF_INET, offender)
    return head + struct.pack("=HH4s8x", socket.AF_INET, 0, packed)


def address_of(target: str) -> tuple[Any, ...]:
    """Return the socket address tuple a reply from ``target`` carries.

    Args:
        target: IP literal.

    Returns:
        tuple[Any, ...]: ``(host, 0)`` or ``(host, 0, 0, 0)``.

    Raises:
        None
    """
    return (target, 0, 0, 0) if ":" in target else (target, 0)


class FakeSocket:
    """An in-memory ICMP socket whose arrivals the test controls."""

    def __init__(self, version: int, kind: SocketKind, source: str | None) -> None:
        """Create the socket.

        Args:
            version: IP version.
            kind: Datagram or raw.
            source: Bound source address.

        Returns:
            None

        Raises:
            None
        """
        self.version, self.kind, self.source = version, kind, source
        self._fd = os.eventfd(0, os.EFD_NONBLOCK | os.EFD_CLOEXEC)
        self.inbox: deque[tuple[bytes, list[Any], tuple[Any, ...]]] = deque()
        self.errqueue: deque[tuple[bytes, list[Any], tuple[Any, ...]]] = deque()
        self.sent: list[tuple[bytes, tuple[Any, ...]]] = []
        self.send_errors: deque[int] = deque()
        self.recv_errors: deque[int] = deque()
        self.errqueue_errors: deque[int] = deque()
        self.requeue_on_send_error: tuple[bytes, list[Any], tuple[Any, ...]] | None = None
        self.drops = 0
        self.closed = False
        self.auto_reply = False

    def fileno(self) -> int:
        """Return the eventfd that signals readiness.

        Args:
            None

        Returns:
            int: File descriptor.

        Raises:
            None
        """
        return self._fd

    def sendto(self, data: bytes, address: Any, /) -> int:
        """Record a send, or raise the next scripted send error.

        Args:
            data: Packet.
            address: Destination.

        Returns:
            int: Bytes "sent".

        Raises:
            OSError: The next errno in ``send_errors``.
        """
        if self.send_errors:
            code = self.send_errors.popleft()
            if self.requeue_on_send_error is not None:  # a fresh ICMP error each time
                self.errqueue.append(self.requeue_on_send_error)
            raise OSError(code, os.strerror(code))
        self.sent.append((data, address))
        if self.auto_reply:
            self.deliver(echo_reply(data), address)
        return len(data)

    def recvmsg(self, bufsize: int, ancbufsize: int = 0, flags: int = 0, /) -> tuple[Any, ...]:
        """Pop the next queued datagram or error.

        Args:
            bufsize: Ignored.
            ancbufsize: Ignored.
            flags: ``MSG_ERRQUEUE`` reads the error queue.

        Returns:
            tuple[Any, ...]: ``(data, ancdata, 0, address)``.

        Raises:
            OSError: A scripted receive error, or ``BlockingIOError`` when empty.
        """
        del bufsize, ancbufsize  # the fake has no size limits
        errq = bool(flags & socket.MSG_ERRQUEUE)
        scripted = self.errqueue_errors if errq else self.recv_errors
        if scripted:
            code = scripted.popleft()
            raise OSError(code, os.strerror(code))
        queue = self.errqueue if errq else self.inbox
        if not queue:
            if not self.inbox and not self.errqueue and not self.recv_errors:
                self._clear()
            raise BlockingIOError(errno.EAGAIN, "empty")
        data, anc, address = queue.popleft()
        return data, anc, 0, address

    def getsockopt(self, level: int, option: int, buflen: int, /) -> bytes:
        """Return ``SO_MEMINFO`` with the scripted drop count.

        Args:
            level: Ignored.
            option: Ignored.
            buflen: Ignored.

        Returns:
            bytes: Nine native unsigned ints; the last is drops.

        Raises:
            None
        """
        del level, option, buflen  # only SO_MEMINFO is ever asked for
        return struct.pack("=9I", *([0] * 8), self.drops)

    def close(self) -> None:
        """Close the eventfd.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if not self.closed:
            self.closed = True
            os.close(self._fd)

    def deliver(self, data: bytes, address: tuple[Any, ...], anc: list[Any] | None = None) -> None:
        """Queue a datagram for the prober to read.

        Args:
            data: Bytes as the socket would return them.
            address: Sender.
            anc: Control messages, such as an arrival timestamp.

        Returns:
            None

        Raises:
            None
        """
        self.inbox.append((data, anc or [], address))
        self._signal()

    def deliver_error(self, data: bytes, anc: list[Any], address: tuple[Any, ...]) -> None:
        """Queue an error-queue entry.

        Args:
            data: The quoted request.
            anc: Control messages, normally one ``IP_RECVERR`` entry.
            address: Original destination.

        Returns:
            None

        Raises:
            None
        """
        self.errqueue.append((data, anc, address))
        self._signal()

    def fail_reads(self, code: int) -> None:
        """Make the next read raise ``code`` and wake the reader.

        Args:
            code: errno to raise.

        Returns:
            None

        Raises:
            None
        """
        self.recv_errors.append(code)
        self._signal()

    def _signal(self) -> None:
        """Mark the eventfd readable.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if not self.closed:
            os.eventfd_write(self._fd, 1)

    def _clear(self) -> None:
        """Reset the eventfd so the loop stops calling the reader.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with contextlib.suppress(BlockingIOError):
            os.eventfd_read(self._fd)


class FakeNet:
    """Opens ``FakeSocket`` objects and can refuse some kinds of socket."""

    def __init__(self, refuse: dict[SocketKind, CollectorFault] | None = None) -> None:
        """Create the factory.

        Args:
            refuse: Socket kinds to refuse, with the fault to raise.

        Returns:
            None

        Raises:
            None
        """
        self.refuse = dict(refuse or {})
        self.sockets: list[FakeSocket] = []
        self.auto_reply = False

    def __call__(self, version: int, kind: SocketKind, source: str | None) -> FakeSocket:
        """Open a fake socket (the ``Opener`` signature).

        Args:
            version: IP version.
            kind: Datagram or raw.
            source: Bound source.

        Returns:
            FakeSocket: The new socket.

        Raises:
            SocketOpenError: If ``kind`` is refused.
        """
        if kind in self.refuse:
            raise SocketOpenError(self.refuse[kind], "refused by the test")
        sock = FakeSocket(version, kind, source)
        sock.auto_reply = self.auto_reply
        self.sockets.append(sock)
        return sock


def manual_clock() -> ManualClock:
    """Return a clock starting at ``START`` with monotonic time 1000 s.

    Args:
        None

    Returns:
        ManualClock: The clock.

    Raises:
        None
    """
    return ManualClock(START, monotonic_start=1000.0)


def advance_us(clock: ManualClock, micros: int) -> None:
    """Advance both clocks by ``micros`` microseconds.

    Args:
        clock: The manual clock.
        micros: Microseconds.

    Returns:
        None

    Raises:
        None
    """
    clock.advance(timedelta(microseconds=micros))


async def wait_sent(
    sock_list: list[FakeSocket], count: int, spins: int = 1000, index: int = 0
) -> FakeSocket:
    """Yield to the loop until socket ``index`` has sent ``count`` packets.

    Args:
        sock_list: ``FakeNet.sockets`` (read live, so new sockets appear).
        count: Packets expected.
        spins: Most loop iterations to wait.
        index: Which opened socket to watch.

    Returns:
        FakeSocket: The socket.

    Raises:
        AssertionError: If the packets were not sent in time.
    """
    for _ in range(spins):
        if len(sock_list) > index and len(sock_list[index].sent) >= count:
            return sock_list[index]
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} sends on socket {index}")


async def drain(sock: FakeSocket, spins: int = 1000) -> None:
    """Yield until the prober has read everything queued on ``sock``.

    Args:
        sock: The fake socket.
        spins: Most loop iterations to wait.

    Returns:
        None

    Raises:
        AssertionError: If the queue was not read.
    """
    for _ in range(spins):
        if not sock.inbox and not sock.errqueue:
            return
        await asyncio.sleep(0)
    raise AssertionError("prober did not read the queued datagrams")
