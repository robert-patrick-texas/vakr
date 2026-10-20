"""Tests for socket opening and errno classification (ICMP-012, OBS-002).

``socket.socket`` is replaced by a recorder so creation, option, and bind
failures are exercised without touching the network, and every failure is
checked to close the socket and map to a ``CollectorFault``.

Dependencies:
    errno, pytest: Error codes and monkeypatching.
    vakr.adapters.icmp.sockets: The functions under test.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_sockets.py
"""

import errno
import socket
from typing import cast

import pytest

from vakr.adapters.icmp import sockets
from vakr.adapters.icmp.base import IcmpSocket, SocketOpenError
from vakr.adapters.icmp.wire import SocketKind
from vakr.ports.icmp import CollectorFault


class RecordingSocket:
    """Stands in for ``socket.socket`` and fails where told to."""

    fail_on: dict[str, int] = {}  # noqa: RUF012 - reset by each test
    made: list[RecordingSocket] = []  # noqa: RUF012 - reset by each test

    def __init__(self, family: int, kind: int, proto: int) -> None:
        """Record the creation, or fail it.

        Args:
            family: Address family.
            kind: Socket type and flags.
            proto: Protocol.

        Returns:
            None

        Raises:
            OSError: When ``fail_on["socket"]`` is set.
        """
        if code := self.fail_on.get("socket"):
            raise OSError(code, "refused")
        self.args, self.options, self.bound, self.closed = (family, kind, proto), [], None, False
        self.made.append(self)

    def setsockopt(self, level: int, option: int, value: int) -> None:
        """Record an option, or fail it.

        Args:
            level: Level.
            option: Option.
            value: Value.

        Returns:
            None

        Raises:
            OSError: When ``fail_on[str(option)]`` is set.
        """
        if code := self.fail_on.get(str(option)):
            raise OSError(code, "unsupported")
        self.options.append((level, option, value))

    def bind(self, address: tuple[object, ...]) -> None:
        """Record the bind, or fail it.

        Args:
            address: Socket address.

        Returns:
            None

        Raises:
            OSError: When ``fail_on["bind"]`` is set.
        """
        if code := self.fail_on.get("bind"):
            raise OSError(code, "cannot assign")
        self.bound = address

    def close(self) -> None:
        """Record the close.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.closed = True


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[RecordingSocket]:
    """Install ``RecordingSocket`` in place of ``socket.socket``.

    Args:
        monkeypatch: Pytest monkeypatch.

    Returns:
        type[RecordingSocket]: The class, with fresh failure and creation lists.

    Raises:
        None
    """
    RecordingSocket.fail_on, RecordingSocket.made = {}, []
    monkeypatch.setattr(sockets.socket, "socket", RecordingSocket)
    return RecordingSocket


def test_datagram_socket_is_non_blocking_with_recverr_timestamps_and_source(
    recorder: type[RecordingSocket],
) -> None:
    """ICMP-012: an unprivileged ping socket, configured for errors and arrival stamps.

    Args:
        recorder: The fake socket class.

    Returns:
        None

    Raises:
        None
    """
    sockets.open_socket(6, SocketKind.DATAGRAM, "2001:db8::10")
    made = recorder.made[0]
    family, kind, proto = made.args
    assert (family, proto) == (socket.AF_INET6, socket.IPPROTO_ICMPV6)
    assert kind & socket.SOCK_DGRAM
    assert kind & socket.SOCK_NONBLOCK
    assert [o[1] for o in made.options] == [25, socket.SO_RCVBUF, 35]
    assert made.bound == ("2001:db8::10", 0, 0, 0)


def test_raw_socket_skips_recverr(recorder: type[RecordingSocket]) -> None:
    """Raw sockets receive ICMP errors as packets, so IP_RECVERR is not set.

    Args:
        recorder: The fake socket class.

    Returns:
        None

    Raises:
        None
    """
    sockets.open_socket(4, SocketKind.RAW, None)
    made = recorder.made[0]
    assert made.args[1] & socket.SOCK_RAW
    assert [o[1] for o in made.options] == [socket.SO_RCVBUF, 35]


@pytest.mark.parametrize(
    ("where", "code", "fault"),
    [
        ("socket", errno.EACCES, CollectorFault.PERMISSION_DENIED),
        ("socket", errno.EAFNOSUPPORT, CollectorFault.FAMILY_UNSUPPORTED),
        ("socket", errno.EMFILE, CollectorFault.SOCKET_ERROR),
        ("bind", errno.EADDRNOTAVAIL, CollectorFault.SOURCE_UNAVAILABLE),
        (str(socket.SO_RCVBUF), errno.ENOPROTOOPT, CollectorFault.SOCKET_ERROR),
    ],
)
def test_open_failures_close_the_socket_and_name_the_fault(
    recorder: type[RecordingSocket], where: str, code: int, fault: CollectorFault
) -> None:
    """Nothing leaks, and the operator learns which collector-side step failed.

    Args:
        recorder: The fake socket class.
        where: Step that fails.
        code: Its errno.
        fault: Expected classification.

    Returns:
        None

    Raises:
        None
    """
    recorder.fail_on = {where: code}
    with pytest.raises(SocketOpenError) as caught:
        sockets.open_socket(4, SocketKind.DATAGRAM, "192.0.2.10")
    assert caught.value.fault is fault
    assert all(s.closed for s in recorder.made)


def test_missing_timestamp_support_is_tolerated(recorder: type[RecordingSocket]) -> None:
    """Arrival stamps are an accuracy aid; a kernel without them still probes.

    Args:
        recorder: The fake socket class.

    Returns:
        None

    Raises:
        None
    """
    recorder.fail_on = {"35": errno.ENOPROTOOPT}
    assert sockets.open_socket(4, SocketKind.DATAGRAM, None) is recorder.made[0]


@pytest.mark.parametrize(
    ("code", "fault"),
    [
        (errno.EACCES, CollectorFault.LOCAL_POLICY),
        (errno.EHOSTUNREACH, CollectorFault.LOCAL_NO_ROUTE),
        (errno.EAGAIN, CollectorFault.OVERLOADED),
        (None, CollectorFault.SEND_ERROR),
    ],
)
def test_send_errnos_are_collector_faults(code: int | None, fault: CollectorFault) -> None:
    """Local send failures are never target evidence (invariant 2).

    Args:
        code: errno.
        fault: Expected classification.

    Returns:
        None

    Raises:
        None
    """
    assert sockets.send_fault(code) is fault


def test_socket_addresses_resolve_zones() -> None:
    """IPv6 link-local targets need a zone; unknown zone names fail as OSError.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert sockets.sockaddr("fe80::1%3") == ("fe80::1", 0, 0, 3)
    assert sockets.sockaddr("fe80::1%lo") == ("fe80::1", 0, 0, socket.if_nametoindex("lo"))
    with pytest.raises(OSError, match=r"(?i)no such device"):
        sockets.sockaddr("fe80::1%vakr-no-such-if")


class OptionlessSocket:
    """A socket whose ``getsockopt`` misbehaves."""

    def __init__(self, reply: bytes | None) -> None:
        """Set the reply.

        Args:
            reply: Bytes to return, or ``None`` to raise.

        Returns:
            None

        Raises:
            None
        """
        self.reply = reply

    def getsockopt(self, level: int, option: int, buflen: int) -> bytes:
        """Return the configured reply.

        Args:
            level: Ignored.
            option: Ignored.
            buflen: Ignored.

        Returns:
            bytes: The reply.

        Raises:
            OSError: When no reply is configured.
        """
        del level, option, buflen  # the reply is fixed
        if self.reply is None:
            raise OSError(errno.ENOPROTOOPT, "no")
        return self.reply


def test_drop_counter_is_optional() -> None:
    """Without ``SO_MEMINFO`` the overflow check is skipped, never guessed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for reply in (None, b"\x00" * 8):
        assert sockets.receive_drops(cast(IcmpSocket, OptionlessSocket(reply))) is None
