"""Only pseudonymized parallel-run reports are committed (MVP-ACC-03, MVP-ACC-04, E.34 decision 11).

A report built from clear field bundles names production devices and their
addresses, and the repository is public, so the owner decided on 2026-10-27
that only reports from pseudonymized bundles are committed. The report tool
already refuses to write a clear one outside the git-ignored inbox; this
check catches one that reached the repository another way, in `make check`.

Dependencies:
    subprocess (git ls-files); acc_render (the report's own wording).

Example:
    $ uv run pytest tests/unit/test_evidence_reports.py
"""

import json
import subprocess
from pathlib import Path

from acc_render import NAMES

ROOT = Path(__file__).resolve().parents[2]
ROW = "MVP-ACC-03: 5-minute traffic rates agree with MRTG within the tolerance |"
"""A table row only the Markdown report has."""


def _tracked(suffix: str) -> list[Path]:
    """List tracked files with one suffix.

    Args:
        suffix: Such as ``.md``.

    Returns:
        list[Path]: Absolute paths.

    Raises:
        subprocess.CalledProcessError: If git fails.
    """
    out = subprocess.run(  # noqa: S603 - a fixed read-only git command
        ["git", "ls-files", "-z", f"*{suffix}"],  # noqa: S607 - git from PATH, as check-version
        cwd=ROOT, check=True, capture_output=True,
    ).stdout  # fmt: skip
    return [ROOT / p for p in out.decode().split("\0") if p]


def _clear_json(path: Path) -> bool:
    """Report whether a JSON file is a parallel-run report without pseudonymized names.

    Args:
        path: A tracked JSON file.

    Returns:
        bool: True for a report whose ``pseudonymized`` is not ``true``.

    Raises:
        None
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError, ValueError, OSError:
        return False
    criteria = data.get("criteria") if isinstance(data, dict) else None
    is_report = isinstance(criteria, dict) and "MVP-ACC-03" in criteria
    return is_report and data.get("pseudonymized") is not True


def test_e34_no_clear_parallel_run_report_is_committed() -> None:
    """E.34 decision 11: every committed report says its names are pseudonymized.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clear = [p for p in _tracked(".json") if _clear_json(p)]
    for path in _tracked(".md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if ROW in text and NAMES[True] not in text:
            clear.append(path)
    assert clear == [], f"clear parallel-run reports are committed: {[str(p) for p in clear]}"


def test_e34_the_check_recognizes_both_kinds_of_report(tmp_path: Path) -> None:
    """The check finds a clear JSON report and passes a pseudonymized one.

    Args:
        tmp_path: Scratch directory.

    Returns:
        None

    Raises:
        None
    """
    report = {"criteria": {"MVP-ACC-03": "pass", "MVP-ACC-04": "pass"}}
    clear, pseudo, other = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    clear.write_text(json.dumps({**report, "pseudonymized": False}), encoding="utf-8")
    pseudo.write_text(json.dumps({**report, "pseudonymized": True}), encoding="utf-8")
    other.write_text(json.dumps({"criteria": "unrelated"}), encoding="utf-8")
    assert [_clear_json(p) for p in (clear, pseudo, other)] == [True, False, False]
