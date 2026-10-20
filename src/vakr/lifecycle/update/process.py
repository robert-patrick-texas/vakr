"""Run external commands as argument lists with captured, bounded output.

The updater calls ``ssh-keygen``, ``apt-get``, ``systemctl``, and the newly
installed binary. All of them go through one injectable runner so tests can
substitute fakes and so no call can ever be made through a shell.

Dependencies:
    subprocess, os: Process execution and environment merging.

Safety:
    ``shell`` is never used; every command has a timeout; a missing program
    becomes exit status 127 instead of an exception, matching the shell
    convention operators recognize. Output is kept only for error summaries.

Example:
    >>> run_command(["/bin/true"]).returncode
    0
"""

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_TIMEOUT = 900.0
"""Seconds before a command is killed; generous for ``apt-get install``."""


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome of one external command.

    Attributes:
        returncode: Exit status (127 when the program was not found, 124 on
            timeout).
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""

    def tail(self, lines: int = 12) -> str:
        """Return the last lines of stderr (or stdout) for an error message.

        Args:
            lines: Maximum number of lines.

        Returns:
            str: Trailing output joined by newlines, possibly empty.

        Raises:
            None
        """
        text = (self.stderr or self.stdout).strip()
        return "\n".join(text.splitlines()[-lines:])


class Runner(Protocol):
    """Callable that runs an argument list and returns a ``RunResult``."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> RunResult:
        """Run ``argv``.

        Args:
            argv: Program and arguments.
            stdin: Bytes fed to standard input, or ``None``.
            env: Variables added to the inherited environment.
            timeout: Seconds before the command is killed.

        Returns:
            RunResult: Exit status and captured output.

        Raises:
            None
        """
        ...


def run_command(
    argv: Sequence[str],
    *,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> RunResult:
    """Run ``argv`` without a shell and capture its output.

    Args:
        argv: Program (preferably an absolute path) and arguments.
        stdin: Bytes fed to standard input, or ``None`` for no input.
        env: Variables added to the inherited environment.
        timeout: Seconds before the command is killed.

    Returns:
        RunResult: Exit status and decoded output.

    Raises:
        None
    """
    merged = {**os.environ, **(env or {})}
    try:
        done = subprocess.run(  # noqa: S603 - argument list from trusted callers, no shell
            list(argv),
            input=stdin if stdin is not None else b"",
            capture_output=True,
            env=merged,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return RunResult(127, "", f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return RunResult(124, "", f"timed out after {timeout:.0f}s: {argv[0]}")
    return RunResult(
        done.returncode,
        done.stdout.decode("utf-8", "replace"),
        done.stderr.decode("utf-8", "replace"),
    )


def apt_installer(run: Runner, apt_get: str = "/usr/bin/apt-get") -> Callable[[Path], RunResult]:
    """Build an installer that installs a local ``.deb`` with ``apt-get``.

    ``apt-get`` (not ``dpkg -i``) resolves the package's dependencies
    (UPD-002); ``--allow-downgrades`` lets the same call perform rollbacks.

    Args:
        run: Command runner.
        apt_get: Absolute path of ``apt-get``.

    Returns:
        Callable[[Path], RunResult]: Installer taking a package path.

    Raises:
        None
    """

    def install(package: Path) -> RunResult:
        """Install ``package`` non-interactively.

        Args:
            package: Verified package file.

        Returns:
            RunResult: ``apt-get`` outcome.

        Raises:
            None
        """
        argv = [apt_get, "install", "-y", "--allow-downgrades", str(package.resolve())]
        return run(argv, env={"DEBIAN_FRONTEND": "noninteractive"})

    return install
