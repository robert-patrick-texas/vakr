"""The API's two listening sockets, opened before readiness (16.3: bind addresses and ports).

- TCP on ``api.listen_address:api.port`` (``127.0.0.1:8080``), plain HTTP
  for the reverse proxy on the same host (SEC-013). An empty address turns
  it off.
- The Unix socket ``api.socket_path`` (``/run/vakr/api.sock``), mode 0666:
  anyone may connect, and each connection's account decides its role
  (E.12), as PostgreSQL's peer authentication does. ``/run/vakr`` is 0751,
  so other accounts can reach the socket by name but cannot list the
  directory.

Both are opened during the startup checks, so a port in use or a missing
run directory stops the service with exit 78 and a sentence that says
what to fix, before systemd is told it is ready. A socket file left behind
by a crash is removed when nothing answers on it; one that answers means
another ``vakr-api`` is running, and this one refuses to start.

Dependencies:
    socket, stat: Sockets and file checks.
    vakr.app.collector.preflight: ``PreflightError``.

Safety:
    Never removes anything but a stale socket file. Blocking: call in a
    worker thread.

Example:
    >>> Listeners(None, None).sockets()
    []
"""

import contextlib
import socket
import stat
from dataclasses import dataclass
from pathlib import Path

from vakr.api.settings import BACKLOG, SOCKET_MODE, ApiSettings
from vakr.app.collector.preflight import PreflightError

STALE_PROBE_S = 0.5
"""How long a probe of an existing socket file waits for an answer."""


@dataclass(slots=True)
class Listeners:
    """The listening sockets.

    Attributes:
        tcp: The TCP listener, or ``None`` when ``api.listen_address`` is empty.
        unix: The Unix-socket listener, or ``None`` (tests only).
        unix_path: Where the Unix socket lives, for removal at stop.
    """

    tcp: socket.socket | None
    unix: socket.socket | None
    unix_path: Path | None = None

    def sockets(self) -> list[socket.socket]:
        """Return the open sockets.

        Args:
            None

        Returns:
            list[socket.socket]: TCP first.

        Raises:
            None
        """
        return [s for s in (self.tcp, self.unix) if s is not None]

    def close(self) -> None:
        """Close the sockets and remove the socket file.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for sock in self.sockets():
            sock.close()
        if self.unix_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self.unix_path.unlink()


def open_tcp(address: str, port: int) -> socket.socket:
    """Listen on an IPv4 or IPv6 address.

    Args:
        address: IP literal.
        port: TCP port.

    Returns:
        socket.socket: Listening.

    Raises:
        PreflightError: If the address cannot be bound.
    """
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((address, port))
        sock.listen(BACKLOG)
    except OSError as exc:
        sock.close()
        raise PreflightError("listen", f"cannot listen on {address} port {port} ({exc.strerror}); "
                             "another vakr-api, or another program, may hold it; see "
                             "api.listen_address and api.port") from None  # fmt: skip
    return sock


def _clear_stale(path: Path) -> None:
    """Remove a socket file nothing answers on; refuse anything else.

    Args:
        path: The socket path.

    Returns:
        None

    Raises:
        PreflightError: If it is not a socket, or another server answers on it.
    """
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise PreflightError("socket", f"{path} exists and is not a socket; remove it")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(STALE_PROBE_S)
    try:
        probe.connect(str(path))
    except OSError:
        path.unlink(missing_ok=True)  # left behind by a crash: nothing answers
        return
    finally:
        probe.close()
    raise PreflightError("socket", f"another vakr-api is answering on {path}; stop it first")


def open_unix(path: Path) -> socket.socket:
    """Listen on the local Unix socket.

    Args:
        path: The socket path.

    Returns:
        socket.socket: Listening, mode ``SOCKET_MODE``.

    Raises:
        PreflightError: If the directory is missing or the socket cannot be created.
    """
    if not path.parent.is_dir():
        raise PreflightError("socket", f"{path.parent} does not exist; run "
                             "`systemd-tmpfiles --create vakr.conf`")  # fmt: skip
    _clear_stale(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
        path.chmod(SOCKET_MODE)  # after bind: the umask made it 0750
        sock.listen(BACKLOG)
    except OSError as exc:
        sock.close()
        raise PreflightError("socket", f"cannot create {path} ({exc.strerror}); "
                             f"{path.parent} must be writable by vakr") from None  # fmt: skip
    return sock


def open_listeners(settings: ApiSettings) -> Listeners:
    """Open both listeners (blocking).

    Args:
        settings: ``[api]`` settings.

    Returns:
        Listeners: The sockets.

    Raises:
        PreflightError: If either cannot be opened; nothing stays open.
    """
    tcp = open_tcp(settings.listen_address, settings.port) if settings.listen_address else None
    try:
        unix = open_unix(settings.socket_path)
    except PreflightError:
        if tcp is not None:
            tcp.close()
        raise
    return Listeners(tcp, unix, settings.socket_path)
