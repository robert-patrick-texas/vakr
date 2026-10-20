"""``vakr config validate`` rejects unsafe keywords and inconsistent scheduler settings.

Dependencies:
    pytest: Temporary configuration directories and output capture.

Example:
    $ uv run pytest tests/unit/config/test_validate_keywords.py
"""

from pathlib import Path

import pytest

from vakr.cli.main import main


def _write(config_dir: Path, keyword: str) -> None:
    """Write a config.toml with one extra interface keyword.

    Args:
        config_dir: Directory to write into.
        keyword: The keyword pattern under test.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written.
    """
    (config_dir / "config.toml").write_text(
        "config_version = 1\n\n[[interface_classification.keywords]]\n"
        f"keyword = '{keyword}'\ntag = 'x'\n",
        encoding="utf-8",
    )


def test_unsafe_regex_keyword_fails_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A catastrophic-backtracking ``re:`` keyword is caught before services start.

    Args:
        tmp_path: Temporary configuration directory.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    _write(tmp_path, "re:(a+)+")
    assert main(["config", "validate", "--config-dir", str(tmp_path)]) == 1
    assert "unsafe or invalid interface keyword" in capsys.readouterr().out


def test_safe_keyword_validates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An ordinary operator keyword passes.

    Args:
        tmp_path: Temporary configuration directory.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    _write(tmp_path, "TRANSIT")
    assert main(["config", "validate", "--config-dir", str(tmp_path)]) == 0
    assert "configuration valid" in capsys.readouterr().out


def test_inconsistent_scheduler_settings_fail_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``late_after`` beyond ``max_start_delay`` is a cross-key error (E.25, SCHED-017).

    Args:
        tmp_path: Temporary configuration directory.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "config.toml").write_text(
        "config_version = 1\n\n[scheduler]\nlate_after = '60s'\nmax_start_delay = '30s'\n",
        encoding="utf-8",
    )
    assert main(["config", "validate", "--config-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "scheduler.late_after (1m) must not exceed scheduler.max_start_delay (30s)" in out
    assert "inconsistent scheduler settings" in out


def test_inconsistent_spool_settings_fail_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Out-of-order spool capacity thresholds are caught by validation (E.24, STORE-005).

    Args:
        tmp_path: Temporary configuration directory.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "config.toml").write_text(
        "config_version = 1\n\n[spool]\nwarning_pct = 90\ncritical_pct = 80\n",
        encoding="utf-8",
    )
    assert main(["config", "validate", "--config-dir", str(tmp_path)]) == 1
    assert "inconsistent spool settings" in capsys.readouterr().out
