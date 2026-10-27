"""Guard tests: nothing in the repository delays a dependency release by its age (ADR-036).

vakr adopts the newest release of every dependency however recently it was
published, and holds one back only after it fails vakr's tests (TOOLS-002,
TOOLS-004). Each package tool has its own way to wait for releases to age:
Dependabot applies a 3-day cooldown unless told otherwise, npm has
``min-release-age``, and uv has ``exclude-newer``. These tests pin the
settings that turn every one of them off, so a later edit cannot bring a
delay back unnoticed (TOOLS-T10).

Dependencies:
    tomllib: Reads ``pyproject.toml`` and the check-updates configuration.

Example:
    $ uv run pytest tests/unit/test_dependency_currency.py
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_dependabot_entry_turns_the_cooldown_off() -> None:
    """Each ecosystem excludes every dependency from Dependabot's default cooldown (TOOLS-T10).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If an entry lacks ``cooldown: exclude: ["*"]``.
    """
    text = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    entries = re.split(r"^  - package-ecosystem:", text, flags=re.MULTILINE)[1:]
    assert len(entries) == 3
    for entry in entries:
        assert re.search(r'^    cooldown:\n      exclude: \["\*"\]$', entry, re.MULTILINE), entry


def test_npm_accepts_releases_of_any_age() -> None:
    """``web/.npmrc`` sets ``min-release-age=0``, which outranks a developer's ~/.npmrc (TOOLS-T10).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the setting is missing or non-zero.
    """
    lines = (ROOT / "web/.npmrc").read_text(encoding="utf-8").splitlines()
    settings = [line.replace(" ", "") for line in lines if line.strip().startswith("min-release")]
    assert settings == ["min-release-age=0"]


def test_uv_never_excludes_new_releases() -> None:
    """No uv cutoff in the project, and the lockfile check overrides any local one (TOOLS-T10).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a cutoff is configured or the override is gone.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    uv = pyproject["tool"]["uv"]
    assert not {"exclude-newer", "exclude-newer-package"} & set(uv)
    config = (ROOT / "tools/check-updates/check-updates.toml").read_text(encoding="utf-8")
    command = tomllib.loads(config)["lockfile"]["command"]
    cutoff = command[command.index("--exclude-newer") + 1]
    assert cutoff.startswith("2999-")
