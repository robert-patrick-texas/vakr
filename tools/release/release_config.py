# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Load and validate the release tool's project configuration (``release.toml``).

The release tool is portable (blueprint section 29): repository, package
name, signing key, signer identity, build command, and artifact patterns are
data, not constants. This module turns the TOML file into a typed, immutable
``ReleaseConfig`` and rejects unknown or mistyped keys, because a typo in a
release config should stop a release, not silently sign the wrong files.

Dependencies:
    tomllib: Standard-library TOML parser.

Safety:
    Read-only. The signing key path is only recorded here; the key itself is
    never read by this module.

Example:
    >>> cfg = ReleaseConfig.from_mapping(EXAMPLE, Path("/repo"))
    >>> cfg.namespace, cfg.allowed_signers
    ('example-release', PosixPath('/repo/signers'))
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from release_version import PRERELEASE_RULES, ReleaseVersion

_STRINGS = (
    "repo",
    "package",
    "version_file",
    "changelog",
    "branch",
    "remote",
    "signing_key",
    "namespace",
    "identity",
    "allowed_signers",
    "output_dir",
    "prerelease",
)
_LISTS = ("build_command", "artifacts", "optional_artifacts")
_OPTIONAL = ("build_tools", "node_version_file", "ci_ignore_checks", "update_check")
"""Keys that may be absent: build-host tools, a Node pin, check runs that never block, and
the dependency currency command."""

EXAMPLE: dict[str, Any] = {
    **{key: key for key in _STRINGS},
    "repo": "owner/example",
    "namespace": "example-release",
    "allowed_signers": "signers",
    "prerelease": "rc",
    "build_command": ["make", "dist"],
    "artifacts": ["dist/{package}_{deb_version}_amd64.deb"],
    "optional_artifacts": [],
}
"""A minimal valid configuration, used by the doctest and unit tests."""


class ConfigError(ValueError):
    """The release configuration is missing, malformed, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ReleaseConfig:
    """Validated release settings with paths resolved against the repository root.

    Attributes:
        root: Repository root the tool runs in.
        repo: GitHub ``OWNER/NAME``.
        package: Package name for artifact patterns.
        version_file: The single version source (VER-001).
        changelog: File whose ``## <version>`` section becomes release notes.
        branch: Only branch releases may be cut from.
        remote: Git remote that receives the tag.
        signing_key: Private SSH signing key (passphrase-protected).
        namespace: ``ssh-keygen -Y`` signature namespace.
        identity: Principal expected in ``allowed_signers``.
        allowed_signers: Trusted signers file shipped in the package.
        build_command: Argument list that builds the artifacts.
        artifacts: Required artifact glob patterns, with placeholders.
        optional_artifacts: Artifact patterns that may match nothing.
        output_dir: Release staging directory pattern, with placeholders.
        prerelease: One of ``PRERELEASE_RULES``.
        build_tools: Programs the build host must have, each asked for
            ``--version`` in preflight (ADR-035: ``node``, ``npm``).
        node_version_file: File whose version (a major such as ``24``)
            the installed ``node`` must match; ``None`` skips the check.
        ci_ignore_checks: Check-run names the CI check disregards.
        update_check: Command run in preflight that must exit 0, so every
            dependency is current at release time or held by a recorded
            exception (TOOLS-005); empty skips the check.
    """

    root: Path
    repo: str
    package: str
    version_file: Path
    changelog: Path
    branch: str
    remote: str
    signing_key: Path
    namespace: str
    identity: str
    allowed_signers: Path
    build_command: tuple[str, ...]
    artifacts: tuple[str, ...]
    optional_artifacts: tuple[str, ...]
    output_dir: str
    prerelease: str
    build_tools: tuple[str, ...] = ()
    node_version_file: Path | None = None
    ci_ignore_checks: tuple[str, ...] = ()
    update_check: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], root: Path) -> ReleaseConfig:
        """Validate parsed TOML and build the configuration.

        Args:
            data: Top-level table of ``release.toml``.
            root: Repository root; relative paths are resolved against it.

        Returns:
            ReleaseConfig: The validated configuration.

        Raises:
            ConfigError: On unknown, missing, or mistyped keys, an unknown
                prerelease rule, or a malformed ``repo``.
        """
        unknown = sorted(set(data) - set(_STRINGS) - set(_LISTS) - set(_OPTIONAL))
        if unknown:
            raise ConfigError(f"unknown release config keys: {', '.join(unknown)}")
        text = {key: _string(data, key) for key in _STRINGS}
        lists = {key: _string_list(data, key) for key in _LISTS}
        if text["prerelease"] not in PRERELEASE_RULES:
            raise ConfigError(f"prerelease must be one of {PRERELEASE_RULES}")
        if text["repo"].count("/") != 1:
            raise ConfigError("repo must be OWNER/NAME")
        if not lists["build_command"] or not lists["artifacts"]:
            raise ConfigError("build_command and artifacts must not be empty")
        tools = _string_list(data, "build_tools") if "build_tools" in data else ()
        ignore = _string_list(data, "ci_ignore_checks") if "ci_ignore_checks" in data else ()
        pin = _string(data, "node_version_file") if data.get("node_version_file") else None
        updates = _string_list(data, "update_check") if data.get("update_check") else ()
        return cls(
            root=root,
            repo=text["repo"],
            package=text["package"],
            version_file=_resolve(root, text["version_file"]),
            changelog=_resolve(root, text["changelog"]),
            branch=text["branch"],
            remote=text["remote"],
            signing_key=_resolve(root, text["signing_key"]),
            namespace=text["namespace"],
            identity=text["identity"],
            allowed_signers=_resolve(root, text["allowed_signers"]),
            build_command=lists["build_command"],
            artifacts=lists["artifacts"],
            optional_artifacts=lists["optional_artifacts"],
            output_dir=text["output_dir"],
            prerelease=text["prerelease"],
            build_tools=tools,
            node_version_file=_resolve(root, pin) if pin else None,
            ci_ignore_checks=ignore,
            update_check=updates,
        )

    def placeholders(self, version: ReleaseVersion) -> dict[str, str]:
        """Return the values substituted into artifact and output patterns.

        Args:
            version: The version being released.

        Returns:
            dict[str, str]: ``package``, ``version``, ``pep440``,
            ``deb_version``, and ``tag``.

        Raises:
            None
        """
        return {
            "package": self.package,
            "version": version.semver,
            "pep440": version.pep440,
            "deb_version": version.debian,
            "tag": version.tag,
        }

    def expand(self, pattern: str, version: ReleaseVersion) -> str:
        """Fill the placeholders of one configured pattern.

        Args:
            pattern: Artifact or output pattern such as ``dist/{package}_*.deb``.
            version: The version being released.

        Returns:
            str: The pattern with placeholders replaced.

        Raises:
            ConfigError: If the pattern names an unknown placeholder.
        """
        try:
            return pattern.format_map(self.placeholders(version))
        except (KeyError, IndexError, ValueError) as exc:
            raise ConfigError(f"bad placeholder in {pattern!r}: {exc}") from exc


def load_config(path: Path, root: Path) -> ReleaseConfig:
    """Read ``release.toml`` and validate it.

    Args:
        path: Configuration file.
        root: Repository root for resolving relative paths.

    Returns:
        ReleaseConfig: The validated configuration.

    Raises:
        ConfigError: If the file is missing, not valid TOML, or invalid.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read release config {path}: {exc.strerror}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return ReleaseConfig.from_mapping(data, root)


def _resolve(root: Path, value: str) -> Path:
    """Resolve a configured path against the repository root.

    Args:
        root: Repository root.
        value: Path from the config; ``~`` expands to the user's home.

    Returns:
        Path: ``value`` itself when absolute after expansion, else under ``root``.

    Raises:
        None
    """
    # Joining an absolute path discards root, which is what an absolute or
    # home-relative key path (the signing key) needs.
    return root / Path(value).expanduser()


def _string(data: Mapping[str, Any], key: str) -> str:
    """Return a required, non-empty string value.

    Args:
        data: Parsed configuration table.
        key: Key to read.

    Returns:
        str: The value.

    Raises:
        ConfigError: If the key is missing, empty, or not a string.
    """
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _string_list(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """Return a required list of non-empty strings.

    Args:
        data: Parsed configuration table.
        key: Key to read.

    Returns:
        tuple[str, ...]: The values, in order.

    Raises:
        ConfigError: If the key is missing or holds anything but strings.
    """
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{key} must be a list of non-empty strings")
    return tuple(value)
