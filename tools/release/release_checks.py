# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Preflight checks that must all pass before a release is built and published.

A release is cut from a known state: a clean working tree on the release
branch, a version whose tag does not exist yet, green CI for exactly the
commit being released, and a normal (not test-overridden) version. Each check
returns an ``Outcome`` instead of raising, so a dry run can list every
problem at once and a real release can stop at the first.

Dependencies:
    json: Parses ``gh api`` responses.
    release_proc: Runs ``git`` and ``gh`` with argument lists.

Safety:
    Read-only: these checks inspect git and the GitHub API and change
    nothing locally or remotely.

Example:
    >>> evaluate_check_runs({"check_runs": [{"name": "gates", "status": "completed",
    ...     "conclusion": "success"}]})
    (True, '1 check run(s) passed')
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from release_proc import CommandError, Runner

PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
"""Check-run conclusions that do not block a release."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of one preflight check.

    Attributes:
        name: Short description of what was checked.
        ok: Whether the release may proceed past this check.
        detail: Operator-readable explanation.
    """

    name: str
    ok: bool
    detail: str


def clean_tree(runner: Runner) -> Outcome:
    """Require no modified, staged, or untracked files.

    Args:
        runner: Command runner in the repository.

    Returns:
        Outcome: OK when ``git status --porcelain`` prints nothing.

    Raises:
        None
    """
    try:
        status = runner.run(["git", "status", "--porcelain"]).stdout
    except CommandError as exc:
        return Outcome("clean working tree", False, str(exc))
    changed = len(status.splitlines())
    return Outcome("clean working tree", changed == 0, f"{changed} changed or untracked path(s)")


def on_branch(runner: Runner, branch: str) -> Outcome:
    """Require HEAD to be the tip of the release branch.

    Args:
        runner: Command runner in the repository.
        branch: Required branch name, for example ``main``.

    Returns:
        Outcome: OK when the checked-out branch is ``branch``.

    Raises:
        None
    """
    name = f"on branch {branch}"
    try:
        current = runner.run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    except CommandError as exc:
        return Outcome(name, False, str(exc))
    return Outcome(name, current == branch, f"HEAD is on {current}")


def tag_absent(runner: Runner, remote: str, tag: str) -> Outcome:
    """Require that the release tag exists neither locally nor on the remote.

    Args:
        runner: Command runner in the repository.
        remote: Remote name, for example ``origin``.
        tag: Tag to be created, for example ``v1.2.3``.

    Returns:
        Outcome: OK when the tag is new everywhere; not OK when it exists or
        the remote cannot be queried.

    Raises:
        None
    """
    name = f"tag {tag} is new"
    local = runner.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], check=False)
    if local.returncode == 0:
        return Outcome(name, False, "tag already exists locally")
    try:
        remote_refs = runner.run(["git", "ls-remote", "--tags", remote, f"refs/tags/{tag}"]).stdout
    except CommandError as exc:
        return Outcome(name, False, f"cannot query {remote}: {exc}")
    if remote_refs.strip():
        return Outcome(name, False, f"tag already exists on {remote}")
    return Outcome(name, True, f"absent locally and on {remote}")


def head_commit(runner: Runner) -> str:
    """Return the full SHA of HEAD.

    Args:
        runner: Command runner in the repository.

    Returns:
        str: 40-character commit hash.

    Raises:
        release_proc.CommandError: If git fails.
    """
    return runner.run(["git", "rev-parse", "HEAD"]).stdout.strip()


def evaluate_check_runs(payload: Mapping[str, Any]) -> tuple[bool, str]:
    """Decide whether GitHub check runs for a commit allow a release.

    Args:
        payload: JSON body of ``GET /repos/{repo}/commits/{sha}/check-runs``.

    Returns:
        tuple[bool, str]: ``True`` only when at least one run exists and every
        run completed with a passing conclusion; plus an explanation that
        names the blocking runs.

    Raises:
        None
    """
    runs = payload.get("check_runs") or []
    if not runs:
        return False, "no CI check runs found for this commit (is it pushed?)"
    pending = sorted(r.get("name", "?") for r in runs if r.get("status") != "completed")
    failed = sorted(
        r.get("name", "?")
        for r in runs
        if r.get("status") == "completed" and r.get("conclusion") not in PASSING_CONCLUSIONS
    )
    if pending or failed:
        parts = [f"pending: {', '.join(pending)}"] * bool(pending)
        parts += [f"failed: {', '.join(failed)}"] * bool(failed)
        return False, "; ".join(parts)
    return True, f"{len(runs)} check run(s) passed"


def ci_green(runner: Runner, repo: str, sha: str) -> Outcome:
    """Require green GitHub CI for the commit being released.

    Args:
        runner: Command runner; ``gh`` must be authenticated.
        repo: GitHub ``OWNER/NAME``.
        sha: Commit to check.

    Returns:
        Outcome: OK when every check run for ``sha`` passed.

    Raises:
        None
    """
    name = f"CI green for {sha[:12]}"
    # One page of 100 runs covers this project's workflows many times over.
    endpoint = f"repos/{repo}/commits/{sha}/check-runs?per_page=100"
    try:
        payload = json.loads(runner.run(["gh", "api", endpoint]).stdout)
    except (CommandError, json.JSONDecodeError) as exc:
        return Outcome(name, False, f"cannot read CI status: {exc}")
    ok, detail = evaluate_check_runs(payload)
    return Outcome(name, ok, detail)


def no_version_override(environ: Mapping[str, str]) -> Outcome:
    """Refuse releases while ``VERSION_OVERRIDE`` is set.

    ``VERSION_OVERRIDE`` exists only so upgrade tests can build an N+1
    package; a release must carry the version committed in ``VERSION``.

    Args:
        environ: Process environment.

    Returns:
        Outcome: OK when the variable is unset or empty.

    Raises:
        None
    """
    value = environ.get("VERSION_OVERRIDE", "")
    detail = f"VERSION_OVERRIDE={value} is for test builds only" if value else "not set"
    return Outcome("no VERSION_OVERRIDE", not value, detail)
