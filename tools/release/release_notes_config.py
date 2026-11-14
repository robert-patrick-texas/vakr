# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Validate the ``[notes]`` table of ``release.toml``: release-note layout and facts.

Every release's notes follow one layout (VER-005): a one-to-three sentence
summary, then the topics New, Changed, Fixed, and Additional Notes in that
order, skipping empty ones, then a lines-of-code table (VER-006) and a
versions table (VER-007). Which topics exist, which files cloc must not
count, and which components the versions table lists are project data,
so they live here rather than in the code (blueprint section 29).

Dependencies:
    re: Each configured version pattern is compiled when it loads.

Safety:
    Read-only. A typo stops the release before anything is built, because a
    wrong table published on GitHub is worse than no release.

Example:
    >>> cfg = parse_notes({"sections": ["New"], "loc_exclude": []}, Path("/repo"))
    >>> cfg.sections, cfg.loc_command
    (('New',), ('cloc',))
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_KEYS = {
    "sections",
    "summary_sentences",
    "held_file",
    "held_section",
    "loc_command",
    "loc_exclude",
    "versions",
}
_SOURCES = {
    "file": ("file", "regex"),
    "lock": ("lock", "package"),
    "deb": ("deb", "member", "regex"),
}
"""Each versions item reads exactly one source: a repository file, a lockfile, or a
member of a built ``.deb``, with the keys each one needs."""


class NotesError(ValueError):
    """The notes configuration is invalid, or a fact cannot be determined."""


@dataclass(frozen=True, slots=True)
class VersionItem:
    """One row of the versions table and where its version comes from.

    Attributes:
        name: Row label; also the key that matches the previous release's row.
        source: ``file``, ``lock``, or ``deb``.
        path: Repository file, lockfile, or artifact pattern (placeholders
            allowed for ``deb``), relative to the repository root.
        regex: Pattern whose first group (or whole match) is the version;
            ``None`` for a lockfile.
        package: Package name in the lockfile; ``None`` otherwise.
        member: Glob for the archive member of a ``deb`` source.
    """

    name: str
    source: str
    path: str
    regex: re.Pattern[str] | None = None
    package: str | None = None
    member: str | None = None


@dataclass(frozen=True, slots=True)
class NotesConfig:
    """Validated release-note layout and fact sources.

    Attributes:
        sections: Allowed ``###`` topic headings, in their required order.
        summary_sentences: Most sentences the summary may have.
        held_file: TOML file of ``[[exception]] item`` holds (TOOLS-004);
            ``None`` skips the held check.
        held_section: Topic that must name every held item.
        loc_command: cloc argument list, without file arguments.
        loc_exclude: ``fnmatch`` patterns of tracked files that are not
            first-party content (generated lockfiles, hash pins).
        versions: Rows of the versions table, in order.
    """

    sections: tuple[str, ...]
    summary_sentences: int = 3
    held_file: Path | None = None
    held_section: str = ""
    loc_command: tuple[str, ...] = ("cloc",)
    loc_exclude: tuple[str, ...] = ()
    versions: tuple[VersionItem, ...] = ()


def parse_notes(data: Mapping[str, Any], root: Path) -> NotesConfig:
    """Validate the ``[notes]`` table.

    Args:
        data: The parsed ``[notes]`` table.
        root: Repository root for ``held_file``.

    Returns:
        NotesConfig: The validated settings.

    Raises:
        NotesError: If it is not a table, on unknown or mistyped keys, an empty section list, a
            held file without a held section among the sections, or a bad
            versions item.
    """
    if not isinstance(data, Mapping):
        raise NotesError("[notes] must be a table")
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise NotesError(f"unknown [notes] keys: {', '.join(unknown)}")
    sections = _strings(data, "sections")
    if not sections:
        raise NotesError("[notes] sections must not be empty")
    count = data.get("summary_sentences", 3)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise NotesError("[notes] summary_sentences must be a positive integer")
    held = data.get("held_file")
    section = data.get("held_section", "")
    if held is not None and (not isinstance(held, str) or section not in sections):
        raise NotesError("[notes] held_file needs a held_section that is one of the sections")
    items = data.get("versions", [])
    if not isinstance(items, list):
        raise NotesError("[notes] versions must be an array of tables")
    rows = tuple(_item(item) for item in items)
    if len({row.name for row in rows}) != len(rows):
        raise NotesError("[notes] versions names must be unique")
    return NotesConfig(
        sections=sections,
        summary_sentences=count,
        held_file=root / held if held else None,
        held_section=section,
        loc_command=_strings(data, "loc_command") if "loc_command" in data else ("cloc",),
        loc_exclude=_strings(data, "loc_exclude") if "loc_exclude" in data else (),
        versions=rows,
    )


def _item(item: Any) -> VersionItem:
    """Validate one ``[[notes.versions]]`` table.

    Args:
        item: The parsed table.

    Returns:
        VersionItem: The validated row.

    Raises:
        NotesError: If the name is missing, the table names no source or
            more than one, a needed key is missing, or the regex is invalid.
    """
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
        raise NotesError("each [[notes.versions]] needs a non-empty name")
    name = item["name"]
    kinds = [kind for kind in _SOURCES if kind in item]
    if len(kinds) != 1:
        raise NotesError(f"versions item {name!r} needs exactly one of file, lock, deb")
    kind = kinds[0]
    allowed = {"name", *_SOURCES[kind]}
    if set(item) != allowed or not all(isinstance(item[k], str) and item[k] for k in allowed):
        raise NotesError(
            f"versions item {name!r} ({kind}) needs exactly: {', '.join(sorted(allowed))}"
        )
    try:
        regex = re.compile(item["regex"], re.MULTILINE) if "regex" in item else None
    except re.error as exc:
        raise NotesError(f"versions item {name!r}: bad regex: {exc}") from exc
    return VersionItem(name, kind, item[kind], regex, item.get("package"), item.get("member"))


def _strings(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """Return a list of non-empty strings from the table.

    Args:
        data: The ``[notes]`` table.
        key: Key to read.

    Returns:
        tuple[str, ...]: The values, in order.

    Raises:
        NotesError: If the value is missing or not a list of non-empty strings.
    """
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise NotesError(f"[notes] {key} must be a list of non-empty strings")
    return tuple(value)
