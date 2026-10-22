"""The observation half of the ``replication`` check: lag, backlog, gaps (MPOS-006, E.13).

- OK: no partner URL (nothing to pull), or the newest observation stored
  from every partner collector is younger than ``WARN_AFTER`` (5 minutes).
- WARNING: no observation pulled yet, a pull failing for under
  ``CRIT_AFTER``, a lag of 5 to 30 minutes, or gaps found (the partner had
  already discarded observations this node never received, MPOS-005).
- CRITICAL: a lag of ``CRIT_AFTER`` (30 minutes) or more.

The lag is the age of the newest partner observation held here; it also
grows when the partner itself stops polling, which is node health as well.
A replication failure never changes a device's state (MPOS-006).

Dependencies:
    vakr.replication.observation_status: The record the collector writes.

Safety:
    The summary names the partner URL and the error, never the token.

Example:
    >>> judge(None, None, 0.0).status.name
    'WARNING'
"""

from datetime import timedelta

from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.replication.observation_status import ObservationPullStatus

NAME = "replication"
WARN_AFTER = timedelta(minutes=5)
CRIT_AFTER = timedelta(minutes=30)


def judge(record: ObservationPullStatus | None, lag: float | None, since: float) -> CheckResult:
    """Rank the observation pull record.

    Args:
        record: The record, or ``None`` before the first pull.
        lag: Seconds behind the partner's newest observation, or ``None``.
        since: Seconds since the newest successful pull (0 if it just ran).

    Returns:
        CheckResult: OK, WARNING, or CRITICAL.

    Raises:
        None
    """
    if record is None or lag is None:
        why = f": {record.last_error}" if record is not None and record.last_error else ""
        return CheckResult(
            NAME, Status.WARNING, f"no observation pulled from the other position yet{why}"
        )
    backlog = sum(s.backlog for s in record.sources.values())
    gaps = sum(s.gaps for s in record.sources.values())
    perf = (
        PerfDatum("replication_observation_lag_s", round(lag, 1), "s"),
        PerfDatum("replication_backlog", backlog),
        PerfDatum("replication_gaps", gaps),
    )
    word = (
        Status.CRITICAL
        if lag >= CRIT_AFTER.total_seconds()
        else Status.WARNING
        if lag >= WARN_AFTER.total_seconds() or gaps
        else Status.OK
    )
    failing = (
        f"; newest attempt failed {since:.0f} s after the last success: {record.last_error}"
        if record.last_error
        else ""
    )
    if record.last_error and word is Status.OK:
        word = Status.WARNING
    where = ", ".join(sorted({s.position for s in record.sources.values() if s.position}))
    text = (
        f"observations from {where or 'the other position'} {lag:.0f} s behind, backlog "
        f"{backlog}, gaps {gaps}{failing}"
    )
    return CheckResult(NAME, word, text, perf)
