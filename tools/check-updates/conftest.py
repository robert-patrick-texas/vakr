# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for check-updates: offline fetcher and recorded responses.

The tests must never touch the network (TOOLS-001 tests are unit tests),
so every HTTPS request, GitHub API call, and local command goes through
``FakeFetcher``, which answers from dictionaries and fails like the real
``Fetcher`` for anything not recorded. ``testdata/`` holds trimmed
recordings of the real APIs taken on 2026-10-20, with a few synthetic
entries (a pre-release, a yanked release, a draft) added to exercise
filtering; each test says which.

This directory is also put on ``sys.path`` so the tests import sibling
modules the way ``check_updates.py`` does at runtime, whether pytest runs
from the repository root or from inside this directory.

Dependencies:
    pytest: Fixtures.
    updates_fetch: The ``Fetcher`` interface the fake implements.

Safety:
    No network, no credentials; commands are canned results, not run.

Example:
    $ uv run pytest tools/check-updates
"""

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from updates_fetch import CommandResult, Fetcher, FetchError

TESTDATA = Path(__file__).with_name("testdata")


class FakeFetcher(Fetcher):
    """A ``Fetcher`` that replays canned responses and records every request."""

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        commands: dict[tuple[str, ...], CommandResult | Exception] | None = None,
    ) -> None:
        """Store the canned responses.

        Args:
            responses: URL (GitHub paths as ``https://api.github.com/PATH``)
                to decoded JSON, or to an exception to raise.
            commands: Argument tuple to a result, or to an exception to raise.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(timeout=1, use_gh=False)
        self.responses = responses or {}
        self.commands = commands or {}
        self.requests: list[str] = []
        self.commands_run: list[tuple[tuple[str, ...], Path | None]] = []

    def get_json(self, url: str) -> Any:
        """Return the canned document for ``url``.

        Args:
            url: Requested URL.

        Returns:
            Any: The recorded JSON value.

        Raises:
            FetchError: For URLs without a recording, or a recorded failure.
        """
        self.requests.append(url)
        found = self.responses.get(url, FetchError(f"{url}: no recorded response"))
        if isinstance(found, Exception):
            raise found
        return found

    def github_json(self, path: str) -> Any:
        """Answer GitHub API paths from the same table as HTTPS URLs.

        Args:
            path: API path such as ``repos/o/r/releases?per_page=10``.

        Returns:
            Any: The recorded JSON value.

        Raises:
            FetchError: For paths without a recording.
        """
        return self.get_json(f"https://api.github.com/{path}")

    def run(self, argv: Sequence[str], cwd: Path | None = None) -> CommandResult:
        """Return the canned result for a command.

        Args:
            argv: Command and arguments.
            cwd: Working directory, recorded for assertions.

        Returns:
            CommandResult: The recorded result.

        Raises:
            FetchError: As the real fetcher does for a missing program, or a
                recorded failure.
        """
        self.commands_run.append((tuple(argv), cwd))
        found = self.commands.get(tuple(argv), FetchError(f"{argv[0]} is not installed"))
        if isinstance(found, Exception):
            raise found
        return found


def load(name: str) -> Any:
    """Read one recorded JSON fixture from ``testdata/``.

    Args:
        name: File name, such as ``go-dl.json``.

    Returns:
        Any: The decoded document.

    Raises:
        OSError: If the fixture is missing.
    """
    return json.loads((TESTDATA / name).read_text(encoding="utf-8"))


@pytest.fixture
def recorded() -> Callable[[str], Any]:
    """Provide the fixture loader to tests.

    Args:
        None

    Returns:
        Callable[[str], Any]: ``load``.

    Raises:
        None
    """
    return load


@pytest.fixture
def fake_fetcher() -> type[FakeFetcher]:
    """Provide the offline fetcher class so tests can build one per scenario.

    Args:
        None

    Returns:
        type[FakeFetcher]: The class.

    Raises:
        None
    """
    return FakeFetcher
