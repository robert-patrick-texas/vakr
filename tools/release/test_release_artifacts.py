# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for artifact collection, ``SHA256SUMS``, and SSH signing (ADR-026, UPD-001).

Production trusts a release only through ``SHA256SUMS`` and its SSH
signature, so these tests use the real ``ssh-keygen`` with throwaway keys:
a good signature verifies, and a tampered checksum file, a wrong namespace,
or a placeholder signers file is rejected exactly as self-update will
reject it.

Dependencies:
    pytest: Test runner and ``tmp_path``.
    hashlib: Independent checksum for comparison.
    release_artifacts, release_config: Modules under test.

Safety:
    Keys are generated per test under ``tmp_path`` without passphrases and
    are deleted with it; no real key is ever read.

Example:
    $ uv run pytest tools/release/test_release_artifacts.py
"""

import hashlib
import re
import shutil
from pathlib import Path

import pytest
import release_artifacts as artifacts
from release_config import EXAMPLE, ReleaseConfig
from release_proc import Runner
from release_version import ReleaseVersion

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

VERSION = ReleaseVersion.parse("1.2.3-rc.4")


def config(root: Path, optional: list[str] | None = None) -> ReleaseConfig:
    """Return a config whose required artifact is a ``.deb`` under ``dist``.

    Args:
        root: Repository root.
        optional: Optional artifact patterns.

    Returns:
        ReleaseConfig: Test configuration.

    Raises:
        None
    """
    data = {**EXAMPLE, "package": "pkg", "optional_artifacts": optional or []}
    return ReleaseConfig.from_mapping(data, root)


def make_files(root: Path, *names: str) -> None:
    """Create small files under ``root/dist``.

    Args:
        root: Repository root.
        *names: File names to create.

    Returns:
        None

    Raises:
        OSError: If a file cannot be written.
    """
    (root / "dist").mkdir(exist_ok=True)
    for name in names:
        (root / "dist" / name).write_text(f"content of {name}\n", encoding="utf-8")


def test_collect_requires_the_versioned_package(tmp_path: Path) -> None:
    """A missing required artifact stops the release; optional ones may be absent.

    Args:
        tmp_path: Repository root.

    Returns:
        None

    Raises:
        AssertionError: If collection misbehaves.
    """
    cfg = config(tmp_path, optional=["dist/{package}-fieldkit.pyz"])
    with pytest.raises(artifacts.ArtifactError, match=re.escape("pkg_1.2.3~rc.4_amd64.deb")):
        artifacts.collect(cfg, VERSION)
    make_files(tmp_path, "pkg_1.2.3~rc.4_amd64.deb", "pkg_1.0.0_amd64.deb")
    assert [p.name for p in artifacts.collect(cfg, VERSION)] == ["pkg_1.2.3~rc.4_amd64.deb"]
    make_files(tmp_path, "pkg-fieldkit.pyz")
    assert [p.name for p in artifacts.collect(cfg, VERSION)][-1] == "pkg-fieldkit.pyz"


def test_collect_rejects_duplicate_names(tmp_path: Path) -> None:
    """Two artifacts with one name would make ``SHA256SUMS`` ambiguous.

    Args:
        tmp_path: Repository root.

    Returns:
        None

    Raises:
        AssertionError: If duplicates are accepted.
    """
    cfg = config(tmp_path, optional=["other/*.deb"])
    make_files(tmp_path, "pkg_1.2.3~rc.4_amd64.deb")
    (tmp_path / "other").mkdir()
    shutil.copy(tmp_path / "dist/pkg_1.2.3~rc.4_amd64.deb", tmp_path / "other")
    with pytest.raises(artifacts.ArtifactError, match="share a file name"):
        artifacts.collect(cfg, VERSION)


def test_sha256sums_format_matches_sha256sum(tmp_path: Path) -> None:
    """UPD-001: ``SHA256SUMS`` is ``<hex>  <name>`` per line, sorted by name.

    Args:
        tmp_path: Repository root.

    Returns:
        None

    Raises:
        AssertionError: If the format or a digest differs.
    """
    make_files(tmp_path, "b.deb", "a.pyz")
    staged = artifacts.stage(sorted((tmp_path / "dist").iterdir()), tmp_path / "out")
    sums = artifacts.write_sha256sums(staged, tmp_path / "out")
    expected = [
        f"{hashlib.sha256((tmp_path / 'out' / n).read_bytes()).hexdigest()}  {n}"
        for n in ("a.pyz", "b.deb")
    ]
    assert sums.read_text(encoding="utf-8").splitlines() == expected


def test_signature_verifies_and_tampering_is_detected(tmp_path: Path) -> None:
    """ADR-026: a good signature verifies; a changed checksum file does not.

    The signature file is pre-created to prove signing replaces a stale one
    instead of stopping at ssh-keygen's overwrite prompt.

    Args:
        tmp_path: Working directory for keys and files.

    Returns:
        None

    Raises:
        AssertionError: If verification gives the wrong answer.
    """
    runner = Runner(tmp_path)
    keys = tmp_path / "keys"
    keys.mkdir()
    key, signers = artifacts.throwaway_signer(runner, keys, "pkg-release", "pkg-release")
    sums = tmp_path / artifacts.SUMS_NAME
    sums.write_text("0" * 64 + "  pkg.deb\n", encoding="utf-8")
    sums.with_name(artifacts.SIG_NAME).write_text("stale", encoding="utf-8")
    artifacts.sign(runner, key, "pkg-release", sums)
    ok, detail = artifacts.verify(runner, signers, "pkg-release", "pkg-release", sums)
    assert ok, detail
    assert "Good" in detail
    sums.write_text("1" * 64 + "  pkg.deb\n", encoding="utf-8")
    assert not artifacts.verify(runner, signers, "pkg-release", "pkg-release", sums)[0]


def test_wrong_namespace_identity_or_placeholder_signers_fail(tmp_path: Path) -> None:
    """UPD-001: only the listed identity, in the agreed namespace, is trusted.

    Args:
        tmp_path: Working directory for keys and files.

    Returns:
        None

    Raises:
        AssertionError: If an untrusted signature verifies.
    """
    runner = Runner(tmp_path)
    key, signers = artifacts.throwaway_signer(runner, tmp_path, "pkg-release", "pkg-release")
    sums = tmp_path / artifacts.SUMS_NAME
    sums.write_text("data\n", encoding="utf-8")
    artifacts.sign(runner, key, "other-namespace", sums)
    assert not artifacts.verify(runner, signers, "pkg-release", "pkg-release", sums)[0]
    artifacts.sign(runner, key, "pkg-release", sums)
    assert not artifacts.verify(runner, signers, "someone-else", "pkg-release", sums)[0]
    placeholder = tmp_path / "placeholder"
    placeholder.write_text("# no keys yet\n", encoding="utf-8")
    assert not artifacts.verify(runner, placeholder, "pkg-release", "pkg-release", sums)[0]


def test_signer_entries_ignores_comments(tmp_path: Path) -> None:
    """A placeholder signers file has no entries; a real one has one per key.

    Args:
        tmp_path: Working directory.

    Returns:
        None

    Raises:
        AssertionError: If entries are miscounted.
    """
    signers = tmp_path / "signers"
    assert artifacts.signer_entries(signers) == 0
    signers.write_text("# comment\n\n  # indented comment\n", encoding="utf-8")
    assert artifacts.signer_entries(signers) == 0
    signers.write_text('# c\nr namespaces="r" ssh-ed25519 AAAA k\n', encoding="utf-8")
    assert artifacts.signer_entries(signers) == 1
