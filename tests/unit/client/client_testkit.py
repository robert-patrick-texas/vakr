"""Sockets for client tests: the real API on a Unix socket, and broken servers (13.4, E.29).

``short_dir`` gives a socket directory short enough for ``sun_path`` (108
bytes), which pytest's own temporary paths can exceed. ``api_socket``
serves the real application with ``PeerProtocol``: the test process is an
operator by its own GID, or refused. ``raw_server`` answers every
connection with a hand-written reply, for the failures a real ``vakr-api``
should never produce: silence, a trickle, garbage, an oversized body.

Dependencies:
    socket, tempfile, threading; tests.unit.api.api_testkit.

Example:
    >>> http(200, b"{}").startswith(b"HTTP/1.1 200 OK")
    True
"""

import os
import socket
import tempfile
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from tests.unit.api.api_testkit import serving
from vakr.api.context import account_name
from vakr.api.peer import peer_protocol
from vakr.app.api.listeners import open_unix
from vakr.auth import LocalGroups
from vakr.client.status import SocketStatusSource

ROOT = os.geteuid() == 0
"""Root is admin on the socket by rule, so refusal tests cannot run as root."""

type Reply = Callable[[socket.socket, threading.Event], None]


@contextmanager
def short_dir() -> Generator[Path]:
    """Make a short-named temporary directory for sockets; removed afterwards.

    Args:
        None

    Returns:
        Generator[Path]: The directory.

    Raises:
        None
    """
    with tempfile.TemporaryDirectory(prefix="vakr-") as name:
        yield Path(name)


@contextmanager
def api_socket(app: Any, path: Path, *, refuse: bool = False) -> Generator[SocketStatusSource]:
    """Serve ``app`` on a Unix socket, as ``vakr-api`` does, and give a client of it.

    Args:
        app: The ASGI application.
        path: The socket file.
        refuse: Map no group, so the test account is refused (403 ``peer_refused``).

    Returns:
        Generator[SocketStatusSource]: A client of the socket.

    Raises:
        None
    """
    groups = LocalGroups(None, None) if refuse else LocalGroups(None, os.getgid())
    with serving(app, open_unix(path), peer_protocol(groups, account_name)):
        yield SocketStatusSource(str(path))


def http(status: int, body: bytes, *headers: str) -> bytes:
    """Build a raw HTTP/1.1 answer with a ``Content-Length``.

    Args:
        status: The status code.
        body: The body.
        *headers: Extra ``Name: value`` lines; a ``Content-Length`` here replaces the real one.

    Returns:
        bytes: The answer.

    Raises:
        None
    """
    lines = [f"HTTP/1.1 {status} {'OK' if status == 200 else 'Other'}", *headers]
    if not any(h.lower().startswith(("content-length", "transfer-encoding")) for h in headers):
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def read_request(conn: socket.socket) -> bytes:
    """Read a request's head.

    Args:
        conn: The accepted connection.

    Returns:
        bytes: What arrived up to the blank line.

    Raises:
        OSError: If the connection fails.
    """
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def canned(answer: bytes) -> Reply:
    """Reply with fixed bytes, then close.

    Args:
        answer: The raw answer.

    Returns:
        Reply: The handler.

    Raises:
        None
    """

    def reply(conn: socket.socket, stop: threading.Event) -> None:
        """Send the answer.

        Args:
            conn: The connection.
            stop: Unused; the reply ends at once.

        Returns:
            None

        Raises:
            OSError: If the client went away.
        """
        del stop
        read_request(conn)
        conn.sendall(answer)

    return reply


def silent(conn: socket.socket, stop: threading.Event) -> None:
    """Read the request and never answer, until the test ends.

    Args:
        conn: The connection.
        stop: Set when the server stops.

    Returns:
        None

    Raises:
        OSError: If the client went away.
    """
    read_request(conn)
    stop.wait(10)


def trickle(conn: socket.socket, stop: threading.Event) -> None:
    """Answer one header byte every 50 ms, forever: each read succeeds, the whole never ends.

    Args:
        conn: The connection.
        stop: Set when the server stops.

    Returns:
        None

    Raises:
        OSError: When the client cuts the connection.
    """
    read_request(conn)
    conn.sendall(b"HTTP/1.1 200 OK\r\n")
    while not stop.wait(0.05):
        conn.sendall(b"X")


@contextmanager
def raw_server(path: Path, reply: Reply) -> Generator[None]:
    """Answer every connection on ``path`` with ``reply``, in a background thread.

    Args:
        path: The socket file.
        reply: What to do with each connection.

    Returns:
        Generator[None]: While serving.

    Raises:
        None
    """
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(4)
    listener.settimeout(0.1)
    stop = threading.Event()

    def loop() -> None:
        """Accept and reply until stopped.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            with conn, suppress(OSError):
                reply(conn, stop)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(5)
        listener.close()
