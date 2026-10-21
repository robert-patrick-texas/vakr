"""One bounded HTTP/1.1 ``GET`` to ``vakr-api`` over its Unix socket (13.1, E.29).

``get`` connects, sends one request, reads the answer, and closes, within
three bounds from Appendix E.29: ``CONNECT_TIMEOUT_S`` to connect,
``REQUEST_TIMEOUT_S`` for the whole exchange, and ``MAX_ANSWER_BYTES`` for
the body. The whole-exchange bound is a watchdog that shuts the socket
down, so a server that trickles bytes cannot stretch it the way a
per-read timeout would allow. There are no retries: ``vakr status`` is a
monitoring check that must answer promptly, and the TUI's next refresh is
its retry.

Every failure becomes ``SourceUnavailableError`` with what happened and
what to run (13.4): ``systemctl status vakr-api``, ``vakr doctor``, or
``--local`` to read the databases directly.

Dependencies:
    contextlib, http.client, socket, threading, time: The exchange and its
        bounds; standard library only.
    vakr.query.source: ``SourceUnavailableError``.

Safety:
    Sends no credentials: the socket authorizes by account (E.12). Messages
    name the socket and the kind of failure, never an answer's contents.

Example:
    >>> Limits().max_bytes == MAX_ANSWER_BYTES
    True
"""

import contextlib
import http.client
import socket
import threading
import time
from dataclasses import dataclass
from types import TracebackType

from vakr.query.source import SourceUnavailableError

CONNECT_TIMEOUT_S = 2.0
"""Seconds to connect to the socket (E.29)."""
REQUEST_TIMEOUT_S = 30.0
"""Seconds for one whole request and answer (E.29)."""
MAX_ANSWER_BYTES = 16 * 1024 * 1024
"""Largest answer body read; a 2,000-interface device screen is about 1.9 MB (E.29)."""
_CHUNK = 65536
HINT = ("check `systemctl status vakr-api` and `vakr doctor`, "
        "or add --local to read the databases directly")  # fmt: skip


@dataclass(frozen=True, slots=True)
class Limits:
    """The bounds of one exchange (Appendix E.29).

    Attributes:
        connect_s: Seconds to connect.
        request_s: Seconds for the whole exchange.
        max_bytes: Largest body read.
    """

    connect_s: float = CONNECT_TIMEOUT_S
    request_s: float = REQUEST_TIMEOUT_S
    max_bytes: int = MAX_ANSWER_BYTES


@dataclass(frozen=True, slots=True)
class Answer:
    """What the API sent back.

    Attributes:
        status: HTTP status.
        body: The body, at most ``Limits.max_bytes``.
        elapsed_s: Seconds the exchange took.
    """

    status: int
    body: bytes
    elapsed_s: float


class UnixHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` to a Unix socket instead of a TCP port."""

    def __init__(self, path: str, timeout: float) -> None:
        """Prepare a connection.

        Args:
            path: The socket file.
            timeout: Seconds for connect and each read.

        Returns:
            None

        Raises:
            None
        """
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        """Connect to the socket.

        Args:
            None

        Returns:
            None

        Raises:
            OSError: If the socket refuses, is missing, or times out.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class _Watchdog:
    """Shuts a connection's socket down when the whole-exchange bound passes."""

    def __init__(self, connection: UnixHTTPConnection, seconds: float) -> None:
        """Arm nothing yet; ``with`` starts and stops the timer.

        Args:
            connection: The connection to cut.
            seconds: The bound.

        Returns:
            None

        Raises:
            None
        """
        self.fired = False
        self._connection = connection
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        """Cut the socket so a blocked read returns at once.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.fired = True
        sock = self._connection.sock
        if sock is not None:
            with contextlib.suppress(OSError):  # already closed: the exchange ended as it fired
                sock.shutdown(socket.SHUT_RDWR)

    def __enter__(self) -> _Watchdog:
        """Start the timer.

        Args:
            None

        Returns:
            _Watchdog: Itself.

        Raises:
            None
        """
        self._timer.start()
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """Stop the timer.

        Args:
            kind: Exception type, if any.
            exc: Exception, if any.
            tb: Traceback, if any.

        Returns:
            None

        Raises:
            None
        """
        self._timer.cancel()


def _read(response: http.client.HTTPResponse, limit: int, path: str) -> bytes:
    """Read a body of at most ``limit`` bytes.

    Args:
        response: The answer.
        limit: Largest body accepted.
        path: The socket, for the message.

    Returns:
        bytes: The body.

    Raises:
        SourceUnavailableError: If the body is larger.
        OSError, http.client.HTTPException: If the transfer fails.
    """
    large = SourceUnavailableError(
        f"vakr-api sent an answer larger than {limit} bytes on {path} (Appendix E.29); "
        "narrow the request with --limit or --search"
    )
    length = response.getheader("Content-Length") or ""
    if length.isdigit() and int(length) > limit:
        raise large
    chunks: list[bytes] = []
    size = 0
    while chunk := response.read1(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise large
        chunks.append(chunk)
    return b"".join(chunks)


def explain(exc: BaseException, path: str, limits: Limits, timed_out: bool) -> str:
    """Say in operator words why an exchange failed, and what to run.

    Args:
        exc: What the connection raised.
        path: The socket.
        limits: The bounds, for the timeout message.
        timed_out: True when the watchdog cut the exchange.

    Returns:
        str: The message, ending with what to check.

    Raises:
        None
    """
    if timed_out:
        what = f"vakr-api did not answer on {path} within {limits.request_s:g} s"
    elif isinstance(exc, TimeoutError):  # reads wait longer than the watchdog, so only connect
        what = f"vakr-api did not accept a connection on {path} within {limits.connect_s:g} s"
    elif isinstance(exc, FileNotFoundError):
        what = f"vakr-api is not running: {path} is gone"
    elif isinstance(exc, ConnectionRefusedError):
        what = f"nothing answers on {path}: vakr-api stopped uncleanly or is still starting"
    elif isinstance(exc, PermissionError):
        what = f"permission denied on {path}; run as root or as a member of the vakr group"
    else:
        what = f"vakr-api gave no usable answer on {path} ({type(exc).__name__})"
    return f"{what}; {HINT}"


def get(path: str, target: str, limits: Limits | None = None) -> Answer:
    """Send one ``GET`` and return the answer, within the E.29 bounds.

    Args:
        path: The socket file.
        target: Path and query, such as ``/api/v1/devices?limit=100``.
        limits: The bounds; Appendix E.29's when ``None``.

    Returns:
        Answer: Status, body, and seconds taken; any status.

    Raises:
        SourceUnavailableError: If the socket cannot be reached, nothing
            answers in time, the answer is too large, or the transfer fails.
    """
    limits = limits or Limits()
    started = time.monotonic()
    connection = UnixHTTPConnection(path, limits.connect_s)
    watchdog = _Watchdog(connection, limits.request_s)
    try:
        with watchdog:
            connection.connect()
            if connection.sock is not None:  # the watchdog, not a read timeout, ends a slow answer
                connection.sock.settimeout(limits.request_s + 1)
            connection.request("GET", target, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = _read(response, limits.max_bytes, path)
    except (OSError, http.client.HTTPException) as exc:
        raise SourceUnavailableError(explain(exc, path, limits, watchdog.fired)) from None
    finally:
        connection.close()
    if watchdog.fired:  # the cut can look like a clean end of a short body
        raise SourceUnavailableError(explain(TimeoutError(), path, limits, True))
    return Answer(response.status, body, time.monotonic() - started)
