# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Check the release notes' layout and append the generated tables (VER-005..007).

A release's notes are its changelog section plus two tables the tool
generates. The section must open with a one-to-three sentence summary and
use only the configured topics, in order: for vakr New, Changed, Fixed,
and Additional Notes, skipping any with nothing to say. Every dependency
held back after a failed test is named under Additional Notes (TOOLS-004).
After the topics come the lines-of-code table and the versions table; the
versions table compares with the previous release's published table.

Dependencies:
    release_loc, release_versions: The two tables.
    release_notes_config: Layout and fact sources.
    release_proc, release_version: ``gh`` calls and version ordering.

Safety:
    Read-only toward GitHub: ``gh release list`` and ``gh release view``
    only, limited to ``RELEASE_LIMIT`` releases.

Example:
    >>> layout_problems(FIX_ONLY, NotesConfig(("New", "Fixed")))
    []
"""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from release_checks import Outcome
from release_config import ReleaseConfig
from release_loc import count_loc, render_loc
from release_notes_config import NotesConfig, NotesError
from release_proc import CommandError, Runner
from release_publish import changelog_notes
from release_version import ReleaseVersion, VersionError
from release_versions import previous_versions, read_versions, render_versions

FIX_ONLY = "One fix.\n\n### Fixed\n\n- The fix.\n"
"""A minimal valid section, for the example: a one-fix release shows only Fixed."""
RELEASE_LIMIT = 100
"""Releases listed when looking for the previous one."""
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\[(])")


def layout_problems(notes: str, cfg: NotesConfig, held: tuple[str, ...] = ()) -> list[str]:
    """Find every way a changelog section breaks the configured layout.

    Args:
        notes: The changelog section body, without its ``##`` heading.
        cfg: Notes configuration.
        held: Items held by recorded exceptions, each to be named under
            ``cfg.held_section``.

    Returns:
        list[str]: One message per problem; empty when the layout is right.

    Raises:
        None
    """
    problems: list[str] = []
    parts = re.split(r"^### +(.+?) *$", notes, flags=re.MULTILINE)
    summary, headings, bodies = parts[0].strip(), parts[1::2], parts[2::2]
    sentences = len(_SENTENCE_END.split(summary)) if summary else 0
    if not 1 <= sentences <= cfg.summary_sentences or "\n\n" in summary:
        problems.append(
            f"start with a 1-{cfg.summary_sentences} sentence summary paragraph ({sentences} found)"
        )
    order = [cfg.sections.index(h) if h in cfg.sections else -1 for h in headings]
    if not headings or -1 in order or order != sorted(set(order)):
        wanted = ", ".join(cfg.sections)
        problems.append(
            f"use topics {wanted}, in that order, each once ({', '.join(headings) or 'none'})"
        )
    bodies_by = dict(zip(headings, bodies, strict=True))
    for heading, body in bodies_by.items():
        if not body.strip():
            problems.append(f"topic {heading} is empty; leave it out")
    missing = [i for i in held if i.lower() not in bodies_by.get(cfg.held_section, "").lower()]
    if missing:
        problems.append(f"name held dependencies under {cfg.held_section}: {', '.join(missing)}")
    return problems


def held_items(path: Path | None) -> tuple[str, ...]:
    """Return the items of a TOOLS-004 exceptions file.

    Args:
        path: The exceptions file, or ``None`` for no check.

    Returns:
        tuple[str, ...]: Each ``[[exception]] item``; empty when ``path`` is ``None``.

    Raises:
        NotesError: If the file cannot be read or parsed.
    """
    if path is None:
        return ()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise NotesError(f"cannot read {path.name}: {exc}") from exc
    return tuple(
        str(entry.get("item", "")) for entry in data.get("exception", []) if entry.get("item")
    )


def previous_release(runner: Runner, repo: str, version: ReleaseVersion) -> tuple[str, str] | None:
    """Find the newest published release older than ``version`` and its notes.

    Args:
        runner: Command runner (``gh``).
        repo: GitHub ``OWNER/NAME``.
        version: The version being released.

    Returns:
        tuple[str, str] | None: ``(its version, its notes)``, or ``None``
        when there is no earlier release.

    Raises:
        NotesError: If ``gh`` fails or prints something unexpected.
    """
    listing = ["gh", "release", "list", "--repo", repo, "--exclude-drafts"]
    try:
        out = runner.run([*listing, "--limit", str(RELEASE_LIMIT), "--json", "tagName"]).stdout
        tags = [str(entry["tagName"]) for entry in json.loads(out or "[]")]
    except (CommandError, ValueError, KeyError, TypeError) as exc:
        raise NotesError(f"cannot list releases: {exc}") from exc
    older = [(v, tag) for tag in tags if (v := _parse(tag)) is not None and _key(v) < _key(version)]
    if not older:
        return None
    found, tag = max(older, key=lambda pair: _key(pair[0]))
    try:
        view = runner.run(["gh", "release", "view", tag, "--repo", repo, "--json", "body"]).stdout
        return found.semver, str(json.loads(view)["body"])
    except (CommandError, ValueError, KeyError, TypeError) as exc:
        raise NotesError(f"cannot read the notes of {tag}: {exc}") from exc


def compose(
    section: str,
    cfg: NotesConfig,
    runner: Runner,
    facts: tuple[str, ReleaseVersion, Callable[[str], str], Path],
) -> str:
    """Return the full release notes: the changelog section, then both tables.

    Args:
        section: The changelog section body.
        cfg: Notes configuration.
        runner: Command runner in the repository.
        facts: ``(repo, version, placeholder filler, private workdir)``.

    Returns:
        str: Markdown release notes ending with a newline.

    Raises:
        NotesError: If cloc, a version source, or the previous release's
            notes cannot be read.
    """
    repo, version, expand, workdir = facts
    counts, cloc_version, files = count_loc(runner, cfg, workdir)
    rows = read_versions(cfg.versions, runner.cwd, expand)
    previous = previous_release(runner, repo, version)
    old = previous_versions(previous[1]) if previous else {}
    prior = (previous[0], old) if previous and old else None
    loc = render_loc(counts, cloc_version, files, cfg.loc_exclude)
    table = render_versions(rows, prior, version.semver) if rows else []
    return "\n".join([section.strip(), "", *loc, "", *table]).rstrip() + "\n"


def layout_outcome(cfg: ReleaseConfig, notes: str | None) -> Outcome:
    """Check the changelog section against the configured release-note layout (VER-005).

    Args:
        cfg: Release configuration with a ``notes`` table.
        notes: The changelog section, or ``None`` when it is missing.

    Returns:
        Outcome: OK when the summary, topics, and held notes are right.

    Raises:
        None
    """
    name = "release notes layout"
    if cfg.notes is None or notes is None:
        return Outcome(name, cfg.notes is None, "no changelog section")
    try:
        problems = layout_problems(notes, cfg.notes, held_items(cfg.notes.held_file))
    except NotesError as exc:
        return Outcome(name, False, str(exc))
    return Outcome(name, not problems, "; ".join(problems) or "summary, then topics in order")


def notes_text(cfg: ReleaseConfig, runner: Runner, version: ReleaseVersion, workdir: Path) -> str:
    """Return the release notes: the changelog section, plus the tables when configured.

    Args:
        cfg: Release configuration.
        runner: Command runner in the repository.
        version: Version being released.
        workdir: Private temporary directory.

    Returns:
        str: The notes to publish.

    Raises:
        NotesError: If a table cannot be generated (VER-006, VER-007).
    """
    try:
        text = cfg.changelog.read_text(encoding="utf-8")
    except OSError:
        text = ""
    section = changelog_notes(text, version.semver) or ""
    if cfg.notes is None:
        return section
    facts = (cfg.repo, version, lambda pattern: cfg.expand(pattern, version), workdir)
    return compose(section, cfg.notes, runner, facts)


def _parse(tag: str) -> ReleaseVersion | None:
    """Parse a ``v``-prefixed release tag, or return ``None`` for another tag.

    Args:
        tag: Git tag such as ``v1.2.3``.

    Returns:
        ReleaseVersion | None: The version, or ``None`` when it is not one.

    Raises:
        None
    """
    try:
        return ReleaseVersion.parse(tag.removeprefix("v"))
    except VersionError:
        return None


def _key(version: ReleaseVersion) -> tuple[int, int, int, bool, int]:
    """Return a sort key: a release candidate sorts before its final release.

    Args:
        version: Version to order.

    Returns:
        tuple[int, int, int, bool, int]: Comparable key.

    Raises:
        None
    """
    return (version.major, version.minor, version.patch, version.rc is None, version.rc or 0)
