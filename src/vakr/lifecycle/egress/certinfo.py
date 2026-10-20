"""Inspect and validate X.509 certificates for trust decisions and diagnosis.

Before an operator trusts an enterprise TLS-inspection CA, vakr shows the
subject, SHA-256 fingerprint, and expiry so they can be compared with the
security team's published values, and refuses anything that is not a
currently valid CA certificate in PEM form (EGRESS-003). It also lists what
a CA lacks for the strict X.509 profile that Python 3.13+ asks OpenSSL to
enforce (``VERIFY_X509_STRICT``): such a CA is rejected during every
handshake even after it has been trusted. The same summaries name the
issuer a server presented when verification fails (EGRESS-005) and drive
the CA-expiry health check (EGRESS-006).

Dependencies:
    cryptography.x509: Certificate parsing; the standard library has no
        public X.509 parser.

Safety:
    Parsing never trusts anything; validation only accepts single PEM CA
    certificates within their validity window. Rejection messages name the
    reason without echoing file contents. The strict-profile check only
    reports gaps; whether they are fatal is the caller's policy.

Example:
    >>> summary = summarize(cert)  # doctest: +SKIP
    >>> summary.subject  # doctest: +SKIP
    'CN=Example Inspection CA,O=Example'
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.exceptions import InvalidSignature
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
            ``basicConstraints CA:TRUE``), a ``keyUsage`` that forbids
            certificate signing, or a certificate outside its validity window.
    """
    cert = load_single_pem(data)
    if not _is_ca(cert):
        raise CertificateRejectedError(
            "not a CA certificate (basicConstraints CA:TRUE is required)"
        )
    # Unlike a missing keyUsage, this fails in every OpenSSL mode, so no
    # setting (not even EGRESS-007's fallback) could make the CA usable.
    usage = _extension(cert, x509.KeyUsage)
    if usage is not None and not usage.value.key_cert_sign:
        raise CertificateRejectedError(
            "keyUsage does not include keyCertSign, so it cannot sign certificates"
        )
    if now < cert.not_valid_before_utc:
        raise CertificateRejectedError(
            f"not valid until {cert.not_valid_before_utc:%Y-%m-%d %H:%M}Z"
        )
    if now > cert.not_valid_after_utc:
        raise CertificateRejectedError(f"expired on {cert.not_valid_after_utc:%Y-%m-%d %H:%M}Z")
    return cert


def strict_profile_gaps(cert: x509.Certificate) -> list[str]:
    """List what a CA certificate lacks for the strict X.509 profile (EGRESS-003).

    The rules are the CA-side checks of OpenSSL's ``X509_V_FLAG_X509_STRICT``
    that enterprise inspection CAs typically fail: version 3, critical
    ``basicConstraints`` with CA:TRUE, ``keyUsage`` with ``keyCertSign``, a
    subject key identifier, and an authority key identifier unless the CA
    is self-signed.

    Args:
        cert: Parsed CA certificate.

    Returns:
        list[str]: One operator-readable missing item per failed rule, in a
        stable order; empty when the certificate meets the profile.

    Raises:
        None
    """
    if cert.version is not x509.Version.v3:
        return ["X.509 version 3 (this certificate is version 1, so it has no extensions)"]
    gaps: list[str] = []
    constraints = _extension(cert, x509.BasicConstraints)
    if constraints is None or not constraints.value.ca:
        gaps.append("basicConstraints extension with CA:TRUE")
    elif not constraints.critical:
        gaps.append("basicConstraints marked critical (it is present but not critical)")
    usage = _extension(cert, x509.KeyUsage)
    if usage is None:
        gaps.append("keyUsage extension (with keyCertSign)")
    elif not usage.value.key_cert_sign:
        gaps.append("keyCertSign in the keyUsage extension")
    if _extension(cert, x509.SubjectKeyIdentifier) is None:
        gaps.append("subject key identifier (subjectKeyIdentifier extension)")
    authority = _extension(cert, x509.AuthorityKeyIdentifier)
    if (authority is None or authority.value.key_identifier is None) and not is_self_signed(cert):
        gaps.append(
            "authority key identifier (authorityKeyIdentifier with a keyIdentifier), "
            "required because this CA is not self-signed"
        )
    return gaps


def is_self_signed(cert: x509.Certificate) -> bool:
    """Return whether ``cert`` names itself as issuer and its own key signed it.

    Args:
        cert: Parsed certificate.

    Returns:
        bool: ``True`` only when issuer equals subject and the signature
        verifies with the certificate's own public key.

    Raises:
        None
    """
    try:
        cert.verify_directly_issued_by(cert)
    except ValueError, TypeError, InvalidSignature:
        return False
    return True


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
    ext = _extension(cert, x509.BasicConstraints)
    return ext is not None and bool(ext.value.ca)


def _extension[T: x509.ExtensionType](
    cert: x509.Certificate, kind: type[T]
) -> x509.Extension[T] | None:
    """Return the extension of type ``kind``, or ``None`` when absent.

    Args:
        cert: Parsed certificate.
        kind: Extension class, for example ``x509.KeyUsage``.

    Returns:
        x509.Extension | None: The extension with its criticality.

    Raises:
        None
    """
    try:
        return cert.extensions.get_extension_for_class(kind)
    except x509.ExtensionNotFound:
        return None
