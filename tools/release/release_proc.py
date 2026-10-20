# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Run the external commands a release needs: git, gh, ssh-keygen, and the build.

Every step of a release shells out to a well-known tool. Funnelling those
calls through one small runner keeps three promises in one place: commands
are argument lists and never pass through a shell, a missing tool produces a
clear message instead of a traceback, and tests can substitute a fake runner
so no test ever talks to GitHub.

Dependencies:
    subprocess, contextlib: Process execution with argument lists.

Safety:
    ``shell=True`` is never used. Captured output is returned to the caller,
    never logged here, so nothing secret is echoed by accident.

Example:
    >>> Runner(Path(".")).run(["true"]).returncode
    0
"""

import contextlib
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class CommandError(RuntimeError):
    """An external command was missing or exited with a non-zero status."""


@dataclass(frozen=True, slots=True)
class Result:
    """Outcome of one finished command.

    Attributes:
        returncode: Exit status.
        stdout: Captured standard output (empty when not captured).
        stderr: Captured standard error (empty when not captured).
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    """Executes commands in the repository; tests replace it with a fake.

    Not thread-safe; a release runs its steps one at a time.
    """

    def __init__(self, cwd: Path, env: Mapping[str, str] | None = None) -> None:
        """Create a runner rooted at ``cwd``.

        Args:
            cwd: Working directory for every command (the repository root).
            env: Base environment; ``None`` means the current process's.

        Returns:
            None

        Raises:
            None
        """
        self.cwd = cwd
        self.env = dict(os.environ if env is None else env)

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = True,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        stdin: Path | None = None,
    ) -> Result:
        """Run one command and wait for it.

        Args:
            argv: Program and arguments; never interpreted by a shell.
            capture: Capture output (``True``) or let it stream to the
                terminal, as long builds and passphrase prompts need.
            check: Raise ``CommandError`` on a non-zero exit.
            env: Environment for this command; ``None`` uses the runner's.
            stdin: File to connect to standard input, if any.

        Returns:
            Result: Exit status and any captured output.

        Raises:
            CommandError: If the program is missing, or exits non-zero while
                ``check`` is true.
        """
        source = stdin.open("rb") if stdin else contextlib.nullcontext()
        try:
            with source as handle:
                done = subprocess.run(  # noqa: S603 - argument list, no shell
                    list(argv),
                    cwd=self.cwd,
                    env=dict(self.env if env is None else env),
                    stdin=handle,
                    capture_output=capture,
                    text=True,
                    check=False,
                )
        except FileNotFoundError as exc:
            raise CommandError(f"command not found: {argv[0]}") from exc
        result = Result(done.returncode, done.stdout or "", done.stderr or "")
        if check and result.returncode != 0:
            detail = result.stderr.strip().splitlines()[-1:] if capture else []
            suffix = f": {detail[0]}" if detail else ""
            raise CommandError(f"{argv[0]} exited with status {result.returncode}{suffix}")
        return result
