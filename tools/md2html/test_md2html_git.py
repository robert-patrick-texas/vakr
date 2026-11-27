# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Tests for md2html_git: files git ignores never become pages (DOCPUB-001).

DOCPUB-001 publishes every Markdown file in the repository. A developer's
private notes kept beside the tree, and listed in ``.gitignore``, are not in
the repository, so a site built from the work tree must leave them out. Each
test builds a throwaway tree under ``tmp_path``, with its own ``git init``
where it needs one, so nothing touches the repository.

Dependencies:
    pytest: Test runner; ``tmp_path`` isolates files.
    git: Needed by the work-tree test, which is skipped without it.
    md2html, md2html_git: Modules under test; pytest adds this directory to
        ``sys.path``.

Safety considerations:
    Tests only write beneath ``tmp_path`` and never access the network.

Example:
    $ uv run --with pytest==9.2.0 --with markdown-it-py==4.2.0 \\
        --with mdit-py-plugins==0.6.1 pytest tools/md2html/test_md2html_git.py
"""

import shutil
import subprocess
from pathlib import Path

import md2html
import md2html_git
import pytest

FILES = {
    "README.md": "# Home\n",
    "docs/a.md": "# A\n",
    "docs/forced.md": "# Forced\n",
    "STATUS.md": "# Private status\n",
    "local-notes/n.md": "# Private note\n",
}
IGNORE = "/STATUS.md\n/local-notes/\ndocs/forced.md\n"
GIT = shutil.which("git")


def make_tree(root: Path) -> Path:
    """Write FILES and the ``.gitignore`` below ``root``.

    Args:
        root (Path): Directory that receives the files.

    Returns:
        Path: ``root``, for chaining.

    Raises:
        None.
    """
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / ".gitignore").write_text(IGNORE, encoding="utf-8")
    return root


@pytest.mark.skipif(GIT is None, reason="needs git")
def test_ignored_files_are_left_out_in_a_work_tree(tmp_path: Path) -> None:
    """Ignored files are skipped; a tracked file renders even if it matches.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If an ignored file is found or a tracked one is lost.
    """
    src = make_tree(tmp_path / "repo")
    assert GIT is not None
    subprocess.run([GIT, "init", "-q", str(src)], check=True)  # noqa: S603 - no shell
    subprocess.run(  # noqa: S603 - no shell
        [GIT, "-C", str(src), "add", "-f", "docs/forced.md"], check=True
    )
    found = md2html.discover(src, tmp_path / "site", [])
    assert found == ["README.md", "docs/a.md", "docs/forced.md"]


def test_nothing_is_left_out_outside_a_work_tree(tmp_path: Path) -> None:
    """Outside git, every Markdown file is found, as before.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If any file is dropped.
    """
    src = make_tree(tmp_path / "plain")
    assert md2html.discover(src, tmp_path / "site", []) == sorted(FILES)
    assert md2html_git.ignored(tmp_path / "missing", ["README.md"]) == set()
