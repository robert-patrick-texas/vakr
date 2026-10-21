"""The login throttle schedule (SEC-012, AUTH-004, Appendix E.12).

Dependencies:
    pytest; vakr.auth.throttle.

Example:
    $ uv run pytest tests/unit/auth/test_throttle.py
"""

from datetime import timedelta

import pytest

from tests.unit.auth.auth_testkit import SOURCE, T0
from vakr.auth.throttle import ThrottlePolicy, delay_after, wait_remaining
from vakr.domain.users import LoginFailure

KEY = "a" * 64


@pytest.mark.parametrize(
    ("failures", "delay"),
    [(0, 0.0), (1, 0.0), (4, 0.0), (5, 1.0), (6, 2.0), (7, 4.0), (8, 8.0), (9, 16.0),
     (10, 30.0), (11, 30.0), (10_000, 30.0)],
)  # fmt: skip
def test_auth004_schedule_starts_at_the_fifth_failure_and_caps_at_30s(
    failures: int, delay: float
) -> None:
    """AUTH-004, E.12: four failures cost nothing; then 1 s doubling, capped at 30 s.

    Args:
        failures: Consecutive failures.
        delay: Expected delay in seconds.

    Returns:
        None

    Raises:
        None
    """
    assert delay_after(failures, ThrottlePolicy()) == delay


def test_schedule_follows_configured_start_and_cap() -> None:
    """SEC-012: ``auth.login_throttle_after`` and ``login_throttle_max_delay`` shape the curve.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = ThrottlePolicy(start=3, max_delay_s=5.0)
    assert [delay_after(n, policy) for n in range(2, 7)] == [0.0, 1.0, 2.0, 4.0, 5.0]


def test_auth004_wait_counts_down_and_quiet_runs_are_forgotten() -> None:
    """AUTH-004: the wait shrinks with elapsed time and is gone after the reset window.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = ThrottlePolicy()
    run = LoginFailure(KEY, SOURCE, 7, T0, T0)  # next attempt waits 4 s
    assert wait_remaining(None, T0, policy) == 0.0
    assert wait_remaining(run, T0, policy) == 4.0
    assert wait_remaining(run, T0 + timedelta(seconds=1.5), policy) == 2.5
    assert wait_remaining(run, T0 + timedelta(seconds=4), policy) == 0.0
    long_run = LoginFailure(KEY, SOURCE, 50, T0, T0)
    assert wait_remaining(long_run, T0 + timedelta(minutes=14, seconds=59), policy) == 0.0
    assert wait_remaining(long_run, T0 + timedelta(seconds=10), policy) == 20.0
    assert wait_remaining(LoginFailure(KEY, SOURCE, 4, T0, T0), T0, policy) == 0.0
