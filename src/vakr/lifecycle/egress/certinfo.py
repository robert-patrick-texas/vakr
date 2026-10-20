"""Inspect and validate X.509 certificates for trust decisions and diagnosis.

Before an operator trusts an enterprise TLS-inspection CA, vakr shows the
subject, SHA-256 fingerprint, and expiry so they can be compared with the
security team's published values, and refuses anything that is not a
currently valid CA certificate in PEM form (EGRESS-003). The same summaries
name the issuer a server presented when verification fails (EGRESS-005)
and drive the CA-expiry health check (EGRESS-006).

Dependencies:
    cryptography.x509: Certificate parsing; the standard library has no
        public X.509 parser.

Safety:
    Parsing never trusts anything; validation only accepts single PEM CA
    certificates within their validity window. Rejection messages name the
    reason without echoing file contents.

Example:
    >>> summary = summarize(cert)  # doctest: +SKIP
    >>> summary.subject  # doctest: +SKIP
    'CN=Example Inspection CA,O=Example'
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes

PEM_MARKER = b"-----BEGIN CERTIFICATE-----"
EXPIRY_WARNING = timedelta(days=30)
"""A trusted CA expiring sooner than this turns health WARNING (EGRESS-006)."""


class CertificateRejectedError(ValueError):
    """A certificate offered for trust failed validation; the message says why."""


@dataclass(frozen=True, slots=True)
class CertSummary:
    """Operator-facing facts about one certificate.

    Attributes:
        subject: RFC 4514 subject distinguished name.
        issuer: RFC 4514 issuer distinguished name.
        sha256: Upper-case colon-separated SHA-256 fingerprint.
        not_before: Start of validity (UTC).
        not_after: End of validity (UTC).
        is_ca: Whether basicConstraints marks it as a CA.
    """

    subject: str
    issuer: str
    sha256: str
    not_before: datetime
    not_after: datetime
    is_ca: bool


def summarize(cert: x509.Certificate) -> CertSummary:
    """Extract the operator-facing fields of ``cert``.

    Args:
        cert: Parsed certificate.

    Returns:
        CertSummary: Subject, issuer, fingerprint, validity, and CA flag.

    Raises:
        None
    """
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return CertSummary(
        subject=cert.subject.rfc4514_string() or "(empty subject)",
        issuer=cert.issuer.rfc4514_string() or "(empty issuer)",
        sha256=":".join(digest[i : i + 2] for i in range(0, len(digest), 2)),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        is_ca=_is_ca(cert),
    )


def summarize_der(der: bytes) -> CertSummary:
    """Summarize a DER certificate, such as one a TLS server presented.

    Args:
        der: DER-encoded certificate.

    Returns:
        CertSummary: The certificate's summary.

    Raises:
        CertificateRejectedError: When ``der`` is not a parseable certificate.
    """
    try:
        return summarize(x509.load_der_x509_certificate(der))
    except ValueError as exc:
        raise CertificateRejectedError("presented certificate could not be parsed") from exc


def load_single_pem(data: bytes) -> x509.Certificate:
    """Parse exactly one PEM certificate from ``data``.

    Args:
        data: File contents.

    Returns:
        x509.Certificate: The certificate.

    Raises:
        CertificateRejectedError: When the data is not PEM, does not parse, or
            holds more than one certificate.
    """
    if PEM_MARKER not in data:
        raise CertificateRejectedError("not a PEM certificate (no BEGIN CERTIFICATE block)")
    try:
        certs = x509.load_pem_x509_certificates(data)
    except ValueError as exc:
        raise CertificateRejectedError("PEM certificate could not be parsed") from exc
    if len(certs) != 1:
        raise CertificateRejectedError(
            f"file holds {len(certs)} certificates; add them one at a time"
        )
    return certs[0]


def validate_ca(data: bytes, now: datetime) -> x509.Certificate:
    """Accept only a single PEM CA certificate that is currently valid.

    Args:
        data: Candidate PEM file contents.
        now: Current time (timezone-aware UTC).

    Returns:
        x509.Certificate: The validated CA certificate.

    Raises:
        CertificateRejectedError: For non-PEM data, a leaf certificate (no
            ``basicConstraints CA:TRUE``), or a certificate outside its
            validity window.
    """
    cert = load_single_pem(data)
    if not _is_ca(cert):
        raise CertificateRejectedError(
            "not a CA certificate (basicConstraints CA:TRUE is required)"
        )
    if now < cert.not_valid_before_utc:
        raise CertificateRejectedError(
            f"not valid until {cert.not_valid_before_utc:%Y-%m-%d %H:%M}Z"
        )
    if now > cert.not_valid_after_utc:
        raise CertificateRejectedError(f"expired on {cert.not_valid_after_utc:%Y-%m-%d %H:%M}Z")
    return cert


def expiry_state(not_after: datetime, now: datetime, warn: timedelta = EXPIRY_WARNING) -> str:
    """Classify a trusted CA's remaining lifetime for health reporting.

    Args:
        not_after: Certificate expiry (UTC).
        now: Current time (UTC).
        warn: Remaining lifetime below which the CA is ``"expiring"``.

    Returns:
        str: ``"expired"``, ``"expiring"``, or ``"ok"``.

    Raises:
        None
    """
    if not_after <= now:
        return "expired"
    return "expiring" if not_after - now < warn else "ok"


def _is_ca(cert: x509.Certificate) -> bool:
    """Return whether ``cert`` carries ``basicConstraints CA:TRUE``.

    Args:
        cert: Parsed certificate.

    Returns:
        bool: ``False`` when the extension is absent or says CA:FALSE.

    Raises:
        None
    """
    try:
        ext = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return False
    return bool(ext.value.ca)
