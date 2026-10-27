# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Hold an item on its last-known-good version after its newest release failed testing.

A project that adopts every release as soon as it is published, whatever
its age, still meets releases that break it. The only accepted reason to
stay behind is such a failure, recorded as an exception (TOOLS-004): the
report ITEM, the release that failed, what failed, and when. While that
release is still the newest, its row is "held" and passes the release gate.
As soon as a newer release is published, the row is "update available"
again, so every hold is revisited with the dependency's next release
instead of being forgotten. The exceptions file is named by the
``[exceptions]`` section of ``check-updates.toml``.

Dependencies:
    tomllib, datetime: Standard library, to read the TOML file and its dates.
    updates_versions: Version parsing and ordering.

Safety:
    Reads one TOML file and never writes. A malformed file is a
    configuration error, never a silently ignored hold.

Example:
    >>> row = Result("typescript", "npm", "6.0.3", "7.0.2", "update available")
    >>> hold = Hold("typescript", parse_version("7.0.2"), "lint fails", "2026-10-27")
    >>> apply_exceptions([row], [hold])[0][0].status
    'held'
"""

import dataclasses
import datetime
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from updates_config import ConfigError
from updates_report import Result
from updates_versions import HELD, UNKNOWN, UPDATE, Version, parse_version

_KEYS = frozenset({"item", "version", "reason", "recorded"})


@dataclass(frozen=True, slots=True)
class Hold:
    """One recorded exception.

    Attributes:
        item: The report ITEM it applies to, exactly as the report prints it.
        version: The newest release that failed testing.
        reason: What failed: the test, command, or error.
        recorded: ISO date of the failed test.
    """

    item: str
    version: Version
    reason: str
    recorded: str


def _parse_hold(entry: Any, where: str) -> Hold:
    """Validate one ``[[exception]]`` table.

    Args:
        entry: Parsed TOML value.
        where: Location for error messages, such as ``exception[0]``.

    Returns:
        Hold: The validated exception.

    Raises:
        ConfigError: On a missing, unknown, or mistyped key.
    """
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be a table")
    if unknown := sorted(set(entry) - _KEYS):
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
    for key in ("item", "version", "reason"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ConfigError(f"{where}: '{key}' must be a non-empty string")
    version = parse_version(entry["version"])
    if version is None:
        raise ConfigError(f"{where}: cannot parse version {entry['version']!r}")
    recorded = entry.get("recorded")
    if not isinstance(recorded, datetime.date) or isinstance(recorded, datetime.datetime):
        raise ConfigError(f"{where}: 'recorded' must be a date such as 2026-10-27")
    return Hold(entry["item"], version, entry["reason"].strip(), recorded.isoformat())


def load_exceptions(path: Path) -> list[Hold]:
    """Read and validate an exceptions file.

    Args:
        path: TOML file of ``[[exception]]`` tables.

    Returns:
        list[Hold]: The exceptions, in file order.

    Raises:
        ConfigError: If the file cannot be read or parsed, has an invalid
            entry, or names one item twice.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    if unknown := sorted(set(data) - {"exception"}):
        raise ConfigError(f"{path.name}: unknown key(s) {', '.join(unknown)}")
    entries = data.get("exception", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{path.name}: 'exception' must be a list of tables")
    holds = [_parse_hold(entry, f"{path.name}: exception[{i}]") for i, entry in enumerate(entries)]
    seen: set[str] = set()
    for hold in holds:
        if hold.item in seen:
            raise ConfigError(f"{path.name}: {hold.item!r} has more than one exception")
        seen.add(hold.item)
    return holds


def _judge(row: Result, hold: Hold) -> tuple[Result, str]:
    """Apply one exception to one matching row.

    Args:
        row: A row whose item is ``hold.item``.
        hold: The exception.

    Returns:
        tuple[Result, str]: The row (held, explained, or unchanged), and a
        problem with the exception, or an empty string.

    Raises:
        None
    """
    failed = f"{hold.version.text} failed testing on {hold.recorded} ({hold.reason})"
    latest = parse_version(row.latest)
    if row.status == UNKNOWN or latest is None:
        return row, ""
    if row.status != UPDATE:
        return row, (
            f"{row.item}: no update is pending, so the exception for "
            f"{hold.version.text} no longer applies; remove it"
        )
    newest, held = latest.sort_key(), hold.version.sort_key()
    if newest == held:
        detail = f"{failed}; staying on {row.current} until a newer release is published"
        return dataclasses.replace(row, status=HELD, detail=detail), ""
    if newest > held:
        detail = f"{row.latest} is newer than {failed}: test it, then update or move the exception"
        return dataclasses.replace(row, detail=detail), ""
    return row, (
        f"{row.item}: the exception names {hold.version.text}, "
        f"newer than the latest release {row.latest}; correct it"
    )


def apply_exceptions(
    results: Sequence[Result], holds: Sequence[Hold]
) -> tuple[list[Result], list[str]]:
    """Mark held rows and find exceptions that no longer match the report (TOOLS-004).

    Args:
        results: Report rows in order.
        holds: Recorded exceptions.

    Returns:
        tuple[list[Result], list[str]]: The rows, with held ones marked and
        retest hints added, and one problem per exception that names no
        reported item, has nothing left to hold, or names a release newer
        than the latest.

    Raises:
        None
    """
    by_item = {hold.item: hold for hold in holds}
    rows, problems = [], []
    for row in results:
        hold = by_item.get(row.item)
        if hold is None:
            rows.append(row)
            continue
        judged, problem = _judge(row, hold)
        rows.append(judged)
        if problem:
            problems.append(problem)
    reported = {row.item for row in results}
    problems += [
        f"{hold.item}: not in the report; correct the item name or remove the exception"
        for hold in holds
        if hold.item not in reported
    ]
    return rows, problems
