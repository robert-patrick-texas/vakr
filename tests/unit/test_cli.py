"""Unit tests for the ``vakr`` CLI skeleton and self-health rendering (OPS-001).

Dependencies:
    pytest: Captures output and exit codes.

Example:
    $ uv run pytest tests/unit/test_cli.py
"""

import argparse

import pytest

from vakr.cli.main import COMMAND_MODULES, build_parser, main
from vakr.health.model import CheckResult, PerfDatum, Status, nagios_line, worst
from vakr.health.registry import run_checks
from vakr.version import UNKNOWN_VERSION, get_version


def test_version_flag_prints_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr --version`` reports the version read from package metadata (VER-001).

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"vakr {get_version()}"
    assert get_version() != UNKNOWN_VERSION


def test_every_listed_command_registers() -> None:
    """Each module in ``COMMAND_MODULES`` contributes exactly one top-level command.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    parser = build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(actions) == 1
    assert len(actions[0].choices) == len(COMMAND_MODULES)


def test_no_command_prints_help_and_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Running ``vakr`` with no command is a usage error, not a silent success.

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main([]) == 2
    assert "commands" in capsys.readouterr().err


def test_check_health_is_nagios_formatted(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr check health`` prints one plugin line and returns its exit code (OPS-001).

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    code = main(["check", "health"])
    line = capsys.readouterr().out.strip()
    assert line.startswith("VAKR ")
    assert "\n" not in line
    assert code in {0, 1, 2, 3}


def test_worst_prefers_critical_over_unknown() -> None:
    """A real failure is never masked by an unrelated UNKNOWN result.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    results = (
        CheckResult("a", Status.UNKNOWN, "x"),
        CheckResult("b", Status.CRITICAL, "y"),
        CheckResult("c", Status.WARNING, "z"),
    )
    assert worst(results) is Status.CRITICAL
    assert worst(()) is Status.OK


def test_nagios_line_lists_problems_first_with_perfdata() -> None:
    """Problems lead the line so truncated notifications still show them.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    results = (
        CheckResult("ok", Status.OK, "fine", (PerfDatum("a", 1.0),)),
        CheckResult("bad", Status.WARNING, "spool 80%", (PerfDatum("spool_pct", 80.0, "%"),)),
    )
    assert nagios_line(results) == "VAKR WARNING - bad: spool 80%; ok: fine | a=1.0 spool_pct=80.0%"


def test_failing_check_becomes_unknown_without_leaking_message() -> None:
    """A check that raises yields UNKNOWN and reports only the exception type.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    def check_broken() -> CheckResult:
        """Simulate a check whose exception message carries sensitive text.

        Args:
            None

        Returns:
            CheckResult: Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("community=s3cr3t")

    (result,) = run_checks((check_broken,))
    assert result.status is Status.UNKNOWN
    assert result.name == "broken"
    assert "s3cr3t" not in result.summary


def test_doctor_prints_one_line_per_check(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr doctor`` lists every registered check with its severity (SVC-007).

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["doctor"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(run_checks())
    assert lines[0].startswith("[OK      ] version:")


def test_placeholder_command_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    """Unfinished commands say so and exit 3 instead of pretending to succeed.

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["tls"]) == 3
    assert "not implemented" in capsys.readouterr().err
