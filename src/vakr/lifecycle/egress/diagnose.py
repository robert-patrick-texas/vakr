"""Step-by-step egress diagnosis: DNS, TCP or proxy CONNECT, TLS, HTTP status.

"Update check failed" is not actionable on a locked-down enterprise network.
This module repeats what the HTTPS client does, one step at a time, and
reports the first step that breaks. When TLS verification fails it reads the
certificate the server presented and names its subject and issuer, which
reveals a TLS-inspection proxy and points the operator at the command that
trusts its CA (EGRESS-005).

Dependencies:
    vakr.lifecycle.egress.transport: The same steps the client uses.
    vakr.lifecycle.egress.peek: Presented certificate after a failure.
    vakr.lifecycle.egress.certinfo: Subject and issuer names.

Safety:
    Sends only a ``HEAD`` request after a verified handshake. The presented
    certificate is displayed, never trusted. Output contains host names,
    addresses, and certificate names only.

Example:
    >>> for line in render(diagnose("https://api.github.com/", settings)):  # doctest: +SKIP
    ...     print(line)
    [ok  ] dns: api.github.com -> 140.82.112.6
"""

import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from vakr.lifecycle.egress.certinfo import CertificateRejectedError, summarize_der
from vakr.lifecycle.egress.client import EgressSettings
from vakr.lifecycle.egress.errors import CertificateVerifyError, EgressError
from vakr.lifecycle.egress.peek import peek_presented_certificate
from vakr.lifecycle.egress.proxy import ProxyEndpoint
from vakr.lifecycle.egress.transport import connect_tcp, proxy_connect, resolve, tls_wrap

DEFAULT_HINT = "add the issuing CA to the trusted CA directory"
UNTRUSTED_ISSUER_MARKERS = ("issuer", "self-signed", "self signed")
"""OpenSSL reasons meaning the chain ends at a CA this host does not trust."""


@dataclass(frozen=True, slots=True)
class Step:
    """Result of one diagnosis step.

    Attributes:
        name: ``"dns"``, ``"tcp"``, ``"proxy"``, ``"tls"``, or ``"http"``.
        ok: Whether the step succeeded.
        detail: What was observed.
    """

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """All steps run for one URL, stopping at the first failure.

    Attributes:
        url: The URL diagnosed.
        steps: Steps in execution order.
        hint: Suggested remedy, when one is known.
    """

    url: str
    steps: tuple[Step, ...]
    hint: str | None = None

    @property
    def ok(self) -> bool:
        """Whether every step succeeded.

        Args:
            None

        Returns:
            bool: ``True`` when no step failed.

        Raises:
            None
        """
        return all(step.ok for step in self.steps)


def diagnose(url: str, settings: EgressSettings, trust_hint: str = DEFAULT_HINT) -> Diagnosis:
    """Run each egress step for ``url`` and report where it fails.

    Args:
        url: HTTPS URL to test, for example ``https://api.github.com/``.
        settings: The same settings the real client uses.
        trust_hint: Remedy suggested when an untrusted issuer is presented,
            for example ``"run: vakr tls trust-ca FILE"``.

    Returns:
        Diagnosis: Steps up to and including the first failure.

    Raises:
        None
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return Diagnosis(url, (Step("config", False, f"not an HTTPS URL: {url!r}"),))
    host, port = parts.hostname, parts.port or 443
    proxy = settings.proxies.proxy_for(host, port)
    steps: list[Step] = []
    try:
        sock = _connect_steps(host, port, proxy, settings.timeout, steps)
        tls = _tls_step(sock, host, port, settings, steps)
    except CertificateVerifyError as exc:
        steps.append(_presented_step(exc, proxy, settings.timeout))
        # Expiry or a host-name mismatch is not fixed by trusting another CA.
        untrusted = any(marker in exc.reason for marker in UNTRUSTED_ISSUER_MARKERS)
        return Diagnosis(url, tuple(steps), trust_hint if untrusted else None)
    except EgressError as exc:
        steps.append(Step(exc.step, False, str(exc).partition(": ")[2]))
        return Diagnosis(url, tuple(steps))
    steps.append(_http_step(tls, host, parts.path or "/", settings.user_agent))
    return Diagnosis(url, tuple(steps))


def _connect_steps(
    host: str, port: int, proxy: ProxyEndpoint | None, timeout: float, steps: list[Step]
) -> socket.socket:
    """Run DNS, TCP, and (when proxied) CONNECT, appending successful steps.

    Args:
        host: Destination host.
        port: Destination port.
        proxy: Proxy for this destination, or ``None``.
        timeout: Socket timeout in seconds.
        steps: Accumulator for step results.

    Returns:
        socket.socket: Stream to the destination.

    Raises:
        EgressError: From the failing step.
    """
    first_host, first_port = (proxy.host, proxy.port) if proxy else (host, port)
    addresses = resolve(first_host, first_port)
    shown = ", ".join(sorted({str(a[1][0]) for a in addresses}))
    via = f" (proxy {proxy}; the proxy resolves {host})" if proxy else ""
    steps.append(Step("dns", True, f"{first_host} -> {shown}{via}"))
    started = time.monotonic()
    sock = connect_tcp(addresses, timeout)
    elapsed = (time.monotonic() - started) * 1000
    steps.append(Step("tcp", True, f"connected to {first_host}:{first_port} in {elapsed:.0f} ms"))
    if proxy is not None:
        try:
            status = proxy_connect(sock, host, port)
        except EgressError:
            sock.close()
            raise
        steps.append(Step("proxy", True, f"CONNECT {host}:{port} -> {status}"))
    return sock


def _tls_step(
    sock: socket.socket, host: str, port: int, settings: EgressSettings, steps: list[Step]
) -> ssl.SSLSocket:
    """Run the verifying handshake and record the trusted issuer on success.

    Args:
        sock: Stream to the destination.
        host: Server name.
        port: Server port.
        settings: Egress settings with the verifying context.
        steps: Accumulator for step results.

    Returns:
        ssl.SSLSocket: The verified connection.

    Raises:
        CertificateVerifyError: When verification fails.
        EgressError: For other handshake failures.
    """
    tls = tls_wrap(sock, settings.context, host, port)
    issuer = _issuer_of(tls.getpeercert(binary_form=True))
    steps.append(Step("tls", True, f"{tls.version()} verified; issuer {issuer}"))
    return tls


def _presented_step(
    exc: CertificateVerifyError, proxy: ProxyEndpoint | None, timeout: float
) -> Step:
    """Describe a failed verification, naming the certificate the server presented.

    Args:
        exc: The verification failure.
        proxy: Proxy used for the failed attempt.
        timeout: Socket timeout for the peek handshake.

    Returns:
        Step: Failed ``tls`` step naming subject and issuer when obtainable.

    Raises:
        None
    """
    detail = f"certificate verify failed: {exc.reason}"
    try:
        summary = summarize_der(peek_presented_certificate(exc.host, exc.port, proxy, timeout))
    except (EgressError, CertificateRejectedError) as peek_exc:
        return Step("tls", False, f"{detail}; presented certificate unavailable ({peek_exc})")
    return Step(
        "tls",
        False,
        f"{detail}; server presented subject {summary.subject!r} "
        f"issued by {summary.issuer!r} (SHA-256 {summary.sha256})",
    )


def _http_step(tls: ssl.SSLSocket, host: str, path: str, user_agent: str) -> Step:
    """Send one ``HEAD`` request and report the HTTP status line.

    Args:
        tls: Verified connection; closed afterwards.
        host: ``Host`` header value.
        path: Request path.
        user_agent: ``User-Agent`` header value.

    Returns:
        Step: ``http`` step; any HTTP status means egress works.

    Raises:
        None
    """
    request = (
        f"HEAD {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {user_agent}\r\n"
        "Connection: close\r\n\r\n"
    )
    try:
        with tls, tls.makefile("rb") as reply:
            tls.sendall(request.encode("ascii"))
            line = reply.readline(1024).decode("latin-1").strip()
    except OSError as exc:
        return Step("http", False, f"no HTTP response: {exc}")
    fields = line.split(" ", 2)
    if len(fields) < 2 or not fields[1].isdigit():
        return Step("http", False, f"malformed HTTP response {line[:80]!r}")
    return Step("http", True, f"HTTP {fields[1]} from {host}")


def _issuer_of(der: bytes | None) -> str:
    """Return the issuer name of a DER certificate for display.

    Args:
        der: Certificate bytes, or ``None``.

    Returns:
        str: RFC 4514 issuer, or ``"unknown"``.

    Raises:
        None
    """
    if not der:
        return "unknown"
    try:
        return repr(summarize_der(der).issuer)
    except CertificateRejectedError:
        return "unknown"


def render(diagnosis: Diagnosis) -> list[str]:
    """Format a diagnosis as lines for a terminal or ticket.

    Args:
        diagnosis: Result of ``diagnose``.

    Returns:
        list[str]: Header, one line per step, and the hint when present.

    Raises:
        None
    """
    verdict = "ok" if diagnosis.ok else "FAILED"
    lines = [f"egress {diagnosis.url}: {verdict}"]
    lines += [f"  [{'ok' if s.ok else 'FAIL':<4}] {s.name}: {s.detail}" for s in diagnosis.steps]
    if diagnosis.hint:
        lines.append("  hint: if that issuer is your organization's TLS-inspection CA,")
        lines.append(f"        {diagnosis.hint}")
    return lines
