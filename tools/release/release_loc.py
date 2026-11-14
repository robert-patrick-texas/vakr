# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Count first-party lines of code with cloc for the release notes (VER-006).

Each release's notes carry a lines-of-code table by language. Only
first-party content counts: cloc is given the files git tracks at the
release commit, minus configured patterns for generated files that record
third-party packages (lockfiles, hash pins). Nothing installed, vendored,
or downloaded at build time is in the repository, so no dependency or
external component reaches the count.

Dependencies:
    release_proc: Runs ``git ls-files`` and cloc with argument lists.
    release_notes_config: Exclusion patterns and the cloc command.
    External: cloc (``build_tools`` asks for its version in preflight).

Safety:
    Read-only. The file list is written to a private temporary directory
    and handed to cloc with ``--list-file``, never through a shell.

Example:
    >>> render_loc({"Python": {"nFiles": 2, "blank": 1, "comment": 3, "code": 40},
    ...             "SUM": {"nFiles": 2, "blank": 1, "comment": 3, "code": 40}}, "2.06", 2, ())[0]
    '### Lines of code'
"""

import fnmatch
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from release_notes_config import NotesConfig, NotesError
from release_proc import CommandError, Runner

HEADING = "### Lines of code"


def first_party_files(runner: Runner, exclude: Sequence[str]) -> list[str]:
    """List the files git tracks, minus the configured exclusions.

    Args:
        runner: Command runner in the repository.
        exclude: ``fnmatch`` patterns of tracked files that are not first-party.

    Returns:
        list[str]: Repository-relative paths, in git's order.

    Raises:
        CommandError: If git fails.
    """
    listed = runner.run(["git", "ls-files", "-z"]).stdout.split("\0")
    return [p for p in listed if p and not any(fnmatch.fnmatchcase(p, pat) for pat in exclude)]


def count_loc(runner: Runner, cfg: NotesConfig, workdir: Path) -> tuple[dict[str, Any], str, int]:
    """Run cloc over the first-party files.

    Args:
        runner: Command runner in the repository.
        cfg: Notes configuration (cloc command and exclusions).
        workdir: Private temporary directory for the file list.

    Returns:
        tuple[dict[str, Any], str, int]: cloc's per-language JSON (with
        ``SUM``), cloc's version, and the number of files given to it.

    Raises:
        NotesError: If cloc fails, prints no JSON, or a path holds a newline.
    """
    try:
        files = first_party_files(runner, cfg.loc_exclude)
        if any("\n" in path for path in files):
            raise NotesError("a tracked path contains a newline; cloc --list-file cannot take it")
        listing = workdir / "loc-files.txt"
        listing.write_text("".join(f"{path}\n" for path in files), encoding="utf-8")
        version = runner.run([*cfg.loc_command, "--version"]).stdout.strip()
        out = runner.run([*cfg.loc_command, f"--list-file={listing}", "--json", "--quiet"]).stdout
        counts = json.loads(out)
    except (CommandError, OSError, ValueError) as exc:
        raise NotesError(f"lines of code: {exc}") from exc
    if not isinstance(counts, dict) or "SUM" not in counts:
        raise NotesError("lines of code: cloc printed no totals")
    counts.pop("header", None)
    return counts, version, len(files)


def render_loc(
    counts: Mapping[str, Any], cloc_version: str, files: int, exclude: Sequence[str]
) -> list[str]:
    """Render the lines-of-code table and the statement under it.

    Args:
        counts: cloc's per-language JSON, including ``SUM``.
        cloc_version: cloc's version, for the statement.
        files: Number of first-party files given to cloc.
        exclude: Patterns left out, named in the statement.

    Returns:
        list[str]: Markdown lines: heading, table, and statement.

    Raises:
        None
    """
    lines = [HEADING, "", "| Language | Files | Blank | Comment | Code |", "|---|--:|--:|--:|--:|"]
    languages = sorted(
        ((name, row) for name, row in counts.items() if name != "SUM"),
        key=lambda item: (-int(item[1]["code"]), item[0]),
    )
    for name, row in [*languages, ("**Total**", counts["SUM"])]:
        cells = [f"{int(row[key]):,}" for key in ("nFiles", "blank", "comment", "code")]
        if name == "**Total**":
            cells = [f"**{cell}**" for cell in cells]
        lines.append(f"| {name} | {' | '.join(cells)} |")
    left_out = ", ".join(f"`{pattern}`" for pattern in exclude)
    statement = (
        f"Counted with [cloc](https://github.com/AlDanial/cloc) {cloc_version} over the "
        f"{files:,} files git tracks at this release's commit; cloc skips files it does not "
        "recognise as source and counts identical files once. The counts cover first-party "
        "content only. No dependencies or external components are included: nothing vendored, "
        "installed, or downloaded at build time is counted"
    )
    statement += f". Generated files are left out too: {left_out}." if exclude else "."
    return [*lines, "", statement]
