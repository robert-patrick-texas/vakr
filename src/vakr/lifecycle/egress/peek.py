"""Read the certificate a server presented after verification already failed.

When an enterprise TLS-inspection proxy re-signs traffic with a CA this host
does not trust, the verifying handshake fails before Python exposes the
certificate. Telling the operator *which* issuer was presented is what turns
"certificate verify failed" into an actionable "install your organization's
CA with ``vakr tls trust-ca``" (EGRESS-005). Getting that certificate needs
one extra handshake that does not verify, and this module is the only place
in the code base allowed to do that (EGRESS-004).

Dependencies:
    ssl: A throwaway non-verifying client context.
    vakr.lifecycle.egress.transport: The same DNS, TCP, and proxy path the
        verifying client uses.

Safety:
    ``peek_presented_certificate`` is called only by the diagnosis, only
    after a verifying handshake to the same destination failed. It completes
    the handshake, copies the presented leaf certificate, and closes the
    connection. It never sends application data (no HTTP request, nothing
    written after the handshake), never returns the socket, and its result is
    only displayed, never trusted. The EGRESS-T04 static test allowlists this
    one function by name and rejects verification-off settings anywhere else
    under ``src/``.

Example:
    >>> der = peek_presented_certificate("api.github.com", 443, None, 10.0)  # doctest: +SKIP
"""

import ssl

from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.egress.proxy import ProxyEndpoint
from vakr.lifecycle.egress.transport import open_stream


def peek_presented_certificate(
    host: str, port: int, proxy: ProxyEndpoint | None, timeout: float
) -> bytes:
    """Return the DER leaf certificate presented by ``host:port``, unverified.

    Diagnosis only: the caller displays the subject and issuer and must not
    use the result to decide trust.

    Args:
        host: Server name, sent as SNI so the same certificate is presented.
        port: Server port.
        proxy: Proxy to tunnel through, exactly as the failed attempt did.
        timeout: Socket timeout in seconds.

    Returns:
        bytes: DER-encoded certificate.

    Raises:
        EgressError: From the DNS, TCP, or proxy step, step ``"tls"`` when
            the handshake fails, or when no certificate was presented.
    """
    stream = open_stream(host, port, proxy, timeout)
    peek_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    peek_context.check_hostname = False
    peek_context.verify_mode = ssl.CERT_NONE
    try:
        with peek_context.wrap_socket(stream, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    except (ssl.SSLError, OSError) as exc:
        stream.close()
        raise EgressError("tls", f"handshake with {host}:{port} failed: {exc}") from exc
    if not der:
        raise EgressError("tls", f"{host}:{port} presented no certificate")
    return der
