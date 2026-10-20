# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Turn a verified release into an annotated tag, a pushed tag, and a GitHub release.

These are the only irreversible steps of a release, so they are built as
plain command lists first. A dry run prints them; a real run executes them in
order and stops at the first failure. Release notes come from the matching
``CHANGELOG.md`` section, so what operators read on GitHub is exactly what
the repository records (VER-004).

Dependencies:
    re, shlex: Find the changelog section; quote commands for display.
    release_proc: Runs ``git`` and ``gh`` with argument lists.

Safety:
    Nothing here runs unless the caller passes ``dry_run=False``. Commands
    never pass through a shell, so notes and titles cannot inject arguments.

Example:
    >>> changelog_notes("## 1.2.3\n\n- Fixed it.\n## 1.2.2\n", "1.2.3")
    '- Fixed it.'
"""

import re
import shlex
from pathlib import Path

from release_config import ReleaseConfig
from release_proc import Runner
from release_version import ReleaseVersion


def changelog_notes(text: str, version: str) -> str | None:
    """Extract the body of the changelog section for ``version``.

    Accepts headings such as ``## 1.2.3``, ``## [1.2.3]``, ``## v1.2.3``,
    and any of those followed by a date.

    Args:
        text: Changelog Markdown.
        version: Version as written in ``VERSION``.

    Returns:
        str | None: The section body without its heading, stripped; ``None``
        when the section is missing or empty.

    Raises:
        None
    """
    heading = re.compile(rf"^##\s+\[?v?{re.escape(version)}\]?(?=\s|$)")
    body: list[str] | None = None
    for line in text.splitlines():
        if body is None:
            body = [] if heading.match(line) else None
        elif line.startswith("## "):
            break
        else:
            body.append(line)
    notes = "\n".join(body or []).strip()
    return notes or None


def publish_commands(
    cfg: ReleaseConfig,
    version: ReleaseVersion,
    notes: Path,
    files: list[Path],
) -> list[list[str]]:
    """Build the tag, push, and release commands, in execution order.

    Args:
        cfg: Release configuration.
        version: Version being released.
        notes: File holding the release notes.
        files: Artifacts plus ``SHA256SUMS`` and its signature, to attach.

    Returns:
        list[list[str]]: Argument lists for ``git tag``, ``git push``, and
        ``gh release create``.

    Raises:
        release_version.VersionError: If the configured prerelease rule is
            unknown.
    """
    title = f"{cfg.package} {version.semver}"
    release = ["gh", "release", "create", version.tag, "--repo", cfg.repo]
    release += ["--title", title, "--notes-file", str(notes), "--verify-tag"]
    if version.is_prerelease(cfg.prerelease):
        release.append("--prerelease")
    return [
        ["git", "tag", "--annotate", version.tag, "--message", title],
        ["git", "push", cfg.remote, f"refs/tags/{version.tag}"],
        [*release, *(str(path) for path in files)],
    ]


def publish(runner: Runner, commands: list[list[str]], *, dry_run: bool) -> None:
    """Execute (or, in a dry run, only print) the publishing commands.

    Args:
        runner: Command runner in the repository.
        commands: Output of ``publish_commands``.
        dry_run: Print instead of executing.

    Returns:
        None

    Raises:
        release_proc.CommandError: If a command fails; later commands are
            not run, so a failed push never leads to a release.
    """
    for argv in commands:
        if dry_run:
            print(f"  would run: {shlex.join(argv)}")
        else:
            print(f"  running: {shlex.join(argv[:4])} ...")
            runner.run(argv, capture=False)
