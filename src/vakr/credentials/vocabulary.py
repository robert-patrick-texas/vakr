"""Credential vocabulary: provider kinds, SNMPv3 choices, names, and references.

Device records store a ``CredentialReference``, a name plus the provider
that resolves it, never a secret (SEC-001, CONF-007). The SNMPv3 enums use
the spellings that appear in device configuration and in
``credentials.toml``, so an operator can copy them across unchanged
(SNMP-002). ``RECOMMENDED_AUTH`` and ``RECOMMENDED_PRIV`` encode the SHA-2 and
AES posture SNMP-003 recommends.

Dependencies:
    enum, re: Enumerations and the name rule.
    vakr.credentials.errors: ``CredentialValueError``.

Safety:
    Nothing here holds a secret; names and choices are safe to print.

Example:
    >>> ref = CredentialReference("network-v2c")
    >>> ref.provider.value, SecurityLevel("authPriv").name
    ('file', 'AUTH_PRIV')
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from vakr.credentials.errors import CredentialValueError


class ProviderKind(StrEnum):
    """Which credential provider owns a reference (SEC-002); Vault comes later."""

    FILE = "file"


class SecurityLevel(StrEnum):
    """SNMPv3 USM security level (RFC 3414), spelled as in device configuration."""

    NO_AUTH_NO_PRIV = "noAuthNoPriv"
    AUTH_NO_PRIV = "authNoPriv"
    AUTH_PRIV = "authPriv"


class AuthProtocol(StrEnum):
    """SNMPv3 authentication protocol; ``SHA`` is SHA-1 (RFC 3414, RFC 7860)."""

    MD5 = "MD5"
    SHA = "SHA"
    SHA224 = "SHA224"
    SHA256 = "SHA256"
    SHA384 = "SHA384"
    SHA512 = "SHA512"


class PrivProtocol(StrEnum):
    """SNMPv3 privacy (encryption) protocol (RFC 3414, RFC 3826)."""

    DES = "DES"
    AES128 = "AES128"
    AES192 = "AES192"
    AES256 = "AES256"


RECOMMENDED_AUTH = frozenset(
    {AuthProtocol.SHA224, AuthProtocol.SHA256, AuthProtocol.SHA384, AuthProtocol.SHA512}
)
"""SHA-2 authentication protocols (SNMP-003)."""
RECOMMENDED_PRIV = frozenset({PrivProtocol.AES128, PrivProtocol.AES192, PrivProtocol.AES256})
"""AES privacy protocols (SNMP-003)."""
MIN_PASSPHRASE = 8
"""RFC 3414 section 11.2 minimum passphrase length."""
NAME_RULE = "must start with a letter or digit and use only letters, digits, '.', '_' and '-'"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def check_name(name: object) -> str:
    """Validate a credential or secret name, which device records refer to.

    Args:
        name: Candidate name.

    Returns:
        str: The name, unchanged.

    Raises:
        CredentialValueError: If the name is not a string matching ``NAME_RULE``.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise CredentialValueError("name", NAME_RULE)
    return name


@dataclass(frozen=True, slots=True)
class CredentialReference:
    """What a device record stores instead of a secret (SEC-001, CONF-007).

    Attributes:
        name: Credential name, such as ``network-v2c``.
        provider: Provider that resolves the name.
    """

    name: str
    provider: ProviderKind = ProviderKind.FILE

    def __post_init__(self) -> None:
        """Validate the name.

        Args:
            None

        Returns:
            None

        Raises:
            CredentialValueError: If the name breaks ``NAME_RULE``.
        """
        check_name(self.name)
