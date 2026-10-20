# SPDX-License-Identifier: Apache-2.0
"""Fail when the project version is hard-coded outside the single VERSION file.

A project that keeps its version in one file (VER-001) breaks that promise
the moment someone pastes the version into a README, a packaging spec, or a
source constant. This gate reads the canonical version and searches every
git-tracked text file for it, failing unless the file is allowlisted (for
example the changelog, which names versions deliberately). It is portable:
the version file and allowlist are arguments.

Dependencies:
    subprocess: Lists tracked files with ``git ls-files`` (argument list,
        no shell).

Safety:
    Read-only.

Example:
    $ python tools/check-version/check_version.py --version-file VERSION \
        --allow CHANGELOG.md --allow uv.lock
    check-version: 1.2.3 appears only in allowed files
"""

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path


def tracked_files() -> list[Path]:
    """List files tracked by git in the current repository.

    Args:
        None

    Returns:
        list[Path]: Tracked paths relative to the repository root.

    Raises:
        subprocess.CalledProcessError: If git fails (not a repository).
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git is resolved from PATH by design
        check=True,
        capture_output=True,
    ).stdout
    return [Path(p) for p in out.decode().split("\0") if p]


def find_copies(version: str, files: list[Path], allow: list[str]) -> list[str]:
    """Return ``path:line`` locations where ``version`` appears outside the allowlist.

    The match requires non-version characters on both sides so ``1.2.3``
    does not match inside ``11.2.3`` or ``1.2.31``.

    Args:
        version: Canonical version string.
        files: Candidate files.
        allow: Glob patterns of files permitted to contain the version.

    Returns:
        list[str]: Offending locations.

    Raises:
        None
    """
    pattern = re.compile(rf"(?<![\w.]){re.escape(version)}(?![\w.]*\d)")
    hits: list[str] = []
    for path in files:
        if any(fnmatch.fnmatch(path.as_posix(), glob) for glob in allow):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable files cannot carry a text version
        hits += [
            f"{path.as_posix()}:{n}"
            for n, line in enumerate(text.splitlines(), 1)
            if pattern.search(line)
        ]
    return hits


def main(argv: list[str] | None = None) -> int:
    """Run the single-version-source gate.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        int: 0 when the version appears only in allowed files, 1 otherwise.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--allow", action="append", default=[])
    args = parser.parse_args(argv)
    version = args.version_file.read_text(encoding="utf-8").strip()
    allow = [args.version_file.as_posix(), *args.allow]
    hits = find_copies(version, tracked_files(), allow)
    for hit in hits:
        print(f"{hit}: hard-coded version {version}")
    if hits:
        return 1
    print(f"check-version: {version} appears only in allowed files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
