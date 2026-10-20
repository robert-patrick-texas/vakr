"""Tests for the post-update health settle window and new-option summary.

Dependencies:
    pytest: Output capture and parametrization.

Example:
    $ uv run pytest tests/unit/test_update_settle.py
"""

import pytest

from vakr.cli import cmd_check
from vakr.cli.main import main
from vakr.cli.selfupdate_extras import health_hook, new_options_lines
from vakr.health.model import CheckResult, Status
from vakr.lifecycle.update.process import RunResult


@pytest.mark.parametrize(
    ("flag", "env", "expected"),
    [
        (None, {}, 0),
        (None, {"VAKR_HEALTH_RETRY_FOR": "45"}, 45),
        (5, {"VAKR_HEALTH_RETRY_FOR": "45"}, 5),
        (None, {"VAKR_HEALTH_RETRY_FOR": "junk"}, 0),
        (9999, {}, 600),
        (-3, {}, 0),
    ],
)
def test_retry_window_prefers_flag_then_env_and_is_capped(
    flag: int | None, env: dict[str, str], expected: int
) -> None:
    """The flag wins over the environment; bad values mean "check once" (UPD-006).

    Args:
        flag: ``--retry-for`` value.
        env: Environment to read.
        expected: Resulting window in seconds.

    Returns:
        None

    Raises:
        None
    """
    assert cmd_check.retry_window(flag, env) == expected


class _Scripted:
    """A check function returning scripted statuses in turn, counting calls.

    Instances are mutable test doubles; the last status repeats forever.
    """

    def __init__(self, *statuses: Status) -> None:
        """Remember the statuses to return.

        Args:
            *statuses: Overall status of each successive evaluation.

        Returns:
            None

        Raises:
            None
        """
        self.statuses = statuses
        self.calls = 0

    def __call__(self) -> tuple[CheckResult, ...]:
        """Return the next scripted evaluation.

        Args:
            None

        Returns:
            tuple[CheckResult, ...]: One result with the scripted status.

        Raises:
            None
        """
        self.calls += 1
        status = self.statuses[min(self.calls, len(self.statuses)) - 1]
        return (CheckResult("svc", status, status.name),)


def test_settle_retries_while_critical_then_stops_at_recovery() -> None:
    """A restart that looks CRITICAL briefly is not reported as a failure.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    now = [0.0]
    check = _Scripted(Status.CRITICAL, Status.CRITICAL, Status.OK)
    results = cmd_check.settle(
        30, check, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s)
    )
    assert results[0].status is Status.OK
    assert check.calls == 3


def test_settle_gives_up_after_window_and_never_retries_warning() -> None:
    """A real failure is reported once the window expires; WARNING is not retried.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    now = [0.0]
    tick = lambda s: now.__setitem__(0, now[0] + s)  # noqa: E731 - tiny fake clock
    stuck = _Scripted(Status.CRITICAL)
    assert cmd_check.settle(5, stuck, clock=lambda: now[0], sleep=tick)[0].status is Status.CRITICAL
    assert now[0] == pytest.approx(5.0)
    warning = _Scripted(Status.WARNING)
    assert (
        cmd_check.settle(30, warning, clock=lambda: now[0], sleep=tick)[0].status is Status.WARNING
    )
    assert warning.calls == 1


def test_check_health_accepts_retry_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr check health --retry-for 0`` behaves like a single check.

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["check", "health", "--retry-for", "0"]) in {0, 1, 2, 3}
    assert capsys.readouterr().out.startswith("VAKR ")


def test_health_hook_passes_settle_through_environment() -> None:
    """Older binaries ignore the variable, so explicit downgrades still work.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    hook = health_hook("/opt/vakr/venv/bin/vakr", 30)
    assert hook.argv == (
        "/usr/bin/env",
        "VAKR_HEALTH_RETRY_FOR=30",
        "/opt/vakr/venv/bin/vakr",
        "check",
        "health",
    )
    assert hook.ok_codes == frozenset({0, 1, 3})
    assert health_hook("/x", -5).argv[1] == "VAKR_HEALTH_RETRY_FOR=0"


def test_new_options_lines_lists_only_real_additions() -> None:
    """The update summary shows new options and stays quiet otherwise (CFGM-004).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    seen: list[tuple[str, ...]] = []

    def fake(stdout: str, code: int = 0):
        """Return a runner that records argv and yields ``stdout``.

        Args:
            stdout: Output to return.
            code: Exit status to return.

        Returns:
            Callable: Fake command runner.

        Raises:
            None
        """

        def run(argv: tuple[str, ...]) -> RunResult:
            """Record the command and return the scripted result.

            Args:
                argv: Command line.

            Returns:
                RunResult: Scripted outcome.

            Raises:
                None
            """
            seen.append(tuple(argv))
            return RunResult(code, stdout)

        return run

    listing = "options added since 1.4.0: 1\n  update (1): health_settle_seconds\n"
    lines = new_options_lines("/v", "1.4.0", fake(listing))
    assert seen[-1] == ("/v", "config", "new-options", "--since", "1.4.0")
    assert "1 new configuration options since 1.4.0 (packaged defaults apply):" in lines
    assert "  update (1): health_settle_seconds" in lines
    assert lines[-1].endswith("vakr config new-options --annotate")
    assert new_options_lines("/v", "1.4.1", fake("options added since 1.4.1: 0\n")) == []
    assert new_options_lines("/v", "1.4.0", fake("", 3)) == []
    assert new_options_lines("/v", "", fake(listing)) == []
