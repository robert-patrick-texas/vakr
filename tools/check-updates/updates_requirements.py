# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Check the exact pins of requirements files against PyPI, one row per pin.

Some Python packages are pinned outside ``uv.lock``: a hash-pinned
requirements or constraints file, such as the build backend a package
build installs with ``--require-hashes``. Neither the per-package PyPI
rows nor ``uv lock --upgrade --dry-run`` read such a file, so without this
check its pins fall behind while the release gate passes. Every
``name==version`` line of each file that ``[pypi] requirements`` names
becomes a row labelled ``NAME (FILE)``, compared with PyPI's newest stable
release.

Only exact ``==`` pins are compared: a range has no single current
version. A file that is missing, unreadable, or has no exact pin yields
one ``unknown`` row, so a renamed or emptied file cannot drop out of the
report unnoticed.

Dependencies:
    updates_checks: Shared task model and row helpers.
    updates_pypi: PEP 503 name normalization.

Safety:
    Read-only: the files are read, never rewritten. Lines are parsed as
    text; nothing in them is installed, run, or fetched except the PyPI
    JSON document of each pinned name.

Example:
    >>> requirement_pins("hatchling==1.33.0 \\\n    --hash=sha256:ab\n# note\npip>=26\n")
    [('hatchling', '1.33.0')]
"""

import functools
import re
from collections.abc import Sequence

from updates_checks import Context, Task, attempt, failed_task, make_row
from updates_pypi import normalize_name
from updates_report import Result

# NAME[extras]==VERSION, then the end, whitespace, or an environment marker.
# `===` (arbitrary equality) and wildcards such as `==1.*` are not exact pins.
_PIN = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==(?!=)\s*([A-Za-z0-9][A-Za-z0-9.+!_-]*)"
    r"(?=\s|;|$)"
)
_COMMENT = re.compile(r"(^|\s)#.*$")


def logical_lines(text: str) -> list[str]:
    """Join backslash continuations and drop comments, as pip reads a requirements file.

    Args:
        text: File content.

    Returns:
        list[str]: Non-empty logical lines, stripped.

    Raises:
        None
    """
    lines, pending = [], ""
    for raw in text.splitlines():
        # pip joins continuations first and strips comments afterwards.
        if raw.rstrip().endswith("\\"):
            pending += raw.rstrip()[:-1] + " "
            continue
        lines.append(pending + raw)
        pending = ""
    if pending:
        lines.append(pending)
    stripped = (_COMMENT.sub("", line).strip() for line in lines)
    return [line for line in stripped if line]


def requirement_pins(text: str) -> list[tuple[str, str]]:
    """List the exact ``name==version`` pins of a requirements file.

    Args:
        text: File content.

    Returns:
        list[tuple[str, str]]: Normalized name and version, in file order;
        options (``--hash``, ``-r``, ``--index-url``), ranges, and URLs are
        skipped.

    Raises:
        None
    """
    pins = []
    for line in logical_lines(text):
        if not line.startswith("-") and (match := _PIN.match(line)):
            pins.append((normalize_name(match[1]), match[2]))
    return pins


def check_pin(ctx: Context, name: str, version: str, file: str) -> list[Result]:
    """Compare one pin with PyPI's newest stable release.

    Args:
        ctx: Task context.
        name: Normalized package name.
        version: Pinned version.
        file: The file, relative to the repository root, for the label.

    Returns:
        list[Result]: One row, ``NAME (FILE)``.

    Raises:
        None
    """
    latest = attempt(lambda: ctx.upstream.lookup("pypi", name))
    note = f"pinned in {file}"
    return [make_row(f"{name} ({file})", "pypi", (version, ""), latest, note=note)]


def plan_requirements(ctx: Context, files: Sequence[str]) -> list[Task]:
    """Plan one task per exact pin of every configured requirements file.

    Args:
        ctx: Task context.
        files: Paths relative to the repository root (``[pypi] requirements``).

    Returns:
        list[Task]: Pin tasks in file order; a file that cannot be read, or
        that has no exact pin, becomes one task that reports ``unknown``.

    Raises:
        None
    """
    tasks = []
    for file in files:
        try:
            pins = requirement_pins((ctx.root / file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            tasks.append(failed_task(file, "pypi", str(exc) or type(exc).__name__))
            continue
        if not pins:
            tasks.append(failed_task(file, "pypi", "no name==version pins found"))
        for name, version in pins:
            run = functools.partial(check_pin, ctx, name, version, file)
            tasks.append(Task(f"{name} ({file})", "pypi", run))
    return tasks
