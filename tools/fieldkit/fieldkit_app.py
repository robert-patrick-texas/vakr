# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""The application profile (``vakr``): the installed application's own view (FIELD-002).

Once vakr runs in production, development needs its version, ``doctor``
output, effective configuration, health snapshot, and recent journald
events to diagnose problems remotely. The commands and journald units come
from the build configuration (``app_commands``, ``app_units``), so adding a
diagnostic command is a data change. A command that exits 3 and says "not
implemented" is skipped, because earlier releases register commands before
they are built.

Dependencies:
    fieldkit_context: Run context, step log, and bundle.

Safety:
    Only read-only reporting commands are configured. vakr redacts secrets
    in its own output; the bundle writer additionally removes the run's
    SNMP community if it appears anywhere.

Example:
    >>> command_label(("config", "show-effective"))
    'config-show-effective'
"""

import shutil
from pathlib import Path

from fieldkit_context import Context
from fieldkit_run import run_command

NOT_IMPLEMENTED_EXIT = 3
APP_TIMEOUT = 120.0
"""Seconds allowed for each application command and the journald query."""


def command_label(argv: tuple[str, ...]) -> str:
    """Name a command's output member, for example ``doctor`` or ``version``.

    Args:
        argv: Arguments after the program name.

    Returns:
        str: Hyphen-joined arguments without leading dashes.

    Raises:
        None
    """
    return "-".join(a.lstrip("-") for a in argv) or "run"


def find_app(name: str) -> str | None:
    """Locate the installed application's command.

    Args:
        name: Command name, such as ``vakr``.

    Returns:
        str | None: Path from ``PATH`` or ``/opt/<name>/bin/<name>``, else ``None``.

    Raises:
        None
    """
    packaged = Path("/opt") / name / "bin" / name
    return shutil.which(name) or (str(packaged) if packaged.is_file() else None)


def collect_app(ctx: Context) -> None:
    """Run the application profile.

    Args:
        ctx: Run context.

    Returns:
        None

    Raises:
        None
    """
    name = ctx.info.app_name
    program = find_app(name)
    if program is None:
        with ctx.steps.step(f"{name}.detect") as record:
            record["note"] = f"{name} is not installed; profile skipped"
        return
    for argv in ctx.info.app_commands:
        label = command_label(argv)
        with ctx.steps.step(f"{name}.{label}") as record:
            result = run_command([program, *argv], APP_TIMEOUT)
            record["returncode"] = result.returncode
            unbuilt = (
                result.returncode == NOT_IMPLEMENTED_EXIT and "not implemented" in result.stderr
            )
            if unbuilt:
                record["note"] = "not implemented in this release; skipped"
                continue
            if result.error:
                record["error"] = result.error
            stderr = f"\n--- stderr ---\n{result.stderr}" if result.stderr else ""
            ctx.bundle.add_text(f"{name}/{label}.txt", result.stdout + stderr)
    if not ctx.info.app_units:
        return  # without a unit filter journalctl would export the whole journal
    journal = ["journalctl", "--no-pager", "-o", "short-iso", "--since=-24h"]
    journal += ["-n", str(ctx.args.journal_lines), *(f"--unit={u}" for u in ctx.info.app_units)]
    ctx.capture(f"{name}.journal", f"{name}/journal.txt", journal, APP_TIMEOUT)
