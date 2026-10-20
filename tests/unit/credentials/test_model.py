"""Unit tests for credential value objects: v2c, v3 combinations, references.

Covers SNMP-001 (v2c and v3), SNMP-002 (v3 record fields), SNMP-003 (the
recommended SHA-2/AES posture), and SEC-001 (references, not secrets).

Dependencies:
    pytest: Parametrised combination matrix.

Example:
    $ uv run pytest tests/unit/credentials/test_model.py
"""

import itertools
from typing import Any

import pytest

from tests.unit.credentials.credentials_testkit import FakeSecrets
from vakr.credentials.errors import CredentialValueError
from vakr.credentials.model import SnmpV2cCredential, SnmpV3Credential
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import (
    AuthProtocol,
    CredentialReference,
    PrivProtocol,
    ProviderKind,
    SecurityLevel,
)
from vakr.domain.enums import SnmpVersion

LEVEL = SecurityLevel


def test_v2c_credential(fake: FakeSecrets) -> None:
    """A v2c credential exposes its version, has no level, and is never recommended.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    cred = SnmpV2cCredential("network-v2c", Secret(fake.community))
    assert cred.version is SnmpVersion.V2C
    assert cred.security_level is None
    assert cred.is_recommended is False
    assert cred.secrets() == (Secret(fake.community),)
    assert repr(cred) == "SnmpV2cCredential(name='network-v2c', community=Secret('***'))"


@pytest.mark.parametrize(
    ("name", "community", "field"),
    [
        ("bad name", Secret("x"), "name"),
        ("-leading", Secret("x"), "name"),
        ("ok", Secret(""), "community"),
        ("ok", "plain", "community"),
    ],
)
def test_v2c_rejects_invalid_fields(name: str, community: Any, field: str) -> None:
    """Names follow the name rule; the community must be a non-empty ``Secret``.

    Args:
        name: Credential name.
        community: Community value.
        field: Field the error must name.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(CredentialValueError) as info:
        SnmpV2cCredential(name, community)
    assert info.value.field == field


def _v3(level: SecurityLevel, **fields: Any) -> SnmpV3Credential:
    """Build an SNMPv3 credential with defaults for the name and user.

    Args:
        level: Security level.
        **fields: Protocol and secret fields.

    Returns:
        SnmpV3Credential: The credential.

    Raises:
        CredentialValueError: If the combination is invalid.
    """
    return SnmpV3Credential("network-snmpv3", "netmon", level, **fields)


def test_v3_valid_combinations(fake: FakeSecrets) -> None:
    """Each security level accepts exactly the fields it uses (SNMP-002).

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    auth, priv = Secret(fake.auth), Secret(fake.privacy)
    assert _v3(LEVEL.NO_AUTH_NO_PRIV).secrets() == ()
    one = _v3(LEVEL.AUTH_NO_PRIV, auth_protocol=AuthProtocol.SHA256, auth_secret=auth)
    assert one.secrets() == (auth,)
    full = _v3(
        LEVEL.AUTH_PRIV,
        auth_protocol=AuthProtocol.SHA256,
        auth_secret=auth,
        privacy_protocol=PrivProtocol.AES128,
        privacy_secret=priv,
    )
    assert full.version is SnmpVersion.V3
    assert full.secrets() == (auth, priv)
    assert fake.auth not in repr(full)
    assert fake.privacy not in repr(full)


@pytest.mark.parametrize(("auth", "priv"), list(itertools.product(AuthProtocol, PrivProtocol)))
def test_v3_every_protocol_pair_is_accepted(auth: AuthProtocol, priv: PrivProtocol) -> None:
    """All 24 authPriv protocol pairs are valid; only SHA-2 with AES is recommended.

    Args:
        auth: Authentication protocol.
        priv: Privacy protocol.

    Returns:
        None

    Raises:
        None
    """
    cred = _v3(
        LEVEL.AUTH_PRIV,
        auth_protocol=auth,
        auth_secret=Secret("a" * 8),
        privacy_protocol=priv,
        privacy_secret=Secret("p" * 8),
    )
    sha2 = auth not in {AuthProtocol.MD5, AuthProtocol.SHA}
    assert cred.is_recommended is (sha2 and priv is not PrivProtocol.DES)


@pytest.mark.parametrize(
    ("level", "fields", "bad_field"),
    [
        (LEVEL.NO_AUTH_NO_PRIV, {"auth_protocol": AuthProtocol.SHA256}, "auth_protocol"),
        (LEVEL.NO_AUTH_NO_PRIV, {"auth_secret": Secret("a" * 8)}, "auth_secret"),
        (LEVEL.AUTH_NO_PRIV, {"auth_secret": Secret("a" * 8)}, "auth_protocol"),
        (LEVEL.AUTH_NO_PRIV, {"auth_protocol": AuthProtocol.SHA}, "auth_secret"),
        (
            LEVEL.AUTH_NO_PRIV,
            {"auth_protocol": "SHA", "auth_secret": Secret("a" * 8)},
            "auth_protocol",
        ),
        (
            LEVEL.AUTH_NO_PRIV,
            {
                "auth_protocol": AuthProtocol.SHA,
                "auth_secret": Secret("a" * 8),
                "privacy_protocol": PrivProtocol.AES128,
            },
            "privacy_protocol",
        ),
        (
            LEVEL.AUTH_PRIV,
            {"auth_protocol": AuthProtocol.SHA256, "auth_secret": Secret("a" * 8)},
            "privacy_protocol",
        ),
        (
            LEVEL.AUTH_PRIV,
            {
                "auth_protocol": AuthProtocol.SHA256,
                "auth_secret": Secret("a" * 8),
                "privacy_protocol": PrivProtocol.AES128,
                "privacy_secret": Secret("short"),
            },
            "privacy_secret",
        ),
        (
            LEVEL.AUTH_NO_PRIV,
            {"auth_protocol": AuthProtocol.SHA, "auth_secret": "plain-text"},
            "auth_secret",
        ),
    ],
)
def test_v3_incoherent_combinations_are_rejected(
    level: SecurityLevel, fields: dict[str, Any], bad_field: str
) -> None:
    """Missing, extra, mistyped, or too-short fields name the field, never a value.

    Args:
        level: Security level.
        fields: Protocol and secret fields.
        bad_field: The field the error must name.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(CredentialValueError) as info:
        _v3(level, **fields)
    assert info.value.field == bad_field
    assert "short" not in str(info.value)
    assert "plain-text" not in str(info.value)


@pytest.mark.parametrize(
    ("username", "level", "field"),
    [("", LEVEL.NO_AUTH_NO_PRIV, "username"), ("netmon", "authPriv", "security_level")],
)
def test_v3_rejects_bad_user_or_level(username: str, level: Any, field: str) -> None:
    """The user name must be non-empty and the level a ``SecurityLevel`` member.

    Args:
        username: USM user name.
        level: Security level value.
        field: Field the error must name.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(CredentialValueError) as info:
        SnmpV3Credential("n", username, level)
    assert info.value.field == field


def test_reference_holds_only_a_name() -> None:
    """A reference is a name plus provider kind and validates the name (SEC-001).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ref = CredentialReference("network-v2c")
    assert ref.provider is ProviderKind.FILE
    assert repr(ref) == (
        "CredentialReference(name='network-v2c', provider=<ProviderKind.FILE: 'file'>)"
    )
    with pytest.raises(CredentialValueError):
        CredentialReference("has space")
