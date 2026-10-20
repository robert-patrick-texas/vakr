# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Step-by-step HTTPS reachability test for the release host (FIELD-010).

Before vakr is installed, development needs to know whether the production
host can reach GitHub for self-update, and if not, where it breaks: DNS,
the proxy, the TCP connect, TLS verification, or HTTP. Many enterprises
re-sign outbound TLS with their own inspection CA; when verification fails,
the test reports the issuer and subject of the certificate the host was
shown, which tells development which CA to trust with ``vakr tls trust-ca``
(EGRESS-003, EGRESS-005). When only the strict X.509 profile that Python
3.13+ enforces rejects the chain, the report says so and names the
``--no-strict-x509`` flag and vakr's ``network.tls_strict_x509`` fallback
(EGRESS-007). Proxies come from ``--proxy`` or the standard environment
variables; the trust store is the OS bundle plus ``--ca-file``, never only
the interpreter's compiled-in paths (EGRESS-002).

Dependencies:
    ssl, http.client: Standard library only.
    fieldkit_der: Decodes the unverified certificate's names.
    fieldkit_tunnel: Resolution, connect, and proxy CONNECT steps.

Safety:
    Verification is never disabled for a request that carries data
    (EGRESS-004). The one exception is ``peek_presented_certificate_unverified``,
    which completes a handshake only to read the presented certificate and
    closes without sending application data. ``--no-strict-x509`` clears only
    ``VERIFY_X509_STRICT``; the strict-profile confirmation handshake keeps
    every other check and also sends nothing. Proxy credentials are used for
    ``Proxy-Authorization`` but every recorded URL is redacted.

Example:
    >>> OS_CA_BUNDLES[0]
    '/etc/ssl/certs/ca-certificates.crt'
"""

import hashlib
import http.client
import json
import ssl
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fieldkit_der import certificate_names
from fieldkit_tunnel import choose_proxy, open_tunnel, redact_url

OS_CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")
MAX_BODY_BYTES = 256 * 1024
STRICT_ONLY_CODES = frozenset({78, *range(80, 95)})
"""OpenSSL 3 verify codes raised only by the strict profile, e.g. 85 missing AKI, 92 no keyUsage."""
STRICT_HINT = (
    "the chain fails the strict X.509 profile that Python 3.13+ enforces; rerun with "
    "--no-strict-x509 to compare, and if the CA or proxy certificates cannot be reissued, "
    "vakr needs network.tls_strict_x509 = false (EGRESS-007)"
)


def tls_context(ca_file: Path | None, strict_x509: bool = True) -> tuple[ssl.SSLContext, list[str]]:
    """Build a verifying client context from the OS store plus ``ca_file``.

    Args:
        ca_file: Extra PEM CA file (enterprise inspection CA), or ``None``.
        strict_x509: Keep the strict X.509 profile; ``False`` clears only
            ``VERIFY_X509_STRICT`` (``--no-strict-x509``).

    Returns:
        tuple[ssl.SSLContext, list[str]]: Context with hostname checking and
        verification on in both modes, and the trust sources that were loaded.

    Raises:
        OSError: If ``ca_file`` cannot be read.
        ssl.SSLError: If ``ca_file`` holds no usable certificate.
    """
    context = ssl.create_default_context()
    if not strict_x509:
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    sources = ["interpreter default paths"]
    for bundle in OS_CA_BUNDLES:
        if Path(bundle).is_file():
            context.load_verify_locations(cafile=bundle)
            sources.append(bundle)
    if ca_file is not None:
        context.load_verify_locations(cafile=str(ca_file))
        sources.append(str(ca_file))
    return context, sources


def peek_presented_certificate_unverified(
    host: str, port: int, proxy: str | None, timeout: float
) -> dict[str, Any]:
    """Read the certificate a server presents, WITHOUT verifying it.

    This is the field kit's only unverified TLS handshake, and it exists
    for diagnosis alone: after a verification failure, the issuer of the
    presented certificate names the enterprise TLS-inspection CA that sits
    in the path (FIELD-010). The handshake completes, the certificate is
    read, and the connection is closed. No application data, request, or
    credential is ever sent over this connection.

    Args:
        host: Server name (also sent as SNI).
        port: Server port.
        proxy: Same proxy as the failed request, or ``None``.
        timeout: Socket timeout in seconds.

    Returns:
        dict[str, Any]: ``issuer``, ``subject``, validity, and ``sha256`` of
        the presented leaf certificate.

    Raises:
        OSError: If the connection or handshake fails.
        ValueError: If the certificate cannot be decoded.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    sock = open_tunnel(host, port, proxy, timeout, {})
    with context.wrap_socket(sock, server_hostname=host) as tls:
        der = tls.getpeercert(binary_form=True) or b""
    return {**certificate_names(der), "sha256": hashlib.sha256(der).hexdigest()}


def _flatten_name(name: Any) -> str:
    """Render ``getpeercert()`` subject/issuer tuples as ``CN=..., O=...``.

    Args:
        name: Tuple of RDN tuples from ``SSLSocket.getpeercert()``.

    Returns:
        str: Comma-separated attributes.

    Raises:
        None
    """
    keys = {"commonName": "CN", "organizationName": "O", "countryName": "C"}
    return ", ".join(f"{keys.get(k, k)}={v}" for rdn in name or () for k, v in rdn)


def _http(tls: ssl.SSLSocket, method: str, host: str, path: str, agent: str) -> dict[str, Any]:
    """Send one request on an established TLS connection and read the reply.

    Args:
        tls: Verified TLS socket.
        method: ``GET`` or ``HEAD``.
        host: Host header value.
        path: Request path and query.
        agent: User-Agent value (GitHub's API requires one).

    Returns:
        dict[str, Any]: Status, reason, and the release ``tag_name`` if the
        body is a GitHub release object.

    Raises:
        OSError: On a socket error.
        http.client.HTTPException: On a malformed response.
    """
    request = (
        f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {agent}\r\n"
        "Accept: */*\r\nConnection: close\r\n\r\n"
    )
    tls.sendall(request.encode("ascii"))
    response = http.client.HTTPResponse(tls, method=method)
    response.begin()
    result: dict[str, Any] = {"status": response.status, "reason": response.reason}
    if method == "GET":
        try:
            body = json.loads(response.read(MAX_BODY_BYTES))
        except ValueError:
            body = None
        if isinstance(body, dict) and "tag_name" in body:
            result["tag_name"] = body["tag_name"]
    response.close()
    return result


def probe(method: str, url: str, options: Mapping[str, Any]) -> dict[str, Any]:
    """Test one HTTPS URL step by step and record where it fails.

    Args:
        method: ``GET`` or ``HEAD``.
        url: ``https://`` URL to test.
        options: ``proxy`` (str or None), ``ca_file`` (Path or None),
            ``timeout`` (float), ``agent`` (str), ``environ`` (mapping), and
            ``strict_x509`` (bool, default ``True``).

    Returns:
        dict[str, Any]: ``dns``, ``connect``, ``tls``, ``http`` entries as
        far as the test got, plus ``error`` naming the failed stage.

    Raises:
        None
    """
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname or "", parts.port or 443
    proxy = choose_proxy(options.get("proxy"), host, options.get("environ", {}))
    record: dict[str, Any] = {"method": method, "url": url, "proxy": proxy and redact_url(proxy)}
    record["strict_x509"] = strict = options.get("strict_x509", True)
    stage = "tls-setup"
    try:
        context, record["trust"] = tls_context(options.get("ca_file"), strict)
        stage = "connect"
        sock = open_tunnel(host, port, proxy, options["timeout"], record)
        stage = "tls"
        with context.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert() or {}
            record["tls"] = {
                "verified": True,
                "version": tls.version(),
                "issuer": _flatten_name(cert.get("issuer")),
                "subject": _flatten_name(cert.get("subject")),
            }
            stage = "http"
            target = parts.path + (f"?{parts.query}" if parts.query else "")
            record["http"] = _http(tls, method, host, target or "/", options["agent"])
    except ssl.SSLCertVerificationError as exc:
        record["error"] = f"tls: certificate verification failed: {exc.verify_message}"
        presented = _peek_or_error(host, port, proxy, options["timeout"])
        record["tls"] = {"verified": False, "verify_code": exc.verify_code, "presented": presented}
        if strict and exc.verify_code in STRICT_ONLY_CODES:
            _strict_profile(record, host, port, proxy, options)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        record["error"] = f"{stage}: {type(exc).__name__}: {exc}"
    return record


def _peek_or_error(host: str, port: int, proxy: str | None, timeout: float) -> dict[str, Any]:
    """Run the diagnostic peek, turning its failure into a record entry.

    Args:
        host: Server name.
        port: Server port.
        proxy: Proxy URL or ``None``.
        timeout: Socket timeout in seconds.

    Returns:
        dict[str, Any]: Presented certificate details, or ``{"error": ...}``.

    Raises:
        None
    """
    try:
        return peek_presented_certificate_unverified(host, port, proxy, timeout)
    except (OSError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _strict_profile(
    record: dict[str, Any], host: str, port: int, proxy: str | None, options: Mapping[str, Any]
) -> None:
    """Confirm a strict-profile failure and add the fallback hint to the record.

    The confirmation is a verifying handshake with only ``VERIFY_X509_STRICT``
    cleared; it closes right after the handshake and sends nothing.

    Args:
        record: Probe record whose TLS stage failed with a strict-only code.
        host: Server name (SNI and host name check).
        port: Server port.
        proxy: Same proxy as the failed request, or ``None``.
        options: Probe options (``ca_file``, ``timeout``).

    Returns:
        None

    Raises:
        None
    """
    remaining: str | None = None
    try:
        context, _ = tls_context(options.get("ca_file"), strict_x509=False)
        sock = open_tunnel(host, port, proxy, options["timeout"], {})
        context.wrap_socket(sock, server_hostname=host).close()
    except ssl.SSLCertVerificationError as exc:
        remaining = exc.verify_message
    except (OSError, ssl.SSLError) as exc:
        remaining = f"confirmation failed: {type(exc).__name__}: {exc}"
    record["tls"]["passes_without_strict"] = remaining is None
    if remaining is None:
        record["error"] += f" (only the strict X.509 profile rejects it); hint: {STRICT_HINT}"
    else:
        record["error"] += f" (strict X.509 profile rule; without it: {remaining})"
