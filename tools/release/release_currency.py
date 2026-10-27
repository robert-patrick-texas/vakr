# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Preflight check that every dependency is current when the release is cut.

Dependencies publish at any hour, and a release prepared in the morning can
be cut after a new version came out the same afternoon. The project rule
is to ship on the newest releases, whatever their age, unless one failed
testing and is held by a recorded exception (ADR-036, TOOLS-002,
TOOLS-004). So the release itself runs the configured update check,
``update_check``, at the moment it starts, rather than trusting a check
from the preparation step (TOOLS-005). The command is project data: for
vakr it is ``tools/check-updates --gate``.

Dependencies:
    release_proc: Runs the command with an argument list.
    release_checks: The ``Outcome`` every preflight check returns.

Safety:
    Read-only for the release tool: it runs one configured command, with no
    shell, and reports its last output line. The command's own safety is
    its own; check-updates changes nothing in the repository.

Example:
    >>> updates_current(Runner(Path(".")), ["true"]).ok
    True
"""

from collections.abc import Sequence

from release_checks import Outcome
from release_proc import CommandError, Runner


def updates_current(runner: Runner, command: Sequence[str]) -> Outcome:
    """Run the update check and pass only when it exits 0.

    Args:
        runner: Command runner in the repository.
        command: ``update_check`` from the configuration.

    Returns:
        Outcome: OK when the command exits 0. The detail is its last
        non-empty output line, which for check-updates names what blocks.

    Raises:
        None
    """
    name = "dependencies current at release time"
    try:
        result = runner.run(command, check=False)
    except CommandError as exc:
        return Outcome(name, False, str(exc))
    lines = [line.strip() for line in (result.stdout + result.stderr).splitlines()]
    last = next((line for line in reversed(lines) if line), "no output")
    if result.returncode != 0:
        last = f"{last} (exit {result.returncode}; apply each update or record a failed test)"
    return Outcome(name, result.returncode == 0, last)
