# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""The ``acceptance`` profile: the application's own numbers for the parallel run.

MVP acceptance compares the application with MRTG and SmokePing. When the
application is installed here, this profile runs its comparison export
(``[fieldkit.acceptance] command``, for vakr ``vakr acceptance export``)
over the comparison window. When the ``mrtg`` or ``smokeping`` profile ran
in the same collection, the export is limited to the interfaces MRTG
graphs and the hosts SmokePing probes; ``--acceptance-targets FILE`` gives
the list by hand when MRTG and SmokePing run on another host; otherwise
everything is exported, within the row budget.

The export writes a *member stream* (``#member-stream 1``, then
``@member NAME KIND`` before each member's lines, ``@end`` last; a content
line starting with ``@`` carries one more ``@``) to standard output. It
goes to a private file next to the bundle, not through memory, and is
split into ``acceptance/<NAME>`` members: ``table`` members are
pseudonymized column by column, ``text`` members by the generic rules.

Dependencies:
    fieldkit_app: Locating the installed application.
    fieldkit_context: Run context and bundle.

Safety:
    The export runs from an argument list with a timeout within the run's
    budget; it may switch to its service account, which is why its output
    goes to an inherited file descriptor. The private directory is removed
    afterwards. Member names are checked by the bundle writer.

Example:
    >>> lines = ["#member-stream 1", "@member a.csv table", "@@x", "@end"]
    >>> list(members(lines))
    [('a.csv', 'table', ['@x'])]
"""

import json
import subprocess
import tempfile
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from fieldkit_app import find_app
from fieldkit_bundle import BundleError
from fieldkit_context import Context

STREAM_HEADER = "#member-stream 1"
KINDS = frozenset({"table", "text"})
EXPORT_TIMEOUT = 3600.0
"""Most seconds the export may take, within the run's ``--time-budget``."""
MAX_MEMBERS = 20_000
STDERR_CAP = 4096


def members(lines: Iterable[str]) -> Iterator[tuple[str, str, list[str]]]:
    """Split a member stream into ``(name, kind, lines)``.

    Args:
        lines: Stream lines without newlines.

    Returns:
        Iterator[tuple[str, str, list[str]]]: Members in stream order.

    Raises:
        ValueError: If the header is missing, a marker is malformed, a kind
            is unknown, the stream has no ``@end``, or it has too many members.
    """
    it = iter(lines)
    if next(it, "") != STREAM_HEADER:
        raise ValueError("the export did not start with a member-stream header")
    name, kind, body, count = "", "", [], 0
    for line in it:
        if line == "@end":
            if name:
                yield name, kind, body
            return
        if line.startswith("@member "):
            if name:
                yield name, kind, body
            parts = line.split(" ")
            if len(parts) != 3 or parts[2] not in KINDS:
                raise ValueError("malformed @member line in the export")
            name, kind, body, count = parts[1], parts[2], [], count + 1
            if count > MAX_MEMBERS:
                raise ValueError(f"the export has more than {MAX_MEMBERS} members")
        elif name:
            body.append(line[1:] if line.startswith("@@") else line)
    raise ValueError("the export ended without @end (cut short or killed)")


def _run(ctx: Context, argv: list[str], out: Path) -> None:
    """Run the export with standard output to ``out``, as one step.

    Args:
        ctx: Run context.
        argv: The command.
        out: Private output file.

    Returns:
        None

    Raises:
        None
    """
    timeout = max(10.0, min(EXPORT_TIMEOUT, ctx.steps.deadline - time.monotonic()))
    with ctx.steps.step("acceptance.export") as record, out.open("wb") as handle:
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=handle,  # noqa: S603
                                  stderr=subprocess.PIPE, timeout=timeout, check=False)  # fmt: skip
        except FileNotFoundError:
            record["error"] = f"{argv[0]}: not installed"
            return
        except subprocess.TimeoutExpired:
            record["error"] = f"timed out after {timeout:g}s"
            return
        record["returncode"] = proc.returncode
        record["note"] = proc.stderr[-STDERR_CAP:].decode("utf-8", errors="replace").strip()
        if proc.returncode:
            record["error"] = f"exit status {proc.returncode}"


def _split(ctx: Context, out: Path) -> None:
    """Add the stream's members to the bundle under ``acceptance/``.

    Args:
        ctx: Run context.
        out: The export's output file.

    Returns:
        None

    Raises:
        None
    """
    with ctx.steps.step("acceptance.members") as record, out.open(encoding="utf-8") as handle:
        added = 0
        try:
            for name, kind, body in members(line.rstrip("\n") for line in handle):
                ctx.bundle.add_text(f"acceptance/{name}", "".join(f"{b}\n" for b in body), kind)
                added += 1
        except (ValueError, BundleError) as exc:
            record["error"] = str(exc)
        record["members"] = added


def collect_acceptance(ctx: Context) -> None:
    """Run the ``acceptance`` profile.

    Args:
        ctx: Run context.

    Returns:
        None

    Raises:
        None
    """
    program = find_app(ctx.info.app_name) if ctx.info.app_name else None
    if program is None:
        with ctx.steps.step("acceptance.detect") as record:
            record["note"] = f"{ctx.info.app_name or 'the application'} is not installed; skipped"
        return
    argv = [program, *ctx.info.acceptance_command, "--since", f"{ctx.args.compare_hours:g}h",
            "--max-rows", str(ctx.args.acceptance_max_rows)]  # fmt: skip
    wanted = ctx.parsed.get("acceptance_targets")
    with tempfile.TemporaryDirectory(prefix=".fieldkit-", dir=ctx.args.output_dir) as work:
        targets = Path(work) / "targets.jsonl"
        if wanted is not None:
            text = "".join(json.dumps(w, sort_keys=True) + "\n" for w in wanted)
            targets.write_text(text, encoding="utf-8")
            ctx.bundle.add_text("acceptance/targets.jsonl", text)
            argv += ["--targets", str(targets)]
        elif ctx.args.acceptance_targets is not None:
            argv += ["--targets", str(ctx.args.acceptance_targets)]
        out = Path(work) / "export.txt"
        _run(ctx, argv, out)
        if out.exists() and out.stat().st_size:
            _split(ctx, out)
