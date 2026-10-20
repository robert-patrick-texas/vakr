"""Open verified TLS streams directly or through an HTTP ``CONNECT`` proxy.

Each function is one step an operator can reason about when egress fails:
resolve names, connect TCP, ask the proxy for a tunnel, and complete a
verifying TLS handshake. The HTTP client and the step-by-step diagnosis both
call these, so what the diagnosis tests is exactly what updates use.

Dependencies:
    socket, ssl: Standard-library networking.
    vakr.lifecycle.egress.proxy: Proxy endpoint type.

Safety:
    Every socket operation has a timeout. Proxy replies are bounded in size
    and must not carry bytes beyond their headers, so a misbehaving proxy
    cannot inject data into the TLS stream. TLS always uses the caller's
    verifying context.

Example:
    >>> sock = open_stream("api.github.com", 443, None, 10.0)  # doctest: +SKIP
    >>> tls = tls_wrap(sock, context, "api.github.com")  # doctest: +SKIP
"""

import socket
import ssl
from typing import Any

from vakr.lifecycle.egress.errors import CertificateVerifyError, EgressError
from vakr.lifecycle.egress.proxy import ProxyEndpoint

type Address = tuple[socket.AddressFamily, tuple[Any, ...]]

MAX_PROXY_REPLY = 16 * 1024
"""Largest ``CONNECT`` reply header block accepted from a proxy, in bytes."""


def resolve(host: str, port: int) -> list[Address]:
    """Resolve ``host`` to TCP socket addresses (the DNS step).

    Args:
        host: Host name or IP literal.
        port: TCP port.

    Returns:
        list[Address]: ``(family, sockaddr)`` pairs in resolver order.

    Raises:
        EgressError: Step ``"dns"`` when resolution fails.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise EgressError("dns", f"cannot resolve {host}: {exc}") from exc
    return [(family, sockaddr) for family, _, _, _, sockaddr in infos]


def connect_tcp(addresses: list[Address], timeout: float) -> socket.socket:
    """Connect to the first reachable address (the TCP step).

    Args:
        addresses: Candidates from ``resolve``.
        timeout: Seconds allowed per connection attempt and for later I/O.

    Returns:
        socket.socket: Connected socket with ``timeout`` set.

    Raises:
        EgressError: Step ``"tcp"`` when every address fails.
    """
    last: OSError | None = None
    for family, sockaddr in addresses:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            sock.close()
            last = exc
            continue
        return sock
    shown = ", ".join(str(a[1][0]) for a in addresses) or "no addresses"
    raise EgressError("tcp", f"cannot connect to {shown}: {last}")


def proxy_connect(sock: socket.socket, host: str, port: int) -> str:
    """Ask an HTTP proxy for a tunnel to ``host:port`` (the CONNECT step).

    Args:
        sock: TCP connection to the proxy.
        host: Destination host.
        port: Destination port.

    Returns:
        str: The proxy's status line, for example ``"HTTP/1.1 200 OK"``.

    Raises:
        EgressError: Step ``"proxy"`` for I/O errors, an oversized or
            malformed reply, extra bytes after the reply, or a non-2xx status.
    """
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    request = f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n"
    try:
        sock.sendall(request.encode("ascii"))
        reply = _read_reply(sock)
    except OSError as exc:
        raise EgressError("proxy", f"CONNECT {authority} failed: {exc}") from exc
    status_line = reply.split(b"\r\n", 1)[0].decode("latin-1")
    fields = status_line.split(" ", 2)
    if len(fields) < 2 or not fields[0].startswith("HTTP/") or not fields[1].isdigit():
        raise EgressError("proxy", f"malformed CONNECT reply: {status_line[:80]!r}")
    if not fields[1].startswith("2"):
        raise EgressError("proxy", f"proxy refused CONNECT {authority}: {status_line[:120]}")
    return status_line


def _read_reply(sock: socket.socket) -> bytes:
    """Read a proxy reply up to and including the blank line after headers.

    Args:
        sock: Connection to the proxy.

    Returns:
        bytes: The header block.

    Raises:
        EgressError: Step ``"proxy"`` when the reply is too large, ends
            early, or is followed by unexpected bytes.
        OSError: On socket errors or timeouts.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_PROXY_REPLY:
            raise EgressError("proxy", "CONNECT reply too large")
        chunk = sock.recv(4096)
        if not chunk:
            raise EgressError("proxy", "proxy closed the connection during CONNECT")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    # The TLS client speaks first, so a correct proxy sends nothing more yet.
    if rest:
        raise EgressError("proxy", "unexpected data after CONNECT reply")
    return head


def open_stream(host: str, port: int, proxy: ProxyEndpoint | None, timeout: float) -> socket.socket:
    """Connect to ``host:port`` directly or through a ``CONNECT`` tunnel.

    Args:
        host: Destination host.
        port: Destination port.
        proxy: Proxy to tunnel through, or ``None`` for direct.
        timeout: Socket timeout in seconds.

    Returns:
        socket.socket: A byte stream to the destination, ready for TLS.

    Raises:
        EgressError: From the DNS, TCP, or proxy step.
    """
    first_hop = (proxy.host, proxy.port) if proxy else (host, port)
    sock = connect_tcp(resolve(*first_hop), timeout)
    if proxy is not None:
        try:
            proxy_connect(sock, host, port)
        except EgressError:
            sock.close()
            raise
    return sock


def tls_wrap(
    sock: socket.socket, context: ssl.SSLContext, host: str, port: int = 443
) -> ssl.SSLSocket:
    """Complete a verifying TLS handshake for ``host`` (the TLS step).

    Args:
        sock: Connected stream from ``open_stream``; closed on failure.
        context: Verifying context from ``truststore.build_context``.
        host: Server name for SNI and hostname verification.
        port: Destination port, used only in error messages.

    Returns:
        ssl.SSLSocket: Established TLS connection.

    Raises:
        CertificateVerifyError: When the chain or host name is not trusted.
        EgressError: Step ``"tls"`` for other handshake failures.
    """
    try:
        return context.wrap_socket(sock, server_hostname=host)
    except ssl.SSLCertVerificationError as exc:
        sock.close()
        reason = exc.verify_message or str(exc)
        raise CertificateVerifyError(host, port, reason, exc.verify_code) from exc
    except (ssl.SSLError, OSError) as exc:
        sock.close()
        raise EgressError("tls", f"handshake with {host}:{port} failed: {exc}") from exc
