# SPDX-License-Identifier: Apache-2.0
"""Fail when authored source files exceed a physical line limit.

Long files hide multiple responsibilities, which makes code harder to review
and to teach from. This gate counts every physical line, docstrings and
comments included, and fails CI for any file over the limit unless an
exceptions file lists it with a justification. It is portable: nothing here
is specific to one project.

Dependencies:
    argparse, pathlib, re: Standard library only, so it runs anywhere.

Safety:
    Read-only; it never modifies the files it inspects.

Example:
    $ python tools/check-loc/check_loc.py --limit 300 src tools \
        --exceptions docs/architecture/loc-exceptions.md
    check-loc: 42 files checked, 0 over 300 lines
"""

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LIMIT = 300


def load_exceptions(path: Path | None) -> set[str]:
    """Read approved exception paths from a Markdown table's first column.

    Args:
        path: Exceptions file, or ``None`` when no exceptions are allowed.

    Returns:
        set[str]: Repository-relative paths that may exceed the limit.

    Raises:
        OSError: If the file is given but cannot be read.
    """
    if path is None or not path.exists():
        return set()
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|", path.read_text(encoding="utf-8"), re.M)
    return set(rows)


def oversized(roots: list[Path], limit: int, allowed: set[str]) -> tuple[int, list[str]]:
    """Find Python files under ``roots`` longer than ``limit`` lines.

    Args:
        roots: Directories to scan recursively for ``*.py`` files.
        limit: Maximum physical lines per file.
        allowed: Paths exempted by the exceptions file.

    Returns:
        tuple[int, list[str]]: Number of files checked, and one message per
        offending file.

    Raises:
        OSError: If a file cannot be read.
    """
    checked = 0
    problems: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            skipped_dir = any(p in {".venv", "_site", "__pycache__"} for p in path.parts)
            # Tests "should" be split (section 17.2) but are not a hard gate.
            if skipped_dir or path.name.startswith("test_"):
                continue
            checked += 1
            count = len(path.read_text(encoding="utf-8").splitlines())
            if count > limit and path.as_posix() not in allowed:
                problems.append(f"{path.as_posix()}: {count} lines (limit {limit})")
    return checked, problems


def main(argv: list[str] | None = None) -> int:
    """Run the line-count gate.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        int: 0 when every file is within the limit, 1 otherwise.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--exceptions", type=Path)
    args = parser.parse_args(argv)
    checked, problems = oversized(args.roots, args.limit, load_exceptions(args.exceptions))
    for problem in problems:
        print(problem)
    print(f"check-loc: {checked} files checked, {len(problems)} over {args.limit} lines")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
