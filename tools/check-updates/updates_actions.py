# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Find GitHub Actions pins in workflows and compare them with the latest releases.

CI is part of the supply chain, so pinned actions age like any other
dependency. Pins come in two forms. A tag pin (``actions/checkout@v7``) is
compared at the precision it is written with: ``v7`` stays current while
7.y.z is the newest series. A commit pin (``astral-sh/setup-uv@<40 hex
digits> # v10.3.0``) cannot be compared by its SHA, so the version in its
trailing comment is used, the convention Dependabot also maintains. A
commit pin without such a comment, or a branch pin such as ``@main``, is
reported as unknown.

Dependencies:
    re: Line-oriented ``uses:`` parsing.
    updates_checks: Shared task model and row helpers.
    updates_local: Workflow discovery.

Safety:
    Workflow YAML is scanned line by line with regular expressions and is
    never evaluated. Read-only.

Example:
    >>> parse_uses("      - uses: actions/checkout@v7  # tag pin")
    [('actions/checkout@v7', 'tag pin')]
    >>> action_pin("org/act/sub@" + "a" * 40, "v1.2.3", "ci.yml").version
    'v1.2.3'
"""

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from updates_checks import Context, Outcome, Task, attempt, failed_task, make_row
from updates_local import discover
from updates_report import Result
from updates_versions import parse_version

DEFAULT_WORKFLOWS = [".github/workflows/*.yml", ".github/workflows/*.yaml"]
_USES = re.compile(
    r"""^\s*(?:-\s*)?uses:\s*(?P<q>["']?)(?P<target>[^\s"'#]+)(?P=q)\s*(?:\#\s*(?P<note>.*?))?\s*$"""
)
_COMMENT_VERSION = re.compile(r"(?<![\w.])(v?\d+(?:\.\d+)*(?:-[0-9A-Za-z.]+)?)")
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class ActionPin:
    """One ``uses:`` reference to a published action or reusable workflow.

    Attributes:
        repo: ``OWNER/REPO`` that publishes releases.
        ref: The text after ``@``: a tag, branch, or commit SHA.
        version: Version to compare: the tag itself, or the version in the
            comment after a SHA. Empty when there is none.
        file: Repository-relative workflow path.
    """

    repo: str
    ref: str
    version: str
    file: str

    @property
    def is_sha(self) -> bool:
        """Report whether the action is pinned to a full commit SHA.

        Args:
            None

        Returns:
            bool: True for a 40-hex-digit ref.

        Raises:
            None
        """
        return bool(_SHA.match(self.ref))


def parse_uses(text: str) -> list[tuple[str, str]]:
    """Find ``uses:`` targets and their trailing comments in workflow text.

    Args:
        text: Contents of one workflow file.

    Returns:
        list[tuple[str, str]]: ``(target, comment)`` pairs in file order;
        the comment is empty when absent.

    Raises:
        None
    """
    return [
        (m["target"], m["note"] or "") for line in text.splitlines() if (m := _USES.match(line))
    ]


def action_pin(target: str, note: str, file: str) -> ActionPin | None:
    """Interpret one ``uses:`` target as a versioned action reference.

    Args:
        target: Text after ``uses:``, such as ``owner/repo/path@ref``.
        note: Trailing comment, which may name the version of a SHA pin.
        file: Workflow path, kept for the report.

    Returns:
        ActionPin | None: The pin, or None for local (``./``) actions,
        ``docker://`` images, and targets without ``@ref``.

    Raises:
        None
    """
    if target.startswith(("./", "docker://")) or "@" not in target:
        return None
    spec, ref = target.rsplit("@", 1)
    repo = "/".join(spec.split("/")[:2])
    if _SHA.match(ref):
        found = _COMMENT_VERSION.search(note)
        version = found[1] if found else ""
    else:
        version = ref
    return ActionPin(repo, ref, version, file)


def action_pins(root: Path, patterns: list[str]) -> list[ActionPin]:
    """Collect every versioned action reference from matching workflow files.

    Args:
        root: Repository root.
        patterns: Workflow globs, such as ``.github/workflows/*.yml``.

    Returns:
        list[ActionPin]: Pins in file and line order.

    Raises:
        OSError: If a workflow file cannot be read.
    """
    pins = []
    for path in discover(root, patterns):
        rel = path.relative_to(root).as_posix()
        for target, note in parse_uses(path.read_text(encoding="utf-8")):
            if pin := action_pin(target, note, rel):
                pins.append(pin)
    return pins


def check_action(ctx: Context, pin: ActionPin, files: list[str]) -> list[Result]:
    """Compare one GitHub Actions pin with the action's latest release.

    Args:
        ctx: Task context.
        pin: Parsed ``uses:`` reference.
        files: Every workflow that uses this exact pin.

    Returns:
        list[Result]: One row.

    Raises:
        None
    """
    item = f"{pin.repo}@{pin.ref[:7] if pin.is_sha else pin.ref}"
    if pin.is_sha and not pin.version:
        current: Outcome = ("", "pinned to a commit without a '# vX.Y.Z' comment")
    elif parse_version(pin.version) is None:
        current = ("", f"ref {pin.ref!r} is a branch or non-version tag")
    else:
        current = (pin.version, "")
    latest = attempt(lambda: ctx.upstream.lookup("github", pin.repo))
    return [make_row(item, "action", current, latest, note="used in " + ", ".join(files))]


def plan_actions(ctx: Context, cfg: Mapping[str, Any]) -> list[Task]:
    """Plan one task per distinct action pin across all workflows.

    Args:
        ctx: Task context.
        cfg: The ``[actions]`` table.

    Returns:
        list[Task]: Tasks in first-use order.

    Raises:
        None
    """
    try:
        pins = action_pins(ctx.root, cfg.get("workflows", DEFAULT_WORKFLOWS))
    except (OSError, ValueError) as exc:
        return [failed_task("GitHub Actions", "action", str(exc))]
    users: dict[ActionPin, list[str]] = {}
    for pin in pins:
        key = ActionPin(pin.repo, pin.ref, pin.version, "")
        if pin.file not in users.setdefault(key, []):
            users[key].append(pin.file)
    return [
        Task(pin.repo, "action", functools.partial(check_action, ctx, pin, files))
        for pin, files in users.items()
    ]
