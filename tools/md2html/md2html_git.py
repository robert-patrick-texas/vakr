# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tell md2html which of the Markdown files it found git ignores.

A developer may keep private Markdown beside the tree, such as status notes or
an agent's working notes, listed in ``.gitignore`` so they are never committed.
A site built from the work tree must not turn them into pages, or a site
published by hand from that build would publish them. This module asks git
which of the found files it ignores, so ``md2html.py`` can leave them out. A
tracked file is never reported, since git ignores only untracked files, so
every committed page still renders, and a new page not yet added still renders
and is link-checked. Outside a git work tree, or where git is not installed,
nothing is left out. Like the rest of md2html it imports nothing from the
project that vendors it.

Dependencies:
    shutil, subprocess: Find git on the PATH and run ``git check-ignore``;
        git itself is optional.

Safety considerations:
    git only reads the tree: the paths go to its standard input, never into a
    shell command line. A git failure leaves the page list as it was rather
    than dropping pages, so a broken git never hides documentation.

Example:
    >>> ignored(Path("/no/such/dir"), ["README.md"])
    set()
"""

import shutil
import subprocess
from pathlib import Path


def ignored(src: Path, names: list[str]) -> set[str]:
    """Report which of the given files git ignores.

    Args:
        src (Path): Directory the names are relative to.
        names (list[str]): POSIX paths relative to ``src``.

    Returns:
        set[str]: The names git ignores; empty outside a git work tree, when git
        is missing or fails, or when ``names`` is empty.

    Raises:
        None.
    """
    git = shutil.which("git")
    if not names or git is None or not src.is_dir():
        return set()
    try:
        done = subprocess.run(  # noqa: S603 - argument list, no shell
            [git, "-C", str(src), "check-ignore", "-z", "--stdin"],
            input="\0".join(names) + "\0",
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    # Exit 0: some names are ignored; 1: none are; anything else: not a work tree.
    if done.returncode not in (0, 1):
        return set()
    return {name for name in done.stdout.split("\0") if name}
