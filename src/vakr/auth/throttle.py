"""Login throttling: the progressive-delay schedule (SEC-012, Appendix E.12).

Failed logins are counted per attempted account *and* client address, so
one noisy address cannot lock a real user out from everywhere, and one
account guessed from many addresses is still slowed at each. The first
four consecutive failures cost nothing; from the fifth on, the next
attempt must wait 1 s, then 2, 4, 8, 16 s, and at most 30 s. A run that
stays quiet for ``reset_after_s`` (15 minutes) starts again at zero, and a
successful login clears it.

These are pure functions: the login service decides, and the HTTP layer
applies the wait asynchronously (it never blocks the event loop).

Dependencies:
    dataclasses, datetime: The policy record and time arithmetic.
    vakr.domain.users: The failure-run record.

Safety:
    Nothing here knows whether an account exists, so the schedule is the
    same for real and invented usernames (AUTH-004).

Example:
    >>> [delay_after(n, ThrottlePolicy()) for n in (4, 5, 6, 9, 10)]
    [0.0, 1.0, 2.0, 16.0, 30.0]
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.domain.users import LoginFailure


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    """When delays start, how long they grow, and when a run is forgotten (E.12).

    Attributes:
        start: The consecutive failure that first earns a delay.
        max_delay_s: Longest delay, in seconds.
        reset_after_s: Quiet time after which a run restarts, in seconds.
    """

    start: int = 5
    max_delay_s: float = 30.0
    reset_after_s: float = 900.0

    @property
    def reset_after(self) -> timedelta:
        """``reset_after_s`` as a ``timedelta``.

        Args:
            None

        Returns:
            timedelta: The quiet time.

        Raises:
            None
        """
        return timedelta(seconds=self.reset_after_s)


def delay_after(failures: int, policy: ThrottlePolicy) -> float:
    """Seconds the next attempt must wait after ``failures`` consecutive failures.

    Args:
        failures: Consecutive failures so far.
        policy: The schedule.

    Returns:
        float: 0 below ``policy.start``; then 1 s doubling per failure, capped.

    Raises:
        None
    """
    if failures < policy.start:
        return 0.0
    # The exponent is clamped first so a long run cannot build a huge integer.
    return min(policy.max_delay_s, float(2 ** min(failures - policy.start, 32)))


def wait_remaining(run: LoginFailure | None, now: datetime, policy: ThrottlePolicy) -> float:
    """Seconds an attempt arriving at ``now`` must still wait.

    Args:
        run: The current failure run, or ``None``.
        now: Arrival time (UTC).
        policy: The schedule.

    Returns:
        float: 0 when the attempt may proceed; otherwise the remaining wait.

    Raises:
        None
    """
    if run is None or now - run.last_failed_at >= policy.reset_after:
        return 0.0
    elapsed = (now - run.last_failed_at).total_seconds()
    return max(0.0, delay_after(run.failures, policy) - elapsed)
