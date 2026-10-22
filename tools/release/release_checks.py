# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Preflight checks that must all pass before a release is built and published.

A release is cut from a known state: a clean working tree on the release
branch, a version whose tag does not exist yet, green CI for exactly the
commit being released, a normal (not test-overridden) version, and the
build-host tools the configuration names (Node and npm for vakr's web UI,
ADR-035), with Node on the pinned line. Each check returns an ``Outcome``
instead of raising, so a dry run can list every problem at once and a real
release can stop at the first.

Dependencies:
    json: Parses ``gh api`` responses.
    release_proc: Runs ``git``, ``gh``, and each tool's ``--version``
        with argument lists.

Safety:
    Read-only: these checks inspect git, the GitHub API, and tool versions
    and change nothing locally or remotely.

Example:
    >>> evaluate_check_runs({"check_runs": [{"name": "gates", "status": "completed",
    ...     "conclusion": "success"}]})
    (True, '1 check run(s) passed')
"""

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
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


def evaluate_check_runs(payload: Mapping[str, Any], skip: Collection[str] = ()) -> tuple[bool, str]:
    """Decide whether GitHub check runs for a commit allow a release.

    Args:
        payload: JSON body of ``GET /repos/{repo}/commits/{sha}/check-runs``.
        skip: Check-run names that never block (``ci_ignore_checks``).

    Returns:
        tuple[bool, str]: ``True`` only when at least one run exists and every
        run completed with a passing conclusion; plus an explanation that
        names the blocking runs.

    Raises:
        None
    """
    runs = [r for r in payload.get("check_runs") or [] if r.get("name") not in skip]
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


def ci_green(runner: Runner, repo: str, sha: str, skip: Collection[str] = ()) -> Outcome:
    """Require green GitHub CI for the commit being released.

    Args:
        runner: Command runner; ``gh`` must be authenticated.
        repo: GitHub ``OWNER/NAME``.
        sha: Commit to check.
        skip: Check-run names that never block (``ci_ignore_checks``).

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
    ok, detail = evaluate_check_runs(payload, skip)
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


def tools_present(runner: Runner, tools: Sequence[str]) -> Outcome:
    """Require every build-host tool the release needs (ADR-035: Node and npm).

    The release machine runs ``make check`` and the web UI's checks, which
    need Node and npm, so a missing tool stops the release before anything
    is built rather than halfway through.

    Args:
        runner: Command runner in the repository.
        tools: Program names; each must answer ``--version``.

    Returns:
        Outcome: OK with each tool's version, or not OK naming the missing ones.

    Raises:
        None
    """
    name = f"build tools present ({', '.join(tools)})"
    found, missing = [], []
    for tool in tools:
        try:
            lines = runner.run([tool, "--version"]).stdout.strip().splitlines()
        except CommandError:
            missing.append(tool)
            continue
        found.append(f"{tool} {lines[0] if lines else '(no version printed)'}")
    if missing:
        detail = f"missing or not working: {', '.join(missing)}; install them on this host"
        return Outcome(name, False, detail)
    return Outcome(name, True, ", ".join(found))


def node_matches_pin(runner: Runner, pin: Path) -> Outcome:
    """Require the installed Node to be on the line the project pins (ADR-035 #16).

    The pin is compared at the precision it is written with: ``24`` admits
    any 24.x, ``24.22.1`` only that release.

    Args:
        runner: Command runner in the repository.
        pin: Version file such as ``web/.node-version``.

    Returns:
        Outcome: OK when ``node --version`` matches the pin.

    Raises:
        None
    """
    name = f"node matches {pin.name}"
    try:
        lines = [line.strip() for line in pin.read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        return Outcome(name, False, f"cannot read {pin}: {exc.strerror}")
    wanted = next((x for x in lines if x and not x.startswith("#")), "").removeprefix("v")
    if not wanted:
        return Outcome(name, False, f"{pin.name} names no version")
    try:
        have = runner.run(["node", "--version"]).stdout.strip().removeprefix("v")
    except CommandError as exc:
        return Outcome(name, False, str(exc))
    want_parts = wanted.split(".")
    ok = have.split(".")[: len(want_parts)] == want_parts
    detail = f"node {have}; {pin.name} pins {wanted}"
    return Outcome(name, ok, detail if ok else f"{detail}: install Node {wanted}")


def toolchain(runner: Runner, tools: Sequence[str], pin: Path | None) -> list[Outcome]:
    """Run the configured build-host checks: tools present, then the Node pin.

    Args:
        runner: Command runner in the repository.
        tools: ``build_tools`` from the configuration; empty skips the check.
        pin: ``node_version_file``; ``None`` skips the check.

    Returns:
        list[Outcome]: Zero, one, or two outcomes.

    Raises:
        None
    """
    outcomes = [tools_present(runner, tools)] if tools else []
    if pin is not None:
        outcomes.append(node_matches_pin(runner, pin))
    return outcomes
