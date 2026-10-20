# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Pytest setup for the release tool: sibling imports and throwaway git repositories.

``release.py`` runs as a script, so Python puts its directory on
``sys.path`` and the ``release_*`` modules import each other by bare name.
The repository runs pytest with ``--import-mode=importlib``, which does not
do that, so this file adds the directory once. Keeping it here, rather than
in the shared ``pyproject.toml``, keeps the tool self-contained (section 29).
The fixtures build a work repository plus a local bare "origin" under
``tmp_path`` so release checks run against real git without any network.

Dependencies:
    pytest, subprocess: Fixtures and the real ``git`` binary.
    release_proc: Base class of the fake runner that stands in for ``gh``
        and ``git push``.

Safety:
    Every repository lives under ``tmp_path``. Global and system git
    configuration are disabled so a developer's signing or hook settings
    cannot leak into, or hang, a test.

Example:
    $ uv run pytest tools/release
"""

import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from release_proc import Result, Runner  # noqa: E402 - needs the path set above


class FakeRunner(Runner):
    """Runner that fakes chosen commands and runs everything else for real.

    Attributes:
        responses: Maps an argv prefix to canned stdout (``str``, or any
            JSON-serialisable value) or to an exception to raise.
        calls: Every argv seen, faked or not, in order.
    """

    def __init__(self, cwd: Path, responses: Mapping[tuple[str, ...], Any]) -> None:
        """Create the fake.

        Args:
            cwd: Working directory for real commands.
            responses: Canned replies keyed by argv prefix.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(cwd)
        self.responses = dict(responses)
        self.calls: list[list[str]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = True,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        stdin: Path | None = None,
    ) -> Result:
        """Record the call, then fake it if a prefix matches, else run it.

        Args:
            argv: Command line.
            capture: Passed to the real runner.
            check: Passed to the real runner.
            env: Passed to the real runner.
            stdin: Passed to the real runner.

        Returns:
            Result: The canned or real result.

        Raises:
            Exception: The canned exception, when one is configured.
        """
        self.calls.append(list(argv))
        for prefix, reply in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if isinstance(reply, Exception):
                    raise reply
                return Result(0, reply if isinstance(reply, str) else json.dumps(reply))
        return super().run(argv, capture=capture, check=check, env=env, stdin=stdin)


@pytest.fixture
def fake_runner() -> type[FakeRunner]:
    """Provide the ``FakeRunner`` class without importing this conftest by name.

    Args:
        None

    Returns:
        type[FakeRunner]: Call it as ``fake_runner(cwd, responses)``.

    Raises:
        None
    """
    return FakeRunner


CHANGELOG = "# Changelog\n\n## Unreleased\n\n## 1.2.3 - 2026-10-20\n\n### Added\n\n- Thing.\n"
"""Changelog used by the throwaway repository; it has a 1.2.3 section."""


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return its standard output.

    Args:
        repo: Repository directory.
        *args: Git arguments.

    Returns:
        str: Captured standard output.

    Raises:
        subprocess.CalledProcessError: If git fails.
    """
    argv = ["git", "-C", str(repo), *args]
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout  # noqa: S603


@pytest.fixture
def run_git() -> Callable[..., str]:
    """Provide ``git`` to tests without importing this conftest by name.

    Args:
        None

    Returns:
        Callable[..., str]: ``git(repo, *args)``.

    Raises:
        None
    """
    return git


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate git from the developer's configuration and give it an identity.

    Args:
        monkeypatch: Pytest fixture for environment changes.

    Returns:
        None

    Raises:
        None
    """
    settings = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("VERSION_OVERRIDE", raising=False)


@pytest.fixture
def repo(tmp_path: Path, git_env: None) -> Path:  # noqa: ARG001 - fixture ordering
    """Create a committed work repository on ``main`` with a bare ``origin``.

    The tree holds ``VERSION`` (1.2.3), a changelog with that section, and
    a placeholder signers file, and ``main`` is pushed to ``origin``.

    Args:
        tmp_path: Per-test temporary directory.
        git_env: Fixture that isolates git configuration.

    Returns:
        Path: The work repository.

    Raises:
        subprocess.CalledProcessError: If git fails.
    """
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)  # noqa: S603, S607
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)  # noqa: S603, S607
    (work / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (work / "signers").write_text("# placeholder: no keys yet\n", encoding="utf-8")
    (work / ".gitignore").write_text("dist/\n", encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "initial")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    return work
