# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Read versions a repository pins in plain files or that a command prints.

The "current" half of most comparisons is a short string somewhere in the
repository: the minor version in ``.python-version``, the floor of
``requires-python``, a version pinned in a Makefile or in a script's
inline metadata, or the first line of ``tool --version``. These helpers
find those strings and discover files such as ``go.mod``. Go modules,
Python package locks, and GitHub Actions pins have their own modules
(``updates_go``, ``updates_pypi``, ``updates_actions``).

Dependencies:
    re, tomllib, pathlib: Pattern search, ``pyproject.toml`` parsing, and
        glob discovery.

Safety:
    Read-only. Discovery skips ``.git``, virtual environments, and
    vendored trees so it never wanders into third-party code.

Example:
    >>> find_version("uv 0.13.3 (x86_64-unknown-linux-gnu)")
    '0.13.3'
    >>> find_version("go version go1.27.3 linux/amd64")
    '1.27.3'
"""

import re
import tomllib
from pathlib import Path

# A version-looking token: dotted digits and any suffix. Letters may precede
# it so "go1.27.3" and "v2.48.0" yield their numbers.
VERSION_TOKEN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+(?:[-.+]?[0-9A-Za-z]+)*)")
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "_site", "vendor"})


def read_version_file(path: Path) -> str:
    """Return the first meaningful line of a file like ``.python-version``.

    Args:
        path: File to read.

    Returns:
        str: The first non-blank line that is not a ``#`` comment.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the file holds no version line.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            return line.strip()
    raise ValueError(f"{path.name} holds no version")


def requires_python_floor(pyproject: Path) -> str:
    """Return the minimum Python version allowed by ``requires-python``.

    Args:
        pyproject: Path to ``pyproject.toml``.

    Returns:
        str: The version after ``>=``, ``~=``, or ``==``, such as ``3.15``.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If there is no ``requires-python`` lower bound or the
            file is not valid TOML.
    """
    spec = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    text = spec.get("project", {}).get("requires-python", "")
    match = re.search(r"(?:>=|~=|==)\s*(\d+(?:\.\d+)*)", text)
    if match is None:
        raise ValueError(f"no requires-python lower bound in {pyproject.name}")
    return match[1]


def discover(root: Path, patterns: list[str]) -> list[Path]:
    """Find files under ``root`` matching glob patterns, skipping vendored trees.

    Args:
        root: Repository root.
        patterns: Globs relative to ``root``, such as ``**/go.mod``.

    Returns:
        list[Path]: Sorted, de-duplicated matches.

    Raises:
        None
    """
    found = {
        path
        for pattern in patterns
        for path in root.glob(pattern)
        if path.is_file() and not _SKIP_DIRS.intersection(path.relative_to(root).parts)
    }
    return sorted(found)


def find_version(text: str, regex: str | None = None) -> str | None:
    """Extract a version from command output or file text.

    Args:
        text: Text to search.
        regex: Pattern whose first group is the version; when None, the
            first version-looking token is used.

    Returns:
        str | None: The version, or None when nothing matches.

    Raises:
        re.error: If ``regex`` is not a valid regular expression.
    """
    match = re.search(regex, text, re.MULTILINE) if regex else VERSION_TOKEN.search(text)
    return match[1] if match else None
