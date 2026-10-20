r"""Credentials: secret values, SNMP credentials, and the protected file provider.

vakr polls devices with SNMPv2c communities (MVP) and SNMPv3 USM users, and
connects to databases with DSNs. All of them are secrets that must never
reach a log, traceback, repr, API response, diagnostic, support bundle, or
audit record (invariant 9, SEC-003). This package owns that material
(blueprint 3.3): device records hold only a ``CredentialReference``, and a
``CredentialResolver`` turns it into a credential immediately before use.

Redaction is by construction. Every secret is wrapped in ``Secret`` as soon
as it is parsed, file errors carry lines and keys but never values, and
``RedactingFilter`` scrubs log records as a second line of defence.

Dependencies:
    tomllib, os: Read and check ``/etc/vakr/secrets/credentials.toml``.
    vakr.domain.enums: ``SnmpVersion``.
    vakr.paths: Installation layout.

Safety:
    ``Secret.reveal()`` is the only way to read a value;
    ``grep -rn '\.reveal()' src`` audits every use.

Example:
    >>> from vakr.credentials import Secret, SnmpV2cCredential
    >>> SnmpV2cCredential("network-v2c", Secret("placeholder"))
    SnmpV2cCredential(name='network-v2c', community=Secret('***'))
"""

from vakr.credentials.errors import (
    CredentialError,
    CredentialFileError,
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialNotFoundError,
    CredentialPermissionError,
    CredentialValueError,
)
from vakr.credentials.model import (
    Credential,
    CredentialSummary,
    SnmpV2cCredential,
    SnmpV3Credential,
)
from vakr.credentials.port import CredentialResolver
from vakr.credentials.provider import FileCredentialProvider
from vakr.credentials.redact import RedactingFilter, redact_text
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import (
    AuthProtocol,
    CredentialReference,
    PrivProtocol,
    ProviderKind,
    SecurityLevel,
)

__all__ = [
    "AuthProtocol",
    "Credential",
    "CredentialError",
    "CredentialFileError",
    "CredentialFileMissingError",
    "CredentialFileUnreadableError",
    "CredentialNotFoundError",
    "CredentialPermissionError",
    "CredentialReference",
    "CredentialResolver",
    "CredentialSummary",
    "CredentialValueError",
    "FileCredentialProvider",
    "PrivProtocol",
    "ProviderKind",
    "RedactingFilter",
    "Secret",
    "SecurityLevel",
    "SnmpV2cCredential",
    "SnmpV3Credential",
    "redact_text",
]
