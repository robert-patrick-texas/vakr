# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Build the release notes' versions table (VER-007).

Every release lists Python and the prominent components and dependencies
it ships or is built with. The previous release's own published table is
the record of what it had, so once one release carries a table, the next
shows two columns, old and new, with each changed version in bold. Values
come from what the release really contains: the interpreter version is
read from the built package, locked packages from the lockfiles, and
toolchain pins from the files that pin them.

Dependencies:
    tarfile, tomllib, json, fnmatch: Read ``.deb`` members, ``uv.lock``,
        and ``package-lock.json`` with the standard library (Python 3.14+
        reads zstd-compressed ``data.tar.zst``).
    release_notes_config: The configured rows.

Safety:
    Read-only. An archive member is read only up to ``MEMBER_LIMIT`` bytes,
    and the ``ar`` scan stops after ``AR_MEMBERS`` entries, so a malformed
    package cannot exhaust memory.

Example:
    >>> render_versions([("Python", "3.15.2")], None)[1]
    '| Component | Version |'
"""

import fnmatch
import io
import json
import re
import tarfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

from release_notes_config import NotesError, VersionItem

MEMBER_LIMIT = 16 * 1024 * 1024
"""Largest archive member read for a version (bytes)."""
AR_MEMBERS = 16
"""A ``.deb`` has three ``ar`` members; scanning further means it is not one."""
HEADING = "### Versions"
"""Heading of the table, also how the next release finds it in these notes."""


def read_versions(
    items: Sequence[VersionItem], root: Path, expand: Callable[[str], str]
) -> list[tuple[str, str]]:
    """Read every configured row's version.

    Args:
        items: Configured rows, in table order.
        root: Repository root.
        expand: Fills placeholders such as ``{deb_version}`` in a ``deb`` path.

    Returns:
        list[tuple[str, str]]: ``(name, version)`` per row, in order.

    Raises:
        NotesError: If a file, package, or member is missing, or a pattern
            does not match.
    """
    return [(item.name, _version(item, root, expand)) for item in items]


def _version(item: VersionItem, root: Path, expand: Callable[[str], str]) -> str:
    """Read one row's version from its source.

    Args:
        item: Configured row.
        root: Repository root.
        expand: Placeholder filler for ``deb`` paths.

    Returns:
        str: The version, stripped.

    Raises:
        NotesError: If the source cannot be read or does not hold a version.
    """
    try:
        if item.source == "lock":
            return _locked(root / item.path, item.package or "")
        if item.source == "deb":
            matches = sorted(root.glob(expand(item.path)))
            if len(matches) != 1:
                raise NotesError(f"{item.name}: {item.path} matches {len(matches)} files, not 1")
            text = deb_member(matches[0], item.member or "").decode("utf-8", "replace")
        else:
            text = (root / item.path).read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        raise NotesError(f"{item.name}: cannot read {item.path}: {exc}") from exc
    match = item.regex.search(text) if item.regex else None
    if match is None:
        raise NotesError(f"{item.name}: pattern not found in {item.path}")
    return (match.group(1) if match.groups() else match.group(0)).strip()


def _locked(path: Path, package: str) -> str:
    """Return a package's locked version from ``uv.lock`` or ``package-lock.json``.

    Args:
        path: The lockfile; ``.json`` means npm, anything else uv.
        package: Package name.

    Returns:
        str: The locked version.

    Raises:
        NotesError: If the package is not in the lockfile.
        OSError, ValueError: If the file cannot be read or parsed.
    """
    if path.suffix == ".json":
        entry = json.loads(path.read_text(encoding="utf-8"))["packages"].get(
            f"node_modules/{package}"
        )
        found = [entry["version"]] if entry else []
    else:
        packages = tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])
        found = [p["version"] for p in packages if p.get("name") == package and "version" in p]
    if len(found) != 1:
        raise NotesError(f"{package}: {len(found)} locked versions in {path.name}, not 1")
    return str(found[0])


def deb_member(deb: Path, pattern: str) -> bytes:
    """Return the first data member of a ``.deb`` whose path matches ``pattern``.

    Args:
        deb: Debian package (an ``ar`` archive holding ``data.tar.*``).
        pattern: ``fnmatch`` glob such as ``*/python/lib/python3.*/x.json``.

    Returns:
        bytes: The member's content, at most ``MEMBER_LIMIT`` bytes.

    Raises:
        NotesError: If the file is not a ``.deb`` or nothing matches.
        OSError, tarfile.TarError: If the archive cannot be read.
    """
    with deb.open("rb") as handle:
        if handle.read(8) != b"!<arch>\n":
            raise NotesError(f"{deb.name} is not an ar archive")
        for _ in range(AR_MEMBERS):
            header = handle.read(60)
            if len(header) < 60:
                break
            name, size = header[:16].decode().strip().rstrip("/"), int(header[48:58])
            if name.startswith("data.tar"):
                return _tar_member(handle.read(size), pattern, deb.name)
            handle.seek(size + size % 2, io.SEEK_CUR)
    raise NotesError(f"{deb.name} has no data.tar member")


def _tar_member(data: bytes, pattern: str, label: str) -> bytes:
    """Return the first member of a (compressed) tar archive matching ``pattern``.

    Args:
        data: The tar archive, compressed or not.
        pattern: ``fnmatch`` glob for the member path.
        label: Package name for error messages.

    Returns:
        bytes: The member's content, at most ``MEMBER_LIMIT`` bytes.

    Raises:
        NotesError: If no regular file matches.
        tarfile.TarError: If the archive is malformed.
    """
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for info in tar:
            if info.isfile() and fnmatch.fnmatchcase(info.name, pattern):
                member = tar.extractfile(info)
                if member is not None:
                    return member.read(MEMBER_LIMIT)
    raise NotesError(f"{label} has no member matching {pattern}")


def previous_versions(body: str) -> dict[str, str]:
    """Parse the versions table out of a published release's notes.

    Args:
        body: Release notes of the previous release.

    Returns:
        dict[str, str]: Row name to the version that release shipped (the
        last column, without bold); empty when it has no table.

    Raises:
        None
    """
    rows: dict[str, str] = {}
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == HEADING), None)
    for line in lines[start + 1 :] if start is not None else []:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.lstrip().startswith("|"):
            if rows:
                break
            continue
        if len(cells) >= 2 and cells[0] != "Component" and not re.fullmatch(r"[-: ]+", cells[0]):
            rows[cells[0]] = cells[-1].replace("**", "")
    return rows


def render_versions(
    rows: Sequence[tuple[str, str]], previous: tuple[str, dict[str, str]] | None, current: str = ""
) -> list[str]:
    """Render the versions table, with old and new columns once a previous table exists.

    Args:
        rows: ``(name, version)`` for this release.
        previous: ``(previous version, its rows)``, or ``None`` when no
            earlier release published a table.
        current: This release's version, for the column heading.

    Returns:
        list[str]: Markdown lines: heading, blank line, table.

    Raises:
        None
    """
    if not previous:
        return [HEADING, "", "| Component | Version |", "|---|---|"] + [
            f"| {name} | {value} |" for name, value in rows
        ]
    old_version, old = previous
    lines = [HEADING, "", f"| Component | Old ({old_version}) | New ({current}) |", "|---|---|---|"]
    for name, value in rows:
        before = old.get(name, "—")
        lines.append(f"| {name} | {before} | {value if before == value else f'**{value}**'} |")
    names = {name for name, _ in rows}
    lines += [
        f"| {name} | {value} | **removed** |" for name, value in old.items() if name not in names
    ]
    return lines
