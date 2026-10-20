# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""TCP paths to an HTTPS server, direct or through an HTTP CONNECT proxy.

Production hosts often reach the internet only through an enterprise proxy.
The reachability test (FIELD-010) needs to say which hop failed, so this
module resolves and connects step by step, recording the DNS answer, the
TCP connect time, and the proxy's CONNECT status line separately. Proxies
come from ``--proxy`` or the standard ``HTTPS_PROXY``/``NO_PROXY``
variables, the same sources vakr itself uses (EGRESS-001).

Dependencies:
    socket, urllib: Standard library only.

Safety:
    Proxy credentials from the URL are sent only as ``Proxy-Authorization``
    to the proxy; every URL that is recorded goes through ``redact_url``.
    Proxy responses are read with a size bound.

Example:
    >>> redact_url("http://alice:s3cret@proxy.example.net:3128")
    'http://[REDACTED]@proxy.example.net:3128'
"""

import base64
import socket
import time
import urllib.parse
from collections.abc import Mapping
from typing import Any

MAX_HEAD_BYTES = 16 * 1024


def redact_url(url: str) -> str:
    """Hide any ``user:password@`` part of a URL.

    Args:
        url: URL that may carry credentials.

    Returns:
        str: The URL with its userinfo replaced by ``[REDACTED]``.

    Raises:
        None
    """
    parts = urllib.parse.urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urllib.parse.urlunsplit(parts._replace(netloc=f"[REDACTED]@{host}"))


def bypasses_proxy(host: str, no_proxy: str) -> bool:
    """Apply ``NO_PROXY`` the way curl and Python's urllib do.

    Args:
        host: Destination host name.
        no_proxy: Comma-separated suffixes; ``*`` matches every host.

    Returns:
        bool: ``True`` when ``host`` should be reached directly.

    Raises:
        None
    """
    host = host.lower().rstrip(".")
    for entry in (e.strip().lower().lstrip(".") for e in no_proxy.split(",")):
        if entry == "*" or (entry and (host == entry or host.endswith("." + entry))):
            return True
    return False


def choose_proxy(explicit: str | None, host: str, environ: Mapping[str, str]) -> str | None:
    """Pick the proxy for ``host``: ``--proxy``, else the environment.

    Args:
        explicit: Value of ``--proxy``, if given; it is used even for hosts
            listed in ``NO_PROXY``, because the operator asked for it.
        host: Destination host name.
        environ: Process environment.

    Returns:
        str | None: Proxy URL, or ``None`` for a direct connection.

    Raises:
        None
    """
    if explicit:
        return explicit
    proxy = environ.get("HTTPS_PROXY") or environ.get("https_proxy")
    no_proxy = environ.get("NO_PROXY") or environ.get("no_proxy") or ""
    if not proxy or bypasses_proxy(host, no_proxy):
        return None
    return proxy


def _resolve(host: str, port: int, record: dict[str, Any]) -> None:
    """Resolve ``host`` and record the addresses and the time taken.

    Args:
        host: Name to resolve.
        port: Destination port.
        record: Probe record to update.

    Returns:
        None

    Raises:
        OSError: If resolution fails.
    """
    start = time.monotonic()
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = sorted({str(info[4][0]) for info in infos})
    record["dns"] = {"host": host, "addresses": addresses, "seconds": _since(start)}


def _since(start: float) -> float:
    """Return seconds elapsed since ``start``, rounded for the manifest.

    Args:
        start: ``time.monotonic()`` value.

    Returns:
        float: Elapsed seconds, three decimals.

    Raises:
        None
    """
    return round(time.monotonic() - start, 3)


def _read_head(sock: socket.socket) -> bytes:
    """Read a proxy's response header block, bounded in size.

    Args:
        sock: Connected socket.

    Returns:
        bytes: Header bytes through the blank line.

    Raises:
        OSError: If the proxy closes early or sends an oversized header.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1024)
        if not chunk or len(data) > MAX_HEAD_BYTES:
            raise OSError("proxy closed the connection or sent an oversized header")
        data += chunk
    return data


def open_tunnel(
    host: str, port: int, proxy: str | None, timeout: float, record: dict[str, Any]
) -> socket.socket:
    """Open a TCP path to ``host:port``, directly or with HTTP CONNECT.

    Args:
        host: Destination host.
        port: Destination port.
        proxy: ``http://[user:pass@]proxy:port`` or ``None``.
        timeout: Socket timeout in seconds.
        record: Probe record that receives ``dns``, ``connect``, and
            ``proxy_connect`` entries.

    Returns:
        socket.socket: Connected socket ready for a TLS handshake.

    Raises:
        OSError: On resolution, connection, or proxy refusal.
    """
    target = urllib.parse.urlsplit(proxy) if proxy else None
    if target is not None and target.scheme not in ("http", ""):
        raise OSError(f"proxy scheme {target.scheme!r} is not supported; use http://")
    peer = (target.hostname or "", target.port or 80) if target else (host, port)
    _resolve(peer[0], peer[1], record)
    start = time.monotonic()
    sock = socket.create_connection(peer, timeout=timeout)
    record["connect"] = {"peer": f"{peer[0]}:{peer[1]}", "seconds": _since(start)}
    if target is None:
        return sock
    request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if target.username:
        pair = (
            f"{urllib.parse.unquote(target.username)}:{urllib.parse.unquote(target.password or '')}"
        )
        request += f"Proxy-Authorization: Basic {base64.b64encode(pair.encode()).decode()}\r\n"
    sock.sendall((request + "\r\n").encode("latin-1"))
    status = _read_head(sock).split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
    record["proxy_connect"] = {"status": status, "seconds": _since(start)}
    if status.split(" ")[1:2] != ["200"]:
        sock.close()
        raise OSError(f"proxy refused CONNECT: {status}")
    return sock
