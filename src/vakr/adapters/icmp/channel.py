"""One ICMP socket shared by many targets, driven by the asyncio event loop.

A channel owns one non-blocking socket per (address family, source) and a
``ProbeBook`` of the probes waiting on it. The event loop calls
``_on_readable`` when replies or errors are queued; each call reads at most
``READ_BUDGET`` datagrams so a reply storm cannot starve other work, and the
loop calls again while more remain (level-triggered). Sharing one socket
across targets keeps file descriptors, epoll registrations, and socket
setup out of the per-probe path (ADR-023).

Dependencies:
    asyncio, secrets, socket: Event loop, unpredictable tokens, flags.
    vakr.adapters.icmp: Wire codec, book, socket helpers.
    vakr.ports: ``Clock`` and the probe value types.

Safety:
    No blocking calls: reads and sends are non-blocking. Local failures
    become ``CollectorFault`` evidence, never target loss (invariant 2).
    Tokens come from ``secrets`` so an off-path host cannot forge a reply.

Example:
    >>> READ_BUDGET, SEND_ATTEMPTS
    (256, 3)
"""

import asyncio
import secrets
import socket
import time
from typing import Any

from vakr.adapters.icmp import wire
from vakr.adapters.icmp.base import IcmpSocket, Tally, packed
from vakr.adapters.icmp.book import ProbeBook
from vakr.adapters.icmp.sockets import ASYNC_ERRNOS, receive_drops, send_fault, sockaddr
from vakr.adapters.icmp.table import TableFullError, Waiting
from vakr.adapters.icmp.wire_errors import IcmpError, parse_errqueue, parse_raw
from vakr.ports.clock import Clock
from vakr.ports.icmp import CollectorFault, ProbeSample

READ_BUDGET = 256
RECV_BYTES = 2048
ERR_ANC_BYTES = 512
SEND_ATTEMPTS = 3  # one send plus two retries after draining a stale queued error


class Channel:
    """One open ICMP socket and the probes waiting on it."""

    def __init__(
        self,
        sock: IcmpSocket,
        version: int,
        kind: wire.SocketKind,
        *,
        capacity: int,
        clock: Clock,
        tally: Tally,
    ) -> None:
        """Wrap an open socket and start watching it on the running loop.

        Args:
            sock: Non-blocking ICMP socket.
            version: IP version, 4 or 6.
            kind: Datagram or raw.
            capacity: Most probes that may wait at once.
            clock: Source of send times and RTTs.
            tally: Shared running totals.

        Returns:
            None

        Raises:
            RuntimeError: If no event loop is running.
        """
        self.sock, self.version, self.kind = sock, version, kind
        # Raw sockets see every ICMP packet on the host, so they need their own identifier.
        self.ident = secrets.randbelow(1 << 16) if kind is wire.SocketKind.RAW else 0
        self.closed = False
        self._loop = asyncio.get_running_loop()
        self.book = ProbeBook(capacity, clock, tally, self._loop)
        self._clock, self._tally = clock, tally
        self._drops = receive_drops(sock)
        self._loop.add_reader(sock.fileno(), self._on_readable)

    def send(self, index: int, target: str, timeout_s: float) -> asyncio.Future[ProbeSample]:
        """Send one probe and return a future for its outcome.

        Args:
            index: Probe position in its cycle.
            target: Canonical IP literal.
            timeout_s: Per-probe timeout.

        Returns:
            asyncio.Future[ProbeSample]: Resolves exactly once; already
            resolved as ``NOT_SENT`` when the send failed locally.

        Raises:
            None
        """
        if self.closed:  # a read failure closed it earlier in this cycle
            return self.book.unsent(index, CollectorFault.SOCKET_ERROR, "the ICMP socket closed")
        try:
            seq, address = self.book.table.next_seq(), sockaddr(target)
        except TableFullError:
            return self.book.unsent(index, CollectorFault.OVERLOADED, "pending-reply table full")
        except OSError as exc:  # an IPv6 zone that names no interface here
            return self.book.unsent(index, CollectorFault.LOCAL_NO_ROUTE, str(exc.strerror))
        token = secrets.token_bytes(wire.TOKEN_BYTES)
        packet = wire.echo_request(self.kind, self.version, self.ident, seq, token)
        sent_at, sent_mono = self._clock.now_utc(), self._clock.monotonic()
        if (error := self._sendto(packet, address)) is not None:
            return self.book.unsent(index, send_fault(error.errno), str(error.strerror))
        self._tally.sent += 1
        probe = Waiting(seq, token, packed(target), sent_mono, timeout_s, None)
        return self.book.add(probe, index, sent_at)

    def _sendto(self, packet: bytes, address: tuple[Any, ...]) -> OSError | None:
        """Send a packet, retrying when the failure was a stale queued error.

        Args:
            packet: Echo request bytes.
            address: Destination socket address.

        Returns:
            OSError | None: The genuine local error, or ``None`` on success.

        Raises:
            None
        """
        last: OSError | None = None
        for _ in range(SEND_ATTEMPTS):
            try:
                self.sock.sendto(packet, address)
            except OSError as exc:
                last = exc
                # An unreachable meant for another target fails this call once
                # and stays on the error queue: drain it, then try again.
                stale = exc.errno in ASYNC_ERRNOS and self.kind is wire.SocketKind.DATAGRAM
                if stale and self._drain_errors(READ_BUDGET):
                    continue
                return exc
            else:
                return None
        return last

    def drops(self) -> int | None:
        """Return the kernel drop counter, adding new drops to the tally.

        Args:
            None

        Returns:
            int | None: Total drops on this socket, or ``None`` if unknown.

        Raises:
            None
        """
        now = receive_drops(self.sock)
        if now is not None and self._drops is not None and now > self._drops:
            self._tally.receive_overflow += now - self._drops
        self._drops = self._drops if now is None else now
        return now

    def close(self, why: str = "collector stopped") -> None:
        """Stop watching the socket, abort waiting probes, and close it.

        Args:
            why: Operator-readable reason recorded on aborted probes.

        Returns:
            None

        Raises:
            None
        """
        if self.closed:
            return
        self.closed = True
        self._loop.remove_reader(self.sock.fileno())
        self.book.abort_all(why)
        self.sock.close()

    def _on_readable(self) -> None:
        """Read queued errors and replies, within ``READ_BUDGET``.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        budget = READ_BUDGET
        if self.kind is wire.SocketKind.DATAGRAM:
            budget -= self._drain_errors(budget)
        while budget > 0 and not self.closed:
            budget -= 1
            try:
                data, anc, _, address = self.sock.recvmsg(RECV_BYTES, wire.TIMESTAMP_ANC_BYTES)
            except BlockingIOError:
                return
            except OSError as exc:
                # A pending ICMP error surfaces here once; its details are on the queue.
                if exc.errno not in ASYNC_ERRNOS:
                    self._tally.receive_errors += 1
                    self.close(f"socket read failed: {exc.strerror}")
                continue
            stamp = wire.arrival_ns(anc)
            unread_s = None if stamp is None else (time.time_ns() - stamp) / 1e9
            self._on_packet(data, address, unread_s)

    def _drain_errors(self, budget: int) -> int:
        """Read up to ``budget`` entries from the socket error queue.

        Args:
            budget: Most entries to read.

        Returns:
            int: Entries read.

        Raises:
            None
        """
        count = 0
        while count < budget:
            try:
                data, anc, _, address = self.sock.recvmsg(
                    RECV_BYTES, ERR_ANC_BYTES, socket.MSG_ERRQUEUE
                )
            except BlockingIOError:
                break
            except OSError:
                self._tally.receive_errors += 1
                break
            count += 1
            target = packed(address[0]) if address else None
            if (error := parse_errqueue(data, anc, target, self.version)) is not None:
                self.book.on_error(error)
        return count

    def _on_packet(self, data: bytes, address: tuple[Any, ...], unread_s: float | None) -> None:
        """Handle one datagram read from the socket.

        Args:
            data: Bytes read.
            address: Sender socket address.
            unread_s: Seconds it waited unread, from the kernel stamp.

        Returns:
            None

        Raises:
            None
        """
        if self.kind is wire.SocketKind.DATAGRAM:
            message = wire.parse_echo(data, self.version)
            if message is None:
                self._tally.foreign += 1
                return
        else:
            message = parse_raw(data, self.version, address[0])
            if message is None or message.ident != self.ident:
                return  # another program's ICMP: raw sockets see all of it
        if isinstance(message, IcmpError):
            self.book.on_error(message)
        else:
            self.book.on_reply(message, packed(address[0]), unread_s)
