"""Credential value objects: SNMPv2c and SNMPv3 (SNMP-001..003, blueprint 15.2).

Only a credential provider turns a ``CredentialReference`` into an
``SnmpV2cCredential`` or an ``SnmpV3Credential``, immediately before an SNMP
session is created (blueprint 3.3, 15.2). Every secret field is a ``Secret``,
so the default dataclass ``repr`` of a credential is already redacted.

SNMPv3 objects check their own coherence: ``noAuthNoPriv`` carries no
protocols or secrets, ``authNoPriv`` authentication only, ``authPriv`` both,
and each passphrase has at least 8 characters (RFC 3414 section 11.2).
``CredentialSummary`` is the non-secret description that commands print.

Dependencies:
    vakr.domain.enums: ``SnmpVersion``.
    vakr.credentials.vocabulary: Names, enums, and recommended protocols.
    vakr.credentials.secret: The redacting secret wrapper.

Safety:
    Validation messages name fields and rules, never values.

Example:
    >>> SnmpV2cCredential("network-v2c", Secret("placeholder"))
    SnmpV2cCredential(name='network-v2c', community=Secret('***'))
"""

from dataclasses import dataclass
from enum import StrEnum

from vakr.credentials.errors import CredentialValueError
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import (
    MIN_PASSPHRASE,
    RECOMMENDED_AUTH,
    RECOMMENDED_PRIV,
    AuthProtocol,
    PrivProtocol,
    SecurityLevel,
    check_name,
)
from vakr.domain.enums import SnmpVersion


def _require_secret(field: str, value: object, minimum: int = 1) -> None:
    """Check that a field holds a ``Secret`` of at least ``minimum`` characters.

    Args:
        field: Field name for the error message.
        value: The field's value.
        minimum: Smallest acceptable length.

    Returns:
        None

    Raises:
        CredentialValueError: If the value is not a ``Secret`` or is too short;
            the message never states the actual length.
    """
    if not isinstance(value, Secret):
        raise CredentialValueError(field, "must be a Secret")
    if len(value.reveal()) < minimum:
        reason = "must not be empty" if minimum == 1 else f"must be at least {minimum} characters"
        raise CredentialValueError(field, reason)


@dataclass(frozen=True, slots=True)
class SnmpV2cCredential:
    """An SNMPv2c community credential (SNMP-001); the MVP polling credential.

    Attributes:
        name: Credential name that device records reference.
        community: The community string.
    """

    name: str
    community: Secret

    def __post_init__(self) -> None:
        """Validate the name and community.

        Args:
            None

        Returns:
            None

        Raises:
            CredentialValueError: If the name is invalid or the community is
                not a non-empty ``Secret``.
        """
        check_name(self.name)
        _require_secret("community", self.community)

    @property
    def version(self) -> SnmpVersion:
        """Return the SNMP version.

        Args:
            None

        Returns:
            SnmpVersion: ``V2C``.

        Raises:
            None
        """
        return SnmpVersion.V2C

    @property
    def security_level(self) -> SecurityLevel | None:
        """Return the SNMPv3 security level, which v2c does not have.

        Args:
            None

        Returns:
            SecurityLevel | None: Always ``None``.

        Raises:
            None
        """
        return None

    @property
    def is_recommended(self) -> bool:
        """Report whether this is the SNMP-003 posture; v2c never is.

        Args:
            None

        Returns:
            bool: ``False``: the community crosses the network in clear text.

        Raises:
            None
        """
        return False

    def secrets(self) -> tuple[Secret, ...]:
        """Return every secret this credential holds, for redaction.

        Args:
            None

        Returns:
            tuple[Secret, ...]: The community.

        Raises:
            None
        """
        return (self.community,)


@dataclass(frozen=True, slots=True)
class SnmpV3Credential:
    """An SNMPv3 USM credential (SNMP-002); post-MVP polling, complete now.

    Attributes:
        name: Credential name that device records reference.
        username: USM user name; an identifier, not a secret.
        security_level: Which of authentication and privacy apply.
        auth_protocol: Authentication protocol, set unless ``noAuthNoPriv``.
        auth_secret: Authentication passphrase, set unless ``noAuthNoPriv``.
        privacy_protocol: Privacy protocol, set only for ``authPriv``.
        privacy_secret: Privacy passphrase, set only for ``authPriv``.
    """

    name: str
    username: str
    security_level: SecurityLevel
    auth_protocol: AuthProtocol | None = None
    auth_secret: Secret | None = None
    privacy_protocol: PrivProtocol | None = None
    privacy_secret: Secret | None = None

    def __post_init__(self) -> None:
        """Validate the name, user name, and protocol/secret combination.

        Args:
            None

        Returns:
            None

        Raises:
            CredentialValueError: On the first incoherent field.
        """
        check_name(self.name)
        if not isinstance(self.username, str) or not self.username:
            raise CredentialValueError("username", "must be a non-empty string")
        if not isinstance(self.security_level, SecurityLevel):
            raise CredentialValueError("security_level", "must be a SecurityLevel")
        uses_auth = self.security_level is not SecurityLevel.NO_AUTH_NO_PRIV
        uses_priv = self.security_level is SecurityLevel.AUTH_PRIV
        self._check_pair("auth", AuthProtocol, self.auth_protocol, self.auth_secret, uses_auth)
        self._check_pair(
            "privacy", PrivProtocol, self.privacy_protocol, self.privacy_secret, uses_priv
        )

    def _check_pair(
        self, prefix: str, kind: type[StrEnum], protocol: object, secret: object, wanted: bool
    ) -> None:
        """Check one protocol/secret pair against the security level.

        Args:
            prefix: ``auth`` or ``privacy``, the field-name prefix.
            kind: Enum the protocol must belong to.
            protocol: The protocol field's value.
            secret: The secret field's value.
            wanted: Whether the security level requires this pair.

        Returns:
            None

        Raises:
            CredentialValueError: If a required half is missing or invalid, or
                a pair is present that the security level does not use.
        """
        level = self.security_level.value
        if not wanted:
            if protocol is not None or secret is not None:
                field = f"{prefix}_protocol" if protocol is not None else f"{prefix}_secret"
                raise CredentialValueError(field, f"must not be set for {level}")
            return
        if not isinstance(protocol, kind):
            raise CredentialValueError(f"{prefix}_protocol", f"is required for {level}")
        if secret is None:
            raise CredentialValueError(f"{prefix}_secret", f"is required for {level}")
        _require_secret(f"{prefix}_secret", secret, MIN_PASSPHRASE)

    @property
    def version(self) -> SnmpVersion:
        """Return the SNMP version.

        Args:
            None

        Returns:
            SnmpVersion: ``V3``.

        Raises:
            None
        """
        return SnmpVersion.V3

    @property
    def is_recommended(self) -> bool:
        """Report whether this is ``authPriv`` with SHA-2 and AES (SNMP-003).

        Args:
            None

        Returns:
            bool: ``True`` only for the recommended posture.

        Raises:
            None
        """
        return (
            self.security_level is SecurityLevel.AUTH_PRIV
            and self.auth_protocol in RECOMMENDED_AUTH
            and self.privacy_protocol in RECOMMENDED_PRIV
        )

    def secrets(self) -> tuple[Secret, ...]:
        """Return every secret this credential holds, for redaction.

        Args:
            None

        Returns:
            tuple[Secret, ...]: The authentication and privacy secrets present.

        Raises:
            None
        """
        return tuple(s for s in (self.auth_secret, self.privacy_secret) if s is not None)


type Credential = SnmpV2cCredential | SnmpV3Credential
"""Any credential a provider can resolve."""


@dataclass(frozen=True, slots=True)
class CredentialSummary:
    """Non-secret description of one entry, for ``vakr credentials`` output.

    Attributes:
        name: Entry name.
        kind: ``snmp`` for a credential, ``secret`` for a ``[secrets]`` entry.
        version: SNMP version, or ``None`` for a named secret.
        security_level: SNMPv3 security level, or ``None``.
        recommended: Whether an SNMP credential meets SNMP-003; ``None`` for
            a named secret.
    """

    name: str
    kind: str
    version: SnmpVersion | None = None
    security_level: SecurityLevel | None = None
    recommended: bool | None = None
