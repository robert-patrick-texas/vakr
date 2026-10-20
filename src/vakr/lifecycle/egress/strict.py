"""Recognize TLS handshakes that fail only the strict X.509 profile (EGRESS-005, EGRESS-007).

Python 3.13+ asks OpenSSL to enforce a strict certificate profile
(``VERIFY_X509_STRICT``). Enterprise TLS-inspection proxies often mint
leaf certificates without an authority key identifier, or run from a CA
without ``keyUsage``, so a handshake fails even though the operator trusts
the CA. The OpenSSL verify code says that a strict-profile rule fired, and
one handshake with only that profile relaxed shows whether it was the only
problem. The diagnosis then names the fallback instead of suggesting that
the operator trust yet another CA.

Dependencies:
    ssl: Verify codes and the confirmation context.
    vakr.lifecycle.egress.transport: The same DNS, TCP, proxy, and TLS
        steps the client uses.

Safety:
    The confirmation context verifies certificates and host names exactly
    as the real one does, minus the strict-profile flag. The confirmation
    completes a handshake, closes the connection, and sends no application
    data; its result only selects diagnostic text, and requests never fall
    back to it (a static test checks that it sends nothing).

Example:
    >>> is_strict_only(85, "Missing Authority Key Identifier")
    True
    >>> is_strict_only(20, "unable to get local issuer certificate")
    False
"""

import ssl
from dataclasses import dataclass

from vakr.lifecycle.egress.errors import CertificateVerifyError, EgressError
from vakr.lifecycle.egress.proxy import ProxyEndpoint
from vakr.lifecycle.egress.transport import open_stream, tls_wrap

STRICT_ONLY_CODES = frozenset({78, *range(80, 95)})
"""OpenSSL 3 ``X509_V_ERR_*`` codes raised only under ``X509_V_FLAG_X509_STRICT``.

78 signature algorithm inconsistency; 80-82 pathLen or keyCertSign misuse;
83-84 empty issuer or subject; 85 missing authority key identifier; 86
missing subject key identifier; 87-88 subjectAltName problems; 89
basicConstraints not critical; 90-91 critical key identifiers; 92 CA
without keyUsage; 93 extensions in a v1 certificate; 94 explicit EC
parameters.
"""
STRICT_ONLY_MESSAGES = (
    "missing authority key identifier",
    "missing subject key identifier",
    "basic constraints of ca cert not marked critical",
    "ca cert does not include key usage extension",
    "key identifier marked critical",
    "using cert extension requires at least x509v3",
    "certificate public key has explicit ecc parameters",
)
"""Lower-case OpenSSL messages for the same rules, used when no code is known."""


@dataclass(frozen=True, slots=True)
class StrictVerdict:
    """What a verification failure means for the strict-profile policy.

    Attributes:
        strict_only: The failure disappears with only the strict profile
            relaxed, so the EGRESS-007 fallback would fix it.
        note: Text to append to the failed TLS step, possibly empty.
        reason: The failure that remains with the strict profile relaxed
            (the original reason when no confirmation ran).
    """

    strict_only: bool
    note: str
    reason: str


def is_strict_only(code: int | None, message: str = "") -> bool:
    """Return whether an OpenSSL verify failure comes from a strict-profile rule.

    Args:
        code: ``SSLCertVerificationError.verify_code``, or ``None``.
        message: ``SSLCertVerificationError.verify_message``.

    Returns:
        bool: ``True`` for a rule that only ``VERIFY_X509_STRICT`` applies.

    Raises:
        None
    """
    if code is not None and code in STRICT_ONLY_CODES:
        return True
    lowered = message.lower()
    return any(marker in lowered for marker in STRICT_ONLY_MESSAGES)


def relaxed_copy(context: ssl.SSLContext) -> ssl.SSLContext:
    """Build a context with the same trust anchors and only the strict profile cleared.

    Args:
        context: The verifying context whose handshake failed.

    Returns:
        ssl.SSLContext: ``CERT_REQUIRED`` with hostname checking, the same
        minimum TLS version and verify flags, minus ``VERIFY_X509_STRICT``.

    Raises:
        ssl.SSLError: When the anchors cannot be copied.
    """
    relaxed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    relaxed.minimum_version = context.minimum_version
    relaxed.verify_flags = context.verify_flags & ~ssl.VERIFY_X509_STRICT
    anchors = context.get_ca_certs(binary_form=True)
    if anchors:
        relaxed.load_verify_locations(cadata=b"".join(anchors))
    return relaxed


def confirm_relaxed(
    host: str, port: int, proxy: ProxyEndpoint | None, timeout: float, context: ssl.SSLContext
) -> str | None:
    """Handshake with only the strict profile relaxed; diagnosis only, sends no data.

    Args:
        host: Server name, verified exactly as in the failed attempt.
        port: Server port.
        proxy: Proxy used by the failed attempt, or ``None``.
        timeout: Socket timeout in seconds.
        context: The verifying context whose handshake failed.

    Returns:
        str | None: ``None`` when the relaxed handshake verifies, otherwise
        the verification failure that remains.

    Raises:
        EgressError: From DNS, TCP, proxy, or a non-verification TLS failure.
    """
    try:
        relaxed = relaxed_copy(context)
    except ssl.SSLError as exc:
        raise EgressError("tls", f"cannot copy the trust store: {exc}") from exc
    stream = open_stream(host, port, proxy, timeout)
    try:
        tls = tls_wrap(stream, relaxed, host, port)
    except CertificateVerifyError as exc:
        return exc.reason
    tls.close()
    return None


def assess(
    exc: CertificateVerifyError,
    proxy: ProxyEndpoint | None,
    timeout: float,
    context: ssl.SSLContext,
) -> StrictVerdict:
    """Decide whether a verification failure is caused only by the strict profile.

    Args:
        exc: The verification failure.
        proxy: Proxy used by the failed attempt.
        timeout: Socket timeout for the confirmation handshake.
        context: The verifying context whose handshake failed.

    Returns:
        StrictVerdict: Not strict-only, without a note, when the failure is
        not a strict-profile rule.

    Raises:
        None
    """
    if not is_strict_only(exc.code, exc.reason):
        return StrictVerdict(False, "", exc.reason)
    try:
        remaining = confirm_relaxed(exc.host, exc.port, proxy, timeout, context)
    except EgressError as confirm_exc:
        note = f"; strict X.509 profile rule (confirmation failed: {confirm_exc})"
        return StrictVerdict(True, note, exc.reason)
    if remaining is None:
        note = "; only the strict X.509 profile rejects it (it verifies with just that relaxed)"
        return StrictVerdict(True, note, exc.reason)
    note = f"; with the strict X.509 profile relaxed it still fails: {remaining}"
    return StrictVerdict(False, note, remaining)
