"""Plain helpers for the credential tests: fake secrets, files, and failure capture.

Every secret value is generated at test time with ``secrets.token_hex``, so
nothing resembling a credential is committed and each value is unique enough
that finding it in output can only mean a leak (CRED-002). Fixtures that use
these helpers live in the sibling ``conftest.py``.

Dependencies:
    secrets: Unique fake secret values.
    vakr.credentials: Types under test.

Example:
    >>> fake = FakeSecrets.generate()
    >>> fake.community != fake.auth
    True
"""

import os
import secrets
import stat
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from vakr.credentials.policy import FilePolicy


@dataclass(frozen=True)
class FakeSecrets:
    """A set of unique fake secret values for one test.

    Attributes:
        community: SNMPv2c community.
        auth: SNMPv3 authentication passphrase.
        privacy: SNMPv3 privacy passphrase.
        dsn: Database DSN stored under ``[secrets]``.
    """

    community: str
    auth: str
    privacy: str
    dsn: str

    @classmethod
    def generate(cls) -> FakeSecrets:
        """Create fresh values.

        Args:
            None

        Returns:
            FakeSecrets: Four distinct random hex values.

        Raises:
            None
        """
        return cls(
            community=f"c{secrets.token_hex(12)}",
            auth=f"a{secrets.token_hex(12)}",
            privacy=f"p{secrets.token_hex(12)}",
            dsn=f"sqlite:///x?key={secrets.token_hex(12)}",
        )

    def values(self) -> tuple[str, ...]:
        """Return every value, for leak assertions.

        Args:
            None

        Returns:
            tuple[str, ...]: All four values.

        Raises:
            None
        """
        return (self.community, self.auth, self.privacy, self.dsn)

    def file_text(self) -> str:
        """Render a complete, valid credentials file with these values.

        Args:
            None

        Returns:
            str: TOML with one v2c credential, one v3 credential, and one DSN.

        Raises:
            None
        """
        return (
            f"{v2c_table('network-v2c', self.community)}\n"
            f"{v3_table('network-snmpv3', self.auth, self.privacy)}\n"
            f'[secrets]\nmetadata-dsn = "{self.dsn}"\n'
        )


def v2c_table(name: str, community: str) -> str:
    """Render one ``[credentials.NAME]`` SNMPv2c table.

    Args:
        name: Credential name.
        community: Community value.

    Returns:
        str: TOML text.

    Raises:
        None
    """
    return f'[credentials.{name}]\nversion = "2c"\ncommunity = "{community}"\n'


def v3_table(name: str, auth: str, privacy: str, level: str = "authPriv") -> str:
    """Render one ``[credentials.NAME]`` SNMPv3 table with SHA256/AES128.

    Args:
        name: Credential name.
        auth: Authentication passphrase.
        privacy: Privacy passphrase.
        level: Security level.

    Returns:
        str: TOML text.

    Raises:
        None
    """
    return (
        f'[credentials.{name}]\nversion = "3"\nusername = "netmon"\n'
        f'security_level = "{level}"\nauth_protocol = "SHA256"\nauth_secret = "{auth}"\n'
        f'privacy_protocol = "AES128"\nprivacy_secret = "{privacy}"\n'
    )


@dataclass(frozen=True)
class Install:
    """A relocated installation's secrets directory.

    Attributes:
        secrets_dir: ``<root>/etc/vakr/secrets`` with mode 0750.
        path: ``credentials.toml`` inside it.
    """

    secrets_dir: Path
    path: Path

    @classmethod
    def under(cls, root: Path) -> Install:
        """Create the secrets directory beneath ``root``.

        Args:
            root: Installation prefix (a pytest ``tmp_path``).

        Returns:
            Install: The layout, with the directory created at mode 0750.

        Raises:
            None
        """
        secrets_dir = root / "etc/vakr/secrets"
        secrets_dir.mkdir(parents=True)
        secrets_dir.chmod(0o750)
        return cls(secrets_dir, secrets_dir / "credentials.toml")

    def write(self, text: str | bytes, mode: int = 0o640) -> Path:
        """Write the credentials file and set its mode.

        Args:
            text: File content.
            mode: Permission bits to apply.

        Returns:
            Path: The file.

        Raises:
            None
        """
        data = text.encode() if isinstance(text, str) else text
        self.path.write_bytes(data)
        self.path.chmod(mode)
        return self.path


def own_policy() -> FilePolicy:
    """Return a policy that trusts the test user as if it were root or vakr.

    Args:
        None

    Returns:
        FilePolicy: Current euid as owner, current egid as reader group.

    Raises:
        None
    """
    return FilePolicy(frozenset({os.geteuid()}), os.getegid())


def fake_stat(mode: int, uid: int = 0, gid: int = 0, kind: int = stat.S_IFREG) -> os.stat_result:
    """Build a stat result with chosen ownership, to test owners we cannot create.

    Args:
        mode: Permission bits.
        uid: Owner uid.
        gid: Owner gid.
        kind: File type bits.

    Returns:
        os.stat_result: Metadata with only mode, uid, and gid meaningful.

    Raises:
        None
    """
    return os.stat_result((kind | mode, 1, 1, 1, uid, gid, 0, 0, 0, 0))


def _captured_locals(exc: BaseException) -> list[str]:
    """Render the local variables of every frame in an exception chain.

    Frames of this helper module and of the test's own lambdas are skipped:
    their locals are the test's fake values, not the code under test.

    Args:
        exc: The exception.

    Returns:
        list[str]: ``function: {locals}`` per frame, causes and contexts included.

    Raises:
        None
    """
    rendered = []
    tb: traceback.TracebackException | None = traceback.TracebackException.from_exception(
        exc, capture_locals=True
    )
    while tb is not None:
        for frame in tb.stack:
            if frame.name != "<lambda>" and frame.filename != __file__:
                rendered.append(f"{frame.name}: {frame.locals}")
        tb = tb.__cause__ or tb.__context__
    return rendered


def failure_text(action: Callable[[], object]) -> str:
    """Run an action expected to fail and render the failure every way it may be seen.

    Args:
        action: Callable that raises.

    Returns:
        str: ``str`` and ``repr`` of the exception, the formatted traceback
        with chained causes and notes, and the local variables of every frame
        of the code under test, as a ``capture_locals`` traceback shows them.

    Raises:
        AssertionError: If the action does not raise.
    """
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - every failure type is rendered
        plain = "".join(traceback.format_exception(exc))
        return "\n".join((str(exc), repr(exc), plain, *_captured_locals(exc)))
    raise AssertionError("expected the action to raise")


def assert_no_secret(text: str, values: Iterable[str]) -> None:
    """Assert that no fake secret value appears in ``text`` (CRED-002).

    Args:
        text: Captured output.
        values: Secret values that must not appear.

    Returns:
        None

    Raises:
        AssertionError: Naming the index of the leaked value, never the value.
    """
    for index, value in enumerate(values):
        assert value not in text, f"secret #{index} leaked"
