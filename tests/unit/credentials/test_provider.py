"""Unit tests for the file provider: parsing, validation, resolution, and rotation.

Covers SEC-001/SEC-002 (references resolved by a file provider behind a
resolver port), CONF-007 (a separate protected file), blueprint 11.3 (the
file format), and 15.2 (rotation without device-record changes).

Dependencies:
    pytest: Fixtures and parametrisation.

Example:
    $ uv run pytest tests/unit/credentials/test_provider.py
"""

import os
from pathlib import Path

import pytest

from tests.unit.credentials.credentials_testkit import (
    FakeSecrets,
    Install,
    own_policy,
    v2c_table,
    v3_table,
)
from vakr.credentials.errors import (
    CredentialFileError,
    CredentialFileMissingError,
    CredentialNotFoundError,
    CredentialPermissionError,
)
from vakr.credentials.keylines import KeyLines
from vakr.credentials.model import CredentialSummary, SnmpV3Credential
from vakr.credentials.parse import syntax_phrase
from vakr.credentials.port import CredentialResolver
from vakr.credentials.provider import FileCredentialProvider
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import CredentialReference, SecurityLevel
from vakr.domain.enums import SnmpVersion

NAME_ERROR = "name must start with a letter or digit and use only letters, digits, '.', '_' and '-'"


def _provider(install: Install) -> FileCredentialProvider:
    """Build a strict provider for the test install that trusts the test user.

    Args:
        install: The relocated install.

    Returns:
        FileCredentialProvider: The provider.

    Raises:
        None
    """
    return FileCredentialProvider(install.secrets_dir, policy=own_policy())


def _issues(install: Install, text: str) -> list[str]:
    """Write a file and return its rendered validation issues.

    Args:
        install: The relocated install.
        text: File content.

    Returns:
        list[str]: Each issue as ``line: key: reason`` with the path removed.

    Raises:
        AssertionError: If the file is valid.
    """
    install.write(text)
    with pytest.raises(CredentialFileError) as info:
        _provider(install).summaries()
    return [i.render(install.path).removeprefix(f"{install.path}:") for i in info.value.issues]


def test_resolves_v2c_v3_and_named_secrets(install: Install, fake: FakeSecrets) -> None:
    """The 11.3 example layout resolves credentials and DSNs by name (SEC-001, SEC-002).

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = _provider(install)
    assert isinstance(provider, CredentialResolver)
    v2c = provider.resolve(CredentialReference("network-v2c"))
    assert v2c.secrets()[0].reveal() == fake.community
    v3 = provider.resolve(CredentialReference("network-snmpv3"))
    assert isinstance(v3, SnmpV3Credential)
    assert v3.security_level is SecurityLevel.AUTH_PRIV
    assert provider.secret("metadata-dsn").reveal() == fake.dsn
    assert set(provider.known_secrets()) == {Secret(v) for v in fake.values()}
    assert provider.summaries() == (
        CredentialSummary("network-snmpv3", "snmp", SnmpVersion.V3, SecurityLevel.AUTH_PRIV, True),
        CredentialSummary("network-v2c", "snmp", SnmpVersion.V2C, None, False),
        CredentialSummary("metadata-dsn", "secret"),
    )


def test_unknown_names_raise_not_found(install: Install, fake: FakeSecrets) -> None:
    """Unknown credential and secret names raise ``CredentialNotFoundError``.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = _provider(install)
    with pytest.raises(CredentialNotFoundError, match="no entry named 'absent'"):
        provider.resolve(CredentialReference("absent"))
    with pytest.raises(CredentialNotFoundError):
        provider.secret("telemetry-dsn")


def test_default_location_follows_install_paths(install: Install) -> None:
    """Without an argument the provider uses ``InstallPaths.from_environment()``.

    Args:
        install: The relocated install (sets ``VAKR_ROOT``).

    Returns:
        None

    Raises:
        None
    """
    provider = FileCredentialProvider()
    assert provider.path == install.path
    assert repr(provider) == f"FileCredentialProvider({str(install.path)!r}, development=False)"


def test_unknown_keys_and_missing_fields_name_line_and_key(
    install: Install, fake: FakeSecrets
) -> None:
    """Every problem is reported at once with its line and dotted key.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    text = (
        'stray = "x"\n'
        '[credentials.core]\nversion = "2c"\ncomunity = "x"\n'
        '[credentials.v3]\nversion = "3"\nusername = "netmon"\n'
        'security_level = "authPriv"\nauth_protocol = "SHA256"\n'
        f'auth_secret = "{fake.auth}"\n'
    )
    assert _issues(install, text) == [
        "1: stray: unknown top-level key; expected [credentials.NAME] tables and [secrets]",
        "4: credentials.core.comunity: unknown field for SNMPv2c",
        "2: credentials.core.community: missing required field",
        "5: credentials.v3.privacy_protocol: is required for authPriv",
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", "2: credentials.c.version: missing required field"),
        ("version = 3\n", '3: credentials.c.version: must be "2c" or "3"'),
        ('version = "2c"\ncommunity = 12345\n', "4: credentials.c.community: must be a string"),
        ('version = "2c"\ncommunity = ""\n', "4: credentials.c.community: must not be empty"),
        (
            'version = "3"\nusername = "u"\nsecurity_level = "authpriv"\n',
            "5: credentials.c.security_level: must be one of: noAuthNoPriv, authNoPriv, authPriv",
        ),
        (
            'version = "3"\nusername = "u"\nsecurity_level = "authNoPriv"\n'
            'auth_protocol = "SHA256"\nauth_secret = "short"\n',
            "7: credentials.c.auth_secret: must be at least 8 characters",
        ),
    ],
)
def test_field_validation(install: Install, body: str, expected: str) -> None:
    """Missing, mistyped, empty, unknown-choice, and short values are reported precisely.

    Args:
        install: The relocated install.
        body: Table body after the header.
        expected: The single rendered issue.

    Returns:
        None

    Raises:
        None
    """
    assert _issues(install, f"\n[credentials.c]\n{body}") == [expected]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("credentials = 5\n", "1: credentials: must be a table"),
        ("secrets = 5\n", "1: secrets: must be a table"),
        ("[credentials]\nflat = 1\n", "2: credentials.flat: must be a table"),
        (
            '[credentials."bad name"]\nversion = "2c"\ncommunity = "x"\n',
            f"1: credentials.bad?name: {NAME_ERROR}",
        ),
        ("[secrets]\ndsn = 5\n", "2: secrets.dsn: must be a string"),
        ('[secrets]\ndsn = ""\n', "2: secrets.dsn: must not be empty"),
        (
            '[secrets]\n"bad name" = "x"\n',
            f"2: secrets.bad?name: {NAME_ERROR}",
        ),
    ],
)
def test_structure_validation(install: Install, text: str, expected: str) -> None:
    """Wrongly shaped tables and invalid names are reported with line and key.

    Args:
        install: The relocated install.
        text: File content.
        expected: The single rendered issue.

    Returns:
        None

    Raises:
        None
    """
    assert _issues(install, text) == [expected]


def test_syntax_and_encoding_errors_report_lines(install: Install) -> None:
    """TOML syntax errors keep the line; invalid UTF-8 reports the line of the bad byte.

    Args:
        install: The relocated install.

    Returns:
        None

    Raises:
        None
    """
    assert _issues(install, '[credentials.c]\nversion = "2c"\ncommunity = "open\n') == [
        "3: TOML syntax error: illegal character"
    ]
    assert _issues(install, '[credentials.c]\nversion = "2c"\ncommunity = "open') == [
        "3: TOML syntax error: unterminated string"
    ]
    install.write(b'[credentials.c]\nversion = "2c"\ncommunity = "\xff"\n')
    with pytest.raises(CredentialFileError) as info:
        _provider(install).summaries()
    assert info.value.issues[0].line == 3
    assert info.value.issues[0].reason == "file is not valid UTF-8"
    deep = "a = " + "[" * 5000 + "]" * 5000 + "\n"
    assert _issues(install, deep) == [" TOML syntax error: keys or values are nested too deeply"]


@pytest.mark.parametrize(
    ("message", "phrase"),
    [
        ("Invalid value", "TOML syntax error: invalid value"),
        ("Illegal character '\\x01'", "TOML syntax error: illegal character"),
        ("Found invalid character 'q'", "TOML syntax error: invalid character"),
        (
            "Cannot declare ('credentials', 'c') twice",
            "TOML syntax error: a table or key is declared twice",
        ),
        ("Expected '='", "TOML syntax error"),
        ("Something new in a later Python", "TOML syntax error"),
    ],
)
def test_syntax_phrases_never_carry_file_text(message: str, phrase: str) -> None:
    """Only fixed tomllib messages pass through; interpolated ones become fixed phrases.

    Args:
        message: A ``TOMLDecodeError.msg``.
        phrase: Expected operator phrase.

    Returns:
        None

    Raises:
        None
    """
    assert syntax_phrase(message) == phrase


def test_key_lines_handles_dotted_quoted_and_multiline() -> None:
    """The locator finds headers and keys and ignores multi-line string bodies.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    lines = KeyLines(
        '[credentials."core.v2c"]\n'
        'version = "2c"\n'
        'note = """\n'
        "inner = not a key\n"
        '"""\n'
        "a . 'b' = 1\n"
        "[[arr]]\n"
        "x = 1\n"
    )
    assert lines.find(("credentials", "core.v2c")) == 1
    assert lines.find(("credentials", "core.v2c", "version")) == 2
    assert lines.find(("credentials", "core.v2c", "inner")) is None
    assert lines.find(("credentials", "core.v2c", "a", "b")) == 6
    assert lines.find(("arr", "x")) == 8


def _rewrite(path: Path, text: str) -> None:
    """Rewrite a file in place and move its mtime forward, as an editor save would.

    Args:
        path: The file.
        text: New content.

    Returns:
        None

    Raises:
        None
    """
    before = path.stat().st_mtime_ns
    path.write_text(text)
    os.utime(path, ns=(before + 10**9, before + 10**9))


def test_rotation_in_place_and_by_rename(install: Install, fake: FakeSecrets) -> None:
    """A new community is picked up after an in-place edit and after an atomic rename.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    ref = CredentialReference("network-v2c")
    install.write(v2c_table("network-v2c", fake.community))
    provider = _provider(install)
    first = provider.resolve(ref)
    assert provider.resolve(ref) is first  # unchanged file: no re-read
    _rewrite(install.path, v2c_table("network-v2c", fake.auth))
    assert provider.resolve(ref).secrets() == (Secret(fake.auth),)
    staged = install.secrets_dir / ".credentials.toml.new"
    staged.write_text(v2c_table("network-v2c", fake.privacy))
    staged.chmod(0o640)
    staged.replace(install.path)
    assert provider.resolve(ref).secrets() == (Secret(fake.privacy),)
    assert Secret(fake.community) not in provider.known_secrets()


def test_failed_reload_drops_old_secrets(install: Install, fake: FakeSecrets) -> None:
    """A broken, unsafe, or deleted file never leaves the old secrets in service.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    ref = CredentialReference("network-v2c")
    install.write(v2c_table("network-v2c", fake.community))
    provider = _provider(install)
    provider.resolve(ref)
    _rewrite(install.path, "[credentials.network-v2c\n")
    with pytest.raises(CredentialFileError):
        provider.resolve(ref)
    assert provider.known_secrets() == ()
    install.write(v2c_table("network-v2c", fake.community))
    provider.resolve(ref)
    install.path.chmod(0o644)
    with pytest.raises(CredentialPermissionError):
        provider.resolve(ref)
    install.path.chmod(0o640)
    provider.resolve(ref)
    install.path.unlink()
    with pytest.raises(CredentialFileMissingError):
        provider.resolve(ref)
    assert provider.known_secrets() == ()


def test_forget_drops_everything(install: Install, fake: FakeSecrets) -> None:
    """``forget()`` empties the provider; the next call reads the file again.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = _provider(install)
    provider.resolve(CredentialReference("network-v2c"))
    assert provider.known_secrets()
    provider.forget()
    assert provider.known_secrets() == ()
    assert provider.warnings == ()
    assert provider.secret("metadata-dsn").reveal() == fake.dsn


def test_v3_table_levels_round_trip(install: Install, fake: FakeSecrets) -> None:
    """A v3 table with authNoPriv must not carry privacy fields (SNMP-002).

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    text = v3_table("v3", fake.auth, fake.privacy, level="authNoPriv")
    assert _issues(install, text) == [
        "7: credentials.v3.privacy_protocol: must not be set for authNoPriv"
    ]
