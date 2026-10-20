# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests of ``release.py`` in a throwaway repository (28.7, ADR-026).

Each test builds a real git repository with a local bare ``origin``, a
build command that writes a fake ``.deb``, and real ``ssh-keygen`` keys.
Only ``gh`` and ``git push`` are faked, so the tests prove the whole flow
(preflight, build, checksums, signing, verification against the shipped
signers file, tagging, and the release command) without touching GitHub
or any real repository.

Dependencies:
    pytest: Test runner; ``repo``, ``run_git``, ``fake_runner`` fixtures.
    release, release_config: Modules under test.

Safety:
    Everything happens under ``tmp_path``. ``git push`` and ``gh`` are
    intercepted by the fake runner and never executed.

Example:
    $ uv run pytest tools/release/test_release.py
"""

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import release
from release_config import load_config

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

BUILD = (
    "import os, pathlib; d = pathlib.Path('dist'); d.mkdir(exist_ok=True); "
    "(d / 'pkg_1.2.3_amd64.deb').write_text('deb'); "
    "(d / 'override.txt').write_text(os.environ.get('VERSION_OVERRIDE', 'unset'))"
)
GREEN = {"check_runs": [{"name": "gates", "status": "completed", "conclusion": "success"}]}
FAKES = {("gh", "api"): GREEN, ("gh", "release"): "", ("git", "push"): ""}


def write_config(repo: Path, run_git: Callable[..., str], key: Path) -> Path:
    """Write and commit a ``release.toml`` for the throwaway repository.

    Args:
        repo: Repository root.
        run_git: Helper that runs git in a repository.
        key: Signing key path (it may not exist).

    Returns:
        Path: The config file.

    Raises:
        subprocess.CalledProcessError: If committing fails.
    """
    settings: dict[str, Any] = {
        "repo": "owner/name",
        "package": "pkg",
        "version_file": "VERSION",
        "changelog": "CHANGELOG.md",
        "branch": "main",
        "remote": "origin",
        "signing_key": str(key),
        "namespace": "pkg-release",
        "identity": "pkg-release",
        "allowed_signers": "signers",
        "build_command": [sys.executable, "-c", BUILD],
        "artifacts": ["dist/{package}_{deb_version}_amd64.deb"],
        "optional_artifacts": ["dist/{package}-fieldkit.pyz"],
        "output_dir": "dist/release/{tag}",
        "prerelease": "rc-or-major-zero",
    }
    path = repo / "release.toml"
    # JSON strings and arrays are valid TOML values for this content.
    path.write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in settings.items()), "utf-8")
    run_git(repo, "add", "release.toml")
    run_git(repo, "commit", "-q", "-m", "release config")
    return path


def trust_new_key(repo: Path, run_git: Callable[..., str], tmp_path: Path) -> Path:
    """Generate a key, list it in the repository's signers file, and commit.

    Args:
        repo: Repository root.
        run_git: Helper that runs git in a repository.
        tmp_path: Directory for the key, outside the repository.

    Returns:
        Path: The private key.

    Raises:
        subprocess.CalledProcessError: If key generation or git fails.
    """
    key = tmp_path / "release_ed25519"
    argv = ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)]
    subprocess.run(argv, check=True)  # noqa: S603
    public = key.with_name(key.name + ".pub").read_text(encoding="utf-8").strip()
    (repo / "signers").write_text(f'pkg-release namespaces="pkg-release" {public}\n', "utf-8")
    run_git(repo, "commit", "-q", "-am", "trust release key")
    return key


def test_dry_run_signs_with_throwaway_key_and_publishes_nothing(
    repo: Path,
    run_git: Callable[..., str],
    fake_runner: Any,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry run: full build/sign/verify, no tag or release; placeholder signers flagged.

    Args:
        repo: Throwaway repository on ``main``.
        run_git: Helper that runs git in a repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Per-test temporary directory.
        capsys: Captures the tool's report.

    Returns:
        None

    Raises:
        AssertionError: If anything is published or a step is skipped.
    """
    cfg = load_config(write_config(repo, run_git, tmp_path / "absent_key"), repo)
    fake = fake_runner(repo, FAKES)
    assert release.release(cfg, fake, dry_run=True, environ={}) == 1  # placeholder signers
    out_dir = repo / "dist/release/v1.2.3"
    assert (out_dir / "SHA256SUMS").read_text("utf-8").endswith("  pkg_1.2.3_amd64.deb\n")
    assert (out_dir / "SHA256SUMS.sig").is_file()
    assert (out_dir / "release-notes.txt").read_text("utf-8") == "### Added\n\n- Thing."
    assert not [c for c in fake.calls if c[:2] in (["git", "tag"], ["git", "push"])]
    assert not [c for c in fake.calls if c[:2] == ["gh", "release"]]
    report = capsys.readouterr().out
    assert "[FAIL] signers lists a signer" in report
    assert "would run: gh release create v1.2.3 --repo owner/name" in report
    assert "dry run uses a throwaway key" in report


def test_release_tags_and_publishes_after_verification(
    repo: Path, run_git: Callable[..., str], fake_runner: Any, tmp_path: Path
) -> None:
    """Real run: verified signature, annotated tag, push, then the GitHub release.

    Args:
        repo: Throwaway repository on ``main``.
        run_git: Helper that runs git in a repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If a step is missing or out of order.
    """
    key = trust_new_key(repo, run_git, tmp_path)
    cfg = load_config(write_config(repo, run_git, key), repo)
    fake = fake_runner(repo, FAKES)
    assert release.release(cfg, fake, dry_run=False, environ={}) == 0
    assert run_git(repo, "cat-file", "-t", "v1.2.3").strip() == "tag"
    names = [c[:2] for c in fake.calls]
    assert (
        names.index(["git", "tag"]) < names.index(["git", "push"]) < names.index(["gh", "release"])
    )
    gh_release = next(c for c in fake.calls if c[:2] == ["gh", "release"])
    assert gh_release[-1].endswith("SHA256SUMS.sig")
    assert "--prerelease" not in gh_release
    assert not run_git(repo, "ls-remote", "--tags", "origin").strip()  # push was faked


def test_failed_preflight_stops_before_build(
    repo: Path, run_git: Callable[..., str], fake_runner: Any, tmp_path: Path
) -> None:
    """Real run: a dirty tree or VERSION_OVERRIDE stops the release before building.

    Args:
        repo: Throwaway repository on ``main``.
        run_git: Helper that runs git in a repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If the build runs.
    """
    key = trust_new_key(repo, run_git, tmp_path)
    cfg = load_config(write_config(repo, run_git, key), repo)
    environ = {"VERSION_OVERRIDE": "1.2.4"}
    assert release.release(cfg, fake_runner(repo, FAKES), dry_run=False, environ=environ) == 1
    assert not (repo / "dist").exists()


def test_missing_key_fails_real_release_and_override_is_not_built(
    repo: Path,
    run_git: Callable[..., str],
    fake_runner: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real run without the key fails; the build never sees VERSION_OVERRIDE.

    Args:
        repo: Throwaway repository on ``main``.
        run_git: Helper that runs git in a repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Per-test temporary directory.
        monkeypatch: Sets VERSION_OVERRIDE in the runner's environment.

    Returns:
        None

    Raises:
        AssertionError: If the release succeeds or the override leaks.
    """
    trust_new_key(repo, run_git, tmp_path)
    cfg = load_config(write_config(repo, run_git, tmp_path / "gone"), repo)
    monkeypatch.setenv("VERSION_OVERRIDE", "7.7.7")
    assert release.release(cfg, fake_runner(repo, FAKES), dry_run=False, environ={}) == 1
    assert (repo / "dist/override.txt").read_text("utf-8") == "unset"


def test_main_prints_versions_and_reports_errors(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--print-version`` serves build scripts; bad input exits 2.

    Args:
        repo: Throwaway repository whose VERSION is 1.2.3.
        capsys: Captures output.

    Returns:
        None

    Raises:
        AssertionError: If output or exit codes differ.
    """
    assert release.main(["--print-version", "deb", "--version", "0.2.0-rc.1"]) == 0
    assert release.main(["--print-version", "next-patch", "--root", str(repo)]) == 0
    assert capsys.readouterr().out.split() == ["0.2.0~rc.1", "1.2.4"]
    assert release.main(["--print-version", "deb", "--version", "1.2"]) == 2
    assert release.main(["--config", str(repo / "absent.toml"), "--root", str(repo)]) == 2
    assert "cannot read release config" in capsys.readouterr().err
