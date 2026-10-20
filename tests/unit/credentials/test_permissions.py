"""CRED-001: credentials-file permission matrix in strict and development modes.

Real files in ``tmp_path`` get each mode with ``os.chmod``; owners the test
user cannot create (root, another uid, another group) are exercised with a
policy that excludes the test user, or with constructed ``stat`` results.
Strict mode must refuse every unsafe case and development mode must load
the file with a clear warning (SEC-002, SEC-016).

Dependencies:
    os, stat: Modes, FIFOs, and symlinks.
    pytest: Parametrised matrix and log capture.

Example:
    $ uv run pytest tests/unit/credentials/test_permissions.py
"""

import logging
import os
import stat
from pathlib import Path

import pytest

from tests.unit.credentials.credentials_testkit import FakeSecrets, Install, fake_stat, own_policy
from vakr.credentials import policy as policy_module
from vakr.credentials.errors import (
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialPermissionError,
)
from vakr.credentials.policy import FilePolicy, directory_problems, file_problems
from vakr.credentials.protected import read_protected
from vakr.credentials.provider import FileCredentialProvider
from vakr.credentials.vocabulary import CredentialReference

REF = CredentialReference("network-v2c")
SAFE_FILE_MODES = (0o600, 0o640, 0o400, 0o440)
UNSAFE_FILE_MODES = {
    0o644: "readable by others",
    0o604: "readable by others",
    0o660: "writable by group or others",
    0o620: "writable by group or others",
    0o602: "writable by group or others",
    0o666: "writable by group or others",
    0o664: "readable by others",
}


def _provider(
    install: Install, *, development: bool = False, **kw: object
) -> FileCredentialProvider:
    """Build a provider for the test install that trusts the test user.

    Args:
        install: The relocated install.
        development: Development mode flag.
        **kw: ``policy`` override.

    Returns:
        FileCredentialProvider: The provider.

    Raises:
        None
    """
    policy = kw.get("policy", own_policy())
    assert isinstance(policy, FilePolicy)
    return FileCredentialProvider(install.secrets_dir, development=development, policy=policy)


@pytest.mark.parametrize("mode", SAFE_FILE_MODES)
def test_cred_001_safe_modes_load_in_strict_mode(
    install: Install, fake: FakeSecrets, mode: int
) -> None:
    """0600, 0640, 0400, and 0440 owned by root/vakr with group vakr are accepted.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        mode: File mode under test.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text(), mode)
    provider = _provider(install)
    assert provider.resolve(REF).name == "network-v2c"
    assert provider.warnings == ()


@pytest.mark.parametrize(("mode", "phrase"), UNSAFE_FILE_MODES.items())
def test_cred_001_unsafe_modes(
    install: Install,
    fake: FakeSecrets,
    caplog: pytest.LogCaptureFixture,
    mode: int,
    phrase: str,
) -> None:
    """Strict mode refuses group/other write and other read; development mode warns.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        caplog: Captures the development-mode warning.
        mode: File mode under test.
        phrase: Expected problem phrase.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text(), mode)
    with pytest.raises(CredentialPermissionError, match=phrase) as info:
        _provider(install).resolve(REF)
    assert f"mode {mode:04o}" in str(info.value)
    dev = _provider(install, development=True)
    with caplog.at_level(logging.WARNING, logger="vakr.credentials.provider"):
        assert dev.resolve(REF).name == "network-v2c"
    assert any(phrase in w for w in dev.warnings)
    assert "development mode" in caplog.text
    assert "strict mode refuses this" in caplog.text


@pytest.mark.parametrize(
    ("mode", "ok"),
    [(0o750, True), (0o700, True), (0o755, True), (0o770, False), (0o757, False), (0o777, False)],
)
def test_cred_001_directory_must_not_be_group_or_world_writable(
    install: Install, fake: FakeSecrets, mode: int, ok: bool
) -> None:
    """A writable secrets directory would let others replace the file.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        mode: Directory mode under test.
        ok: Whether strict mode accepts it.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    install.secrets_dir.chmod(mode)
    if ok:
        assert _provider(install).resolve(REF)
        return
    with pytest.raises(CredentialPermissionError, match=r"directory .* is writable"):
        _provider(install).resolve(REF)
    assert _provider(install, development=True).resolve(REF)


def test_cred_001_wrong_owner(install: Install, fake: FakeSecrets) -> None:
    """A file owned by anyone but root or vakr is refused in strict mode, warned in dev.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text(), 0o600)
    strangers = FilePolicy(frozenset({0, os.geteuid() + 4242}), os.getegid())
    with pytest.raises(CredentialPermissionError, match="must be owned by root or vakr"):
        _provider(install, policy=strangers).resolve(REF)
    dev = _provider(install, development=True, policy=strangers)
    assert dev.resolve(REF)
    assert any("owned by uid" in w for w in dev.warnings)


def test_cred_001_group_other_than_vakr_may_not_read(install: Install, fake: FakeSecrets) -> None:
    """0640 is safe only when the group is vakr; 0600 is safe with any group.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text(), 0o640)
    other_group = FilePolicy(frozenset({os.geteuid()}), os.getegid() + 4242)
    with pytest.raises(CredentialPermissionError, match="only group vakr may read"):
        _provider(install, policy=other_group).resolve(REF)
    install.path.chmod(0o600)
    assert _provider(install, policy=other_group).resolve(REF)


def test_cred_001_symlink_refused_in_strict_followed_in_dev(
    install: Install, fake: FakeSecrets
) -> None:
    """A symlink is refused in strict mode and followed with a warning in development mode.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    real = install.secrets_dir / "real.toml"
    real.write_text(fake.file_text())
    real.chmod(0o640)
    install.path.symlink_to(real)
    with pytest.raises(CredentialPermissionError, match="symbolic link"):
        _provider(install).resolve(REF)
    dev = _provider(install, development=True)
    assert dev.resolve(REF)
    assert any("symbolic link" in w for w in dev.warnings)


@pytest.mark.parametrize("development", [False, True])
def test_cred_001_non_regular_files_are_always_refused(install: Install, development: bool) -> None:
    """A FIFO or directory has nothing to read, so even development mode refuses it.

    Args:
        install: The relocated install.
        development: Mode under test.

    Returns:
        None

    Raises:
        None
    """
    os.mkfifo(install.path, 0o600)
    with pytest.raises(CredentialPermissionError, match="not a regular file"):
        _provider(install, development=development).resolve(REF)


def test_cred_001_missing_and_unreadable(install: Install, fake: FakeSecrets) -> None:
    """A missing file or directory is reported as missing; an unreadable file as unreadable.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(CredentialFileMissingError):
        _provider(install).resolve(REF)
    gone = FileCredentialProvider(install.secrets_dir / "nope", policy=own_policy())
    with pytest.raises(CredentialFileMissingError):
        gone.resolve(REF)
    with pytest.raises(CredentialFileMissingError):
        read_protected(gone.path, own_policy(), strict=True)
    if os.geteuid() == 0:
        pytest.skip("root reads mode-000 files")
    install.write(fake.file_text(), 0o000)
    with pytest.raises(CredentialFileUnreadableError, match="Permission denied"):
        _provider(install).resolve(REF)


def test_cred_001_dangling_symlink_in_dev_is_missing(install: Install) -> None:
    """Development mode follows a symlink; a dangling one means the file is missing.

    Args:
        install: The relocated install.

    Returns:
        None

    Raises:
        None
    """
    install.path.symlink_to(install.secrets_dir / "absent.toml")
    with pytest.raises(CredentialFileMissingError):
        _provider(install, development=True).resolve(REF)


def test_cred_001_ownership_rules_on_constructed_metadata() -> None:
    """Owner and group rules for root:vakr, vakr:vakr, and foreign owners (mocked stat).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    vakr_uid, vakr_gid = 990, 990
    policy = FilePolicy(frozenset({0, vakr_uid}), vakr_gid)
    assert file_problems(fake_stat(0o640, 0, vakr_gid), policy) == []
    assert file_problems(fake_stat(0o600, vakr_uid, vakr_gid), policy) == []
    assert file_problems(fake_stat(0o640, 1000, vakr_gid), policy) == [
        "file is owned by uid 1000; it must be owned by root or vakr"
    ]
    assert file_problems(fake_stat(0o640, 0, 4), policy) == [
        "file is readable by group gid 4; only group vakr may read it"
    ]
    link = fake_stat(0o777, 0, vakr_gid, stat.S_IFLNK)
    assert "file is a symbolic link; it must be a regular file" in file_problems(link, policy)
    no_group = FilePolicy(frozenset({0}), None)
    assert file_problems(fake_stat(0o640, 0, 0), no_group)
    directory = fake_stat(0o750, 1000, vakr_gid, stat.S_IFDIR)
    assert directory_problems(Path("/d"), directory, policy) == [
        "directory /d is owned by uid 1000; it must be owned by root or vakr"
    ]


def test_cred_001_host_policy_uses_vakr_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production policy trusts root and the vakr user, and the vakr group.

    Args:
        monkeypatch: Replaces the account lookups.

    Returns:
        None

    Raises:
        None
    """

    class _Account:
        """Stand-in for ``pwd``/``grp`` entries."""

        pw_uid = 990
        gr_gid = 991

    monkeypatch.setattr(policy_module.pwd, "getpwnam", lambda _name: _Account())
    monkeypatch.setattr(policy_module.grp, "getgrnam", lambda _name: _Account())
    assert FilePolicy.for_host() == FilePolicy(frozenset({0, 990}), 991)

    def _missing(name: str) -> None:
        """Simulate a host without the vakr accounts.

        Args:
            name: Account name.

        Returns:
            None

        Raises:
            KeyError: Always.
        """
        raise KeyError(name)

    monkeypatch.setattr(policy_module.pwd, "getpwnam", _missing)
    monkeypatch.setattr(policy_module.grp, "getgrnam", _missing)
    assert FilePolicy.for_host() == FilePolicy(frozenset({0}), None)
