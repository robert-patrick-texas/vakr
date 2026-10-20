"""Fixtures for the credential tests: fake secrets and a relocated install.

Dependencies:
    tests.unit.credentials.credentials_testkit: Plain helpers.

Example:
    $ uv run pytest tests/unit/credentials
"""

from pathlib import Path

import pytest

from tests.unit.credentials.credentials_testkit import FakeSecrets, Install, own_policy
from vakr.credentials.policy import FilePolicy


@pytest.fixture
def fake() -> FakeSecrets:
    """Provide unique fake secret values for one test.

    Args:
        None

    Returns:
        FakeSecrets: Freshly generated values.

    Raises:
        None
    """
    return FakeSecrets.generate()


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    """Relocate the installation under ``tmp_path`` with ``VAKR_ROOT``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Install: The secrets directory (mode 0750) and file path.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    return Install.under(tmp_path)


@pytest.fixture
def host_policy(monkeypatch: pytest.MonkeyPatch) -> FilePolicy:
    """Make the host policy trust the test user, as if it were root or vakr.

    Args:
        monkeypatch: Replaces ``FilePolicy.for_host``.

    Returns:
        FilePolicy: The policy now returned by ``FilePolicy.for_host()``.

    Raises:
        None
    """
    policy = own_policy()
    monkeypatch.setattr(FilePolicy, "for_host", classmethod(lambda _cls: policy))
    return policy
