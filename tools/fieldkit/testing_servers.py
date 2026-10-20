# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Loopback stand-ins for GitHub and an enterprise proxy, for field kit tests.

The reachability test (FIELD-010) must report DNS, connect, proxy, TLS, and
HTTP outcomes, including the issuer of a certificate it could not verify.
These servers let tests exercise each path without the internet: a TLS
server for ``localhost`` that answers like the GitHub release API, and an
HTTP CONNECT proxy that insists on Basic credentials.

Dependencies:
    socketserver, ssl: Standard library only.

Safety:
    Both bind to 127.0.0.1 on ephemeral ports; the proxy only tunnels to
    127.0.0.1, so it can never relay traffic off the host.

Example:
    >>> TLS_BODY.startswith(b"{")
    True
"""

import base64
import socket
import socketserver
import ssl
import threading
from typing import cast

TLS_BODY = b'{"tag_name": "v9.9.9", "name": "test release"}'
PROXY_CREDENTIALS = b"proxyuser:proxy pass"


class TlsServer(socketserver.ThreadingTCPServer):
    """Threading TCP server that carries the TLS context for its handler.

    Attributes:
        context: Server-side TLS context.
    """

    daemon_threads = True

    def __init__(self, context: ssl.SSLContext) -> None:
        """Bind to an ephemeral loopback port.

        Args:
            context: Server-side TLS context.

        Returns:
            None

        Raises:
            OSError: If binding fails.
        """
        super().__init__(("127.0.0.1", 0), _TlsHandler)
        self.context = context


class _TlsHandler(socketserver.BaseRequestHandler):
    """Answer one HTTP request over TLS with a small JSON body."""

    def handle(self) -> None:
        """Serve one connection; tolerate clients that only handshake.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        context = cast("TlsServer", self.server).context
        try:
            with context.wrap_socket(self.request, server_side=True) as tls:
                head = tls.recv(65536)
                if not head:
                    return
                status = b"200 OK" if b" /missing" not in head else b"404 Not Found"
                body = b"" if head.startswith(b"HEAD") else TLS_BODY
                tls.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                            b"Content-Length: " + str(len(TLS_BODY)).encode() +
                            b"\r\nConnection: close\r\n\r\n" + body)  # fmt: skip
        except ssl.SSLError, OSError:
            return  # the diagnostic peek closes right after the handshake


class ProxyServer(socketserver.ThreadingTCPServer):
    """Threading TCP server that records the CONNECT request heads it saw.

    Attributes:
        seen: Raw request heads, in arrival order.
    """

    daemon_threads = True

    def __init__(self) -> None:
        """Bind to an ephemeral loopback port.

        Args:
            None

        Returns:
            None

        Raises:
            OSError: If binding fails.
        """
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.seen: list[bytes] = []


class _ProxyHandler(socketserver.BaseRequestHandler):
    """Minimal HTTP CONNECT proxy that requires Basic credentials."""

    def handle(self) -> None:
        """Tunnel one CONNECT request to 127.0.0.1, or refuse it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            head += chunk
        cast("ProxyServer", self.server).seen.append(head)
        expected = base64.b64encode(PROXY_CREDENTIALS).decode()
        if f"Proxy-Authorization: Basic {expected}".encode() not in head:
            self.request.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            return
        port = int(head.split(b" ")[1].rsplit(b":", 1)[1])
        upstream = socket.create_connection(("127.0.0.1", port))
        self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _pump(self.request, upstream)


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes both ways until either side closes.

    Args:
        a: Client socket.
        b: Upstream socket.

    Returns:
        None

    Raises:
        None
    """

    def copy(src: socket.socket, dst: socket.socket) -> None:
        """Copy one direction.

        Args:
            src: Source socket.
            dst: Destination socket.

        Returns:
            None

        Raises:
            None
        """
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            dst.close()

    other = threading.Thread(target=copy, args=(b, a), daemon=True)
    other.start()
    copy(a, b)
    other.join(timeout=5)
