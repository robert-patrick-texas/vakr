"""Exceptions for outbound HTTPS, each tied to the step that failed.

An operator debugging egress through a firewall and an inspecting proxy needs
to know *where* a connection broke: name resolution, the TCP connect, the
proxy's CONNECT reply, the TLS handshake, or the HTTP exchange. Every error
raised by this package carries that step name so callers can print it and
run the step-by-step diagnosis (EGRESS-005) when appropriate.

Dependencies:
    None.

Safety:
    Messages contain host names, ports, and status codes only. Proxy URLs
    never carry credentials (``proxy`` rejects them), so messages are safe to
    log and paste into tickets.

Example:
    >>> str(EgressError("dns", "api.github.com: name not known"))
    'dns: api.github.com: name not known'
"""


class EgressError(Exception):
    """Outbound HTTPS failed at a named step.

    Attributes:
        step: One of ``"config"``, ``"dns"``, ``"tcp"``, ``"proxy"``,
            ``"tls"``, or ``"http"``.
    """

    def __init__(self, step: str, message: str) -> None:
        """Record the failing step with an operator-readable message.

        Args:
            step: Name of the step that failed.
            message: What went wrong, without secrets.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"{step}: {message}")
        self.step = step


class CertificateVerifyError(EgressError):
    """The server's certificate chain or host name failed verification.

    Attributes:
        host: Host name the client expected the certificate to match.
        port: TCP port of the TLS server.
        reason: OpenSSL's verification message, for example
            ``"unable to get local issuer certificate"``.
    """

    def __init__(self, host: str, port: int, reason: str) -> None:
        """Describe a verification failure for ``host:port``.

        Args:
            host: Expected server name.
            port: Server port.
            reason: Verification failure reason from OpenSSL.

        Returns:
            None

        Raises:
            None
        """
        super().__init__("tls", f"certificate verify failed for {host}:{port}: {reason}")
        self.host = host
        self.port = port
        self.reason = reason


class ResponseTooLargeError(EgressError):
    """A response body or download exceeded its configured size bound."""

    def __init__(self, url: str, limit: int) -> None:
        """Describe the oversize response.

        Args:
            url: URL whose body was too large.
            limit: Maximum permitted size in bytes.

        Returns:
            None

        Raises:
            None
        """
        super().__init__("http", f"{url}: response exceeds {limit} bytes")
        self.limit = limit
