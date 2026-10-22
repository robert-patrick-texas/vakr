# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for loading ``release.toml`` (section 29 portability, ADR-026 settings).

The release tool takes every project-specific value from its config. These
tests prove the shipped vakr config is valid and uses the agreed signing
namespace and identity, and that typos or wrong types stop a release
instead of being ignored.

Dependencies:
    pytest: Test runner and ``tmp_path``.
    release_config: Module under test.

Safety:
    Writes only beneath ``tmp_path``.

Example:
    $ uv run pytest tools/release/test_release_config.py
"""

from pathlib import Path
from typing import Any

import pytest
from release_config import EXAMPLE, ConfigError, ReleaseConfig, load_config
from release_version import ReleaseVersion

SHIPPED = Path(__file__).with_name("release.toml")


def test_shipped_config_is_valid_and_uses_vakr_release_namespace() -> None:
    """ADR-026: vakr signs with namespace and identity ``vakr-release``.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the shipped config is invalid or differs.
    """
    root = Path("/repo")
    cfg = load_config(SHIPPED, root)
    assert (cfg.namespace, cfg.identity) == ("vakr-release", "vakr-release")
    assert cfg.allowed_signers == root / "packaging/release-signers"
    assert cfg.signing_key == Path("~/.config/vakr-release/release_ed25519").expanduser()
    assert cfg.prerelease == "rc-or-major-zero"
    assert cfg.build_command == ("make", "deb")
    assert cfg.ci_ignore_checks == ("Dependabot",)
    assert ReleaseConfig.from_mapping(EXAMPLE, root).ci_ignore_checks == ()


def test_placeholders_expand_to_each_version_form() -> None:
    """Artifact patterns can name the Debian version and tag (VER-002).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a placeholder expands wrongly.
    """
    cfg = load_config(SHIPPED, Path("/repo"))
    version = ReleaseVersion.parse("7.3.0-rc.2")
    assert cfg.expand(cfg.artifacts[0], version) == "dist/vakr_7.3.0~rc.2_amd64.deb"
    assert cfg.expand(cfg.output_dir, version) == "dist/release/v7.3.0-rc.2"
    assert cfg.expand("{pep440}/{version}", version) == "7.3.0rc2/7.3.0-rc.2"
    with pytest.raises(ConfigError, match="placeholder"):
        cfg.expand("dist/{unknown}", version)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"signing_ky": "typo"}, "unknown release config keys: signing_ky"),
        ({"repo": None}, "repo must be a non-empty string"),
        ({"repo": "no-slash"}, "OWNER/NAME"),
        ({"prerelease": "sometimes"}, "prerelease must be one of"),
        ({"artifacts": []}, "must not be empty"),
        ({"build_command": "make deb"}, "build_command must be a list"),
        ({"optional_artifacts": [""]}, "optional_artifacts must be a list"),
        ({"ci_ignore_checks": "Dependabot"}, "ci_ignore_checks must be a list"),
    ],
)
def test_invalid_configs_are_rejected(change: dict[str, Any], message: str) -> None:
    """Typos and wrong types stop the release with a precise message.

    Args:
        change: Keys to add, replace, or (with ``None``) remove.
        message: Expected fragment of the error.

    Returns:
        None

    Raises:
        AssertionError: If the config is accepted or the message differs.
    """
    data = {**EXAMPLE, **change}
    data = {key: value for key, value in data.items() if value is not None}
    with pytest.raises(ConfigError, match=message):
        ReleaseConfig.from_mapping(data, Path("/repo"))


def test_relative_paths_resolve_under_root_and_tilde_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative paths are repository-relative; ``~`` is the releaser's home.

    Args:
        monkeypatch: Fixture used to point ``HOME`` at a temporary directory.
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If a path resolves elsewhere.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    data = {**EXAMPLE, "signing_key": "~/keys/release", "changelog": "docs/CHANGES.md"}
    cfg = ReleaseConfig.from_mapping(data, tmp_path / "repo")
    assert cfg.signing_key == tmp_path / "home/keys/release"
    assert cfg.changelog == tmp_path / "repo/docs/CHANGES.md"


def test_unreadable_or_malformed_files_are_config_errors(tmp_path: Path) -> None:
    """A missing or broken config file is reported, not raised as a traceback.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If either case escapes as another exception type.
    """
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "absent.toml", tmp_path)
    broken = tmp_path / "broken.toml"
    broken.write_text("repo = [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(broken, tmp_path)
