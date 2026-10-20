# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for release preflight checks (blueprint 28.7 steps 1-3).

A release must start from a clean tree on ``main``, with a new tag, green
CI for the exact commit, and no test-only version override. Git checks run
against a real throwaway repository with a local bare ``origin``; the GitHub
API is replaced by a fake runner, so no test touches the network.

Dependencies:
    pytest: Test runner; ``repo``, ``run_git``, and ``fake_runner`` fixtures
        come from ``conftest.py``.
    release_checks, release_proc: Modules under test.

Safety:
    All repositories live under ``tmp_path``; ``gh`` is never executed.

Example:
    $ uv run pytest tools/release/test_release_checks.py
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import release_checks as checks
from release_proc import CommandError, Runner


def test_clean_tree_and_branch_pass_on_fresh_main(repo: Path) -> None:
    """Step 1: a committed tree on ``main`` passes both checks.

    Args:
        repo: Throwaway repository on ``main``.

    Returns:
        None

    Raises:
        AssertionError: If either check fails.
    """
    runner = Runner(repo)
    assert checks.clean_tree(runner).ok
    assert checks.on_branch(runner, "main").ok


def test_untracked_file_and_other_branch_fail(repo: Path, run_git: Callable[..., str]) -> None:
    """Step 1: untracked files or a feature branch block a release.

    Args:
        repo: Throwaway repository on ``main``.
        run_git: Helper that runs git in a repository.

    Returns:
        None

    Raises:
        AssertionError: If either check passes.
    """
    (repo / "stray.txt").write_text("x", encoding="utf-8")
    run_git(repo, "switch", "-q", "-c", "feature")
    runner = Runner(repo)
    dirty, branch = checks.clean_tree(runner), checks.on_branch(runner, "main")
    assert not dirty.ok
    assert dirty.detail == "1 changed or untracked path(s)"
    assert not branch.ok
    assert branch.detail == "HEAD is on feature"


def test_git_failures_become_failed_outcomes(tmp_path: Path, git_env: None) -> None:  # noqa: ARG001
    """Outside a repository the checks fail cleanly instead of raising.

    Args:
        tmp_path: A directory that is not a git repository.
        git_env: Fixture isolating git configuration.

    Returns:
        None

    Raises:
        AssertionError: If a check passes or raises.
    """
    runner = Runner(tmp_path)
    assert not checks.clean_tree(runner).ok
    assert not checks.on_branch(runner, "main").ok
    assert not checks.tag_absent(runner, "origin", "v1.2.3").ok


def test_tag_must_be_new_locally_and_on_remote(repo: Path, run_git: Callable[..., str]) -> None:
    """Step 1: an existing local or remote tag blocks the release.

    Args:
        repo: Throwaway repository with a bare ``origin``.
        run_git: Helper that runs git in a repository.

    Returns:
        None

    Raises:
        AssertionError: If an existing tag is not detected.
    """
    runner = Runner(repo)
    assert checks.tag_absent(runner, "origin", "v1.2.3").ok
    run_git(repo, "tag", "v1.2.3")
    assert checks.tag_absent(runner, "origin", "v1.2.3").detail == "tag already exists locally"
    run_git(repo, "push", "-q", "origin", "v1.2.3")
    run_git(repo, "tag", "-d", "v1.2.3")
    assert checks.tag_absent(runner, "origin", "v1.2.3").detail == "tag already exists on origin"
    assert not checks.tag_absent(runner, "no-such-remote", "v9.9.9").ok


@pytest.mark.parametrize(
    ("runs", "ok", "detail"),
    [
        ([], False, "no CI check runs"),
        ([{"name": "gates", "status": "in_progress", "conclusion": None}], False, "pending: gates"),
        ([{"name": "gates", "status": "completed", "conclusion": "failure"}], False, "failed"),
        (
            [
                {"name": "gates", "status": "completed", "conclusion": "success"},
                {"name": "docs", "status": "completed", "conclusion": "skipped"},
            ],
            True,
            "2 check run(s) passed",
        ),
    ],
)
def test_evaluate_check_runs(runs: list[dict[str, Any]], ok: bool, detail: str) -> None:
    """Step 3: only completed, passing check runs allow a release.

    Args:
        runs: ``check_runs`` entries as GitHub returns them.
        ok: Expected verdict.
        detail: Expected fragment of the explanation.

    Returns:
        None

    Raises:
        AssertionError: If the verdict or explanation is wrong.
    """
    verdict, explanation = checks.evaluate_check_runs({"check_runs": runs})
    assert verdict is ok
    assert detail in explanation


def test_ci_green_queries_the_exact_commit(tmp_path: Path, fake_runner: Any) -> None:
    """Step 3: the GitHub API is asked about HEAD's SHA in the configured repo.

    Args:
        tmp_path: Working directory for the fake runner.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.

    Returns:
        None

    Raises:
        AssertionError: If the endpoint or verdict is wrong.
    """
    payload = {"check_runs": [{"name": "gates", "status": "completed", "conclusion": "success"}]}
    fake = fake_runner(tmp_path, {("gh",): payload})
    assert checks.ci_green(fake, "owner/name", "a" * 40).ok
    endpoint = f"repos/owner/name/commits/{'a' * 40}/check-runs?per_page=100"
    assert fake.calls == [["gh", "api", endpoint]]


@pytest.mark.parametrize("reply", [CommandError("gh exited with status 4"), "not json"])
def test_ci_green_fails_when_status_is_unreadable(
    tmp_path: Path, fake_runner: Any, reply: object
) -> None:
    """Step 3: an unauthenticated ``gh`` or a garbage reply blocks the release.

    Args:
        tmp_path: Working directory for the fake runner.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        reply: Exception or malformed body returned by the fake.

    Returns:
        None

    Raises:
        AssertionError: If the check passes.
    """
    outcome = checks.ci_green(fake_runner(tmp_path, {("gh",): reply}), "owner/name", "b" * 40)
    assert not outcome.ok
    assert outcome.detail.startswith("cannot read CI status")


def test_version_override_blocks_release() -> None:
    """VERSION_OVERRIDE is only for upgrade-test builds, never releases.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the override is not detected.
    """
    assert checks.no_version_override({}).ok
    assert not checks.no_version_override({"VERSION_OVERRIDE": "9.9.9"}).ok
