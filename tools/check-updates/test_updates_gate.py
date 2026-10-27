# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests of recorded exceptions and the release gate (TOOLS-004, TOOLS-005).

A hold keeps an item on its last-known-good version only while the
release that failed testing is still the newest; a newer release reopens
it. ``--gate`` fails for anything a release must not ship past: an
available update, a row nobody could check, or an exception that no
longer matches. These tests drive both through pure functions and through
``main`` with an offline fetcher (TOOLS-T06, TOOLS-T07).

Dependencies:
    pytest: ``tmp_path``, ``capsys``, and parametrisation.
    check_updates, updates_exceptions: Code under test.

Safety:
    Offline: every request is answered by ``FakeFetcher``. Writes only
    beneath ``tmp_path``.

Example:
    $ uv run pytest tools/check-updates/test_updates_gate.py
"""

import json
from pathlib import Path
from typing import Any

import check_updates
import pytest
from updates_config import ConfigError
from updates_exceptions import Hold, apply_exceptions, load_exceptions
from updates_report import Result
from updates_versions import CURRENT, HELD, UNKNOWN, UPDATE, Version

PYPI = "https://pypi.org/pypi"
HOLD = Hold(
    "fastapi", Version("0.162.0", (0, 162, 0)), "make check: 3 API tests fail", "2026-10-27"
)
EXCEPTION = """
[[exception]]
item = "fastapi"
version = "0.162.0"
reason = "make check: 3 API tests fail"
recorded = 2026-10-27
"""


def _row(current: str, latest: str, status: str) -> Result:
    """Build a fastapi row.

    Args:
        current: Locked version.
        latest: Newest release.
        status: Row status before exceptions apply.

    Returns:
        Result: The row.

    Raises:
        None
    """
    return Result("fastapi", "pypi", current, latest, status)


def test_hold_applies_only_while_the_failed_release_is_newest() -> None:
    """Held at the failed release; a newer one reopens the item; unknown is untouched (TOOLS-T06).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a state is wrong.
    """
    (held,), problems = apply_exceptions([_row("0.159.1", "0.162.0", UPDATE)], [HOLD])
    assert (held.status, problems) == (HELD, [])
    assert "0.162.0 failed testing on 2026-10-27 (make check: 3 API tests fail)" in held.detail
    assert "staying on 0.159.1" in held.detail
    (newer,), problems = apply_exceptions([_row("0.159.1", "0.163.0", UPDATE)], [HOLD])
    assert (newer.status, problems) == (UPDATE, [])
    assert newer.detail.startswith("0.163.0 is newer than 0.162.0 failed testing")
    # An unchecked row stays unknown, unjudged, for the gate to stop on.
    unknown = Result("fastapi", "pypi", "0.159.1", "", UNKNOWN, "", "timed out")
    assert apply_exceptions([unknown], [HOLD]) == ([unknown], [])


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (_row("0.162.0", "0.162.0", CURRENT), "no longer applies; remove it"),
        (_row("0.159.1", "0.161.0", UPDATE), "newer than the latest release 0.161.0"),
        (Result("starlette", "lockfile", "1.9.0", "1.10.0", UPDATE), "not in the report"),
    ],
)
def test_stale_exceptions_are_problems(row: Result, problem: str) -> None:
    """An exception with nothing to hold, a wrong version, or no item is reported (TOOLS-T06).

    Args:
        row: The only row in the report.
        problem: Expected end of the problem text.

    Returns:
        None

    Raises:
        AssertionError: If the problem is missed.
    """
    (kept,), problems = apply_exceptions([row], [HOLD])
    assert kept.status == row.status
    assert len(problems) == 1
    assert problem in problems[0]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (EXCEPTION.replace("reason", "why"), "unknown key(s) why"),
        (EXCEPTION.replace('"make check: 3 API tests fail"', '""'), "'reason' must be"),
        (EXCEPTION.replace("2026-10-27", '"2026-10-27"'), "'recorded' must be a date"),
        (EXCEPTION.replace('"0.162.0"', '"main"'), "cannot parse version 'main'"),
        (EXCEPTION + EXCEPTION, "'fastapi' has more than one exception"),
        ("[exception]\nitem = 1\n", "'exception' must be a list of tables"),
        ("holds = []\n", "unknown key(s) holds"),
    ],
)
def test_invalid_exceptions_files_are_refused(tmp_path: Path, text: str, error: str) -> None:
    """A malformed exceptions file is a configuration error, never a dropped hold (TOOLS-T06).

    Args:
        tmp_path: Directory for the file.
        text: File content.
        error: Expected part of the message.

    Returns:
        None

    Raises:
        AssertionError: If the file loads.
    """
    path = tmp_path / "exceptions.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=error.replace("(", r"\(").replace(")", r"\)")):
        load_exceptions(path)


def _repo(root: Path, exceptions: str | None) -> list[str]:
    """Write a one-dependency repository and return the arguments to check it.

    Args:
        root: Empty directory.
        exceptions: Exceptions-file content, or None for no ``[exceptions]``.

    Returns:
        list[str]: ``--config`` and ``--root`` arguments.

    Raises:
        OSError: If a file cannot be written.
    """
    (root / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi>=0.150"]\n')
    (root / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "fastapi"\nversion = "0.159.1"\n'
    )
    config = '[pypi]\n[[tools]]\nname = "gitleaks"\ngithub = "gitleaks/gitleaks"\n'
    if exceptions is not None:
        (root / "holds.toml").write_text(exceptions, encoding="utf-8")
        config += '[exceptions]\nfile = "holds.toml"\n'
    (root / "check-updates.toml").write_text(config, encoding="utf-8")
    return ["--config", str(root / "check-updates.toml"), "--root", str(root)]


def _fetcher(fake_fetcher: Any, fastapi: str | None) -> Any:
    """Answer PyPI for fastapi (or fail like an outage) and GitHub for gitleaks.

    Args:
        fake_fetcher: ``FakeFetcher`` class.
        fastapi: Newest fastapi release, or None for a timeout.

    Returns:
        Any: The fetcher.

    Raises:
        None
    """
    gh = "https://api.github.com/repos/gitleaks/gitleaks/releases?per_page=10"
    responses: dict[str, Any] = {gh: [{"tag_name": "v8.30.1"}]}
    if fastapi:
        responses[f"{PYPI}/fastapi/json"] = {"info": {"version": fastapi}}
    return fake_fetcher(responses)


@pytest.mark.parametrize(
    ("exceptions", "fastapi", "gate", "outdated"),
    [
        (None, "0.159.1", 0, 0),  # current, plus a reference row
        (EXCEPTION, "0.162.0", 0, 0),  # held
        (None, "0.162.0", 1, 1),  # update available
        (EXCEPTION, "0.163.0", 1, 1),  # newer than the failed release: retest
        (None, None, 1, 0),  # unknown: --fail-on-outdated lets an outage pass
        (EXCEPTION, "0.159.1", 1, 0),  # obsolete exception
    ],
)
def test_gate_exit_codes(
    tmp_path: Path,
    fake_fetcher: Any,
    exceptions: str | None,
    fastapi: str | None,
    gate: int,
    outdated: int,
) -> None:
    """--gate fails on updates, unknowns, and stale holds; held and reference pass (TOOLS-T07).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        exceptions: Exceptions file, or None.
        fastapi: Newest fastapi release, or None for an outage.
        gate: Expected exit status with ``--gate``.
        outdated: Expected exit status with ``--fail-on-outdated``.

    Returns:
        None

    Raises:
        AssertionError: If an exit status differs.
    """
    argv = _repo(tmp_path, exceptions)
    assert check_updates.main([*argv, "--gate"], _fetcher(fake_fetcher, fastapi)) == gate
    flag = "--fail-on-outdated"
    assert check_updates.main([*argv, flag], _fetcher(fake_fetcher, fastapi)) == outdated


def test_gate_line_names_the_blockers(
    tmp_path: Path, fake_fetcher: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """The last line says why the gate failed; JSON lists exception problems (TOOLS-T07).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the verdict or the problems are missing.
    """
    argv = _repo(tmp_path, EXCEPTION)
    assert check_updates.main([*argv, "--gate"], _fetcher(fake_fetcher, "0.162.0")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2] == (
        "check-updates: 2 items: 0 current, 0 update available, 1 held, 1 reference, 0 unknown"
    )
    assert lines[-1] == "check-updates: gate passed"
    assert check_updates.main([*argv, "--gate"], _fetcher(fake_fetcher, "0.159.1")) == 1
    out = capsys.readouterr().out
    assert "exception: fastapi: no update is pending" in out
    assert out.splitlines()[-1] == "check-updates: gate failed: 1 exception problem(s)"
    assert check_updates.main([*argv, "--json"], _fetcher(fake_fetcher, None)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["unknown"] == 1
    assert report["problems"] == []
    assert check_updates.main([*argv, "--gate"], _fetcher(fake_fetcher, None)) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "check-updates: gate failed: 1 unknown (fastapi)"
    )


def test_missing_exceptions_file_exits_2(tmp_path: Path, fake_fetcher: Any) -> None:
    """A configured but absent exceptions file stops the run before any fetch (TOOLS-T05).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If the run proceeds.
    """
    argv = _repo(tmp_path, EXCEPTION)
    (tmp_path / "holds.toml").unlink()
    fetcher = fake_fetcher()
    assert check_updates.main([*argv, "--gate"], fetcher) == 2
    assert fetcher.requests == []
