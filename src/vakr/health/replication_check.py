"""The ``replication`` health check: how old a peer's metadata is (MPOS-006, E.13).

- OK: not an installed node; a primary (a single node is one), which pulls
  nothing; or a peer whose newest successful metadata pull is younger than
  ``WARN_AFTER`` (5 minutes).
- WARNING: a peer with no pull recorded yet, or whose newest success is
  5 to 30 minutes old.
- CRITICAL: a peer whose newest success is ``CRIT_AFTER`` (30 minutes) or
  more old, or that never succeeded since its record began.

A failing pull is node health: the peer keeps polling every device with its
last-known metadata, and no target ever changes state because of it. On a
node with a partner URL the observation pull is judged too
(``vakr.health.observation_check``), and the worse of the two answers.
A primary whose partner reported itself a primary at the newest
observation pull is CRITICAL: two primaries (``vakr node demote``, E.39).
Once that answer is older than 5 minutes with nothing newer, it is a
WARNING naming when the partner was last seen as a primary.

Dependencies:
    vakr.replication.status: The pull record the collector writes.
    vakr.config: The node settings.

Safety:
    Reads one small JSON file; the summary names the primary and the error,
    never the token.

Example:
    >>> judge(None, 0.0).status.name
    'WARNING'
"""

from datetime import datetime, timedelta

from vakr.config.loader import ConfigError, load_effective_config
from vakr.config.node import NodeSettings, node_settings
from vakr.health import observation_check
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.replication import observation_status as obs_status
from vakr.replication import status
from vakr.replication.conflict import conflict_at
from vakr.replication.status import PullStatus

NAME = "replication"
WARN_AFTER = timedelta(minutes=5)
CRIT_AFTER = timedelta(minutes=30)
DOC = "see docs/operations/two-positions.md"


def check_replication() -> CheckResult:
    """Age this node's newest successful metadata pull, on a peer.

    Args:
        None

    Returns:
        CheckResult: As the module docstring describes.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    if not paths.state_dir.exists(follow_symlinks=False):
        return CheckResult(NAME, Status.OK, f"no {paths.state_dir}; not an installed vakr node")
    try:
        node = node_settings(load_effective_config(paths.config_dir))
    except ConfigError:
        return CheckResult(NAME, Status.UNKNOWN, "the configuration cannot be read; see config")
    metadata = None
    if node.is_peer:
        record = status.read_status(paths.state_dir / status.FILE)
        metadata = judge(record, _age(record))
    if not node.partner_url:
        return metadata or CheckResult(NAME, Status.OK, f"{node.role} node without a partner; "
                                       "it replicates nothing")  # fmt: skip
    found = obs_status.read_status(paths.state_dir / obs_status.FILE)
    now = SystemClock().now_utc()
    since = _since(found, now)
    observed = observation_check.judge(found, None if found is None else found.lag_s(now), since)
    result = observed if metadata is None else _worse(metadata, observed)
    conflict = two_primaries_check(paths, node, now)
    return result if conflict is None else _worse(conflict, result)


def two_primaries_check(paths: InstallPaths, node: NodeSettings,
                        now: datetime) -> CheckResult | None:  # fmt: skip
    """Report two primaries: CRITICAL while fresh, WARNING once stale (E.39).

    Two primaries each accept metadata edits that the other never copies, and
    the edits on the one demoted later are discarded; that needs the operator
    now, so it is CRITICAL, not WARNING. Evidence older than ``FRESH_FOR``
    with no newer answer (the old primary switched off) is a WARNING that
    says when it was last seen. It is node health only (MPOS-006).

    Args:
        paths: The installation layout.
        node: This node's settings.
        now: Current time (UTC).

    Returns:
        CheckResult | None: The result, or ``None`` without a conflict.

    Raises:
        None
    """
    found = conflict_at(paths.state_dir, node, now)
    if found is None:
        return None
    if not found.critical:
        return CheckResult(NAME, Status.WARNING, found.text)
    return CheckResult(NAME, Status.CRITICAL, found.text, (PerfDatum("replication_primaries", 2),))


def _since(record: obs_status.ObservationPullStatus | None, now: datetime) -> float:
    """Return seconds since the newest successful observation pull.

    Args:
        record: The record.
        now: Current time.

    Returns:
        float: Seconds; 0 without a recorded success.

    Raises:
        None
    """
    if record is None or record.last_success_at is None:
        return 0.0
    return (now - datetime.fromisoformat(record.last_success_at)).total_seconds()


def _worse(first: CheckResult, second: CheckResult) -> CheckResult:
    """Combine the metadata and observation answers: the worse status, both summaries.

    Args:
        first: The metadata answer.
        second: The observation answer.

    Returns:
        CheckResult: The combined result.

    Raises:
        None
    """
    rank = (Status.OK, Status.WARNING, Status.UNKNOWN, Status.CRITICAL)
    word = max(first.status, second.status, key=rank.index)
    return CheckResult(
        NAME, word, f"{first.summary}; {second.summary}", (*first.perfdata, *second.perfdata)
    )


def _age(record: PullStatus | None) -> float | None:
    """Return the newest success's age in seconds.

    Args:
        record: The pull record.

    Returns:
        float | None: The age, or ``None``.

    Raises:
        None
    """
    return None if record is None else record.success_age_s(SystemClock().now_utc())


def judge(record: PullStatus | None, age: float | None) -> CheckResult:
    """Rank a peer's pull record.

    Args:
        record: The record, or ``None`` when the collector wrote none yet.
        age: Seconds since its newest success, or ``None``.

    Returns:
        CheckResult: OK, WARNING, or CRITICAL.

    Raises:
        None
    """
    if record is None:
        return CheckResult(NAME, Status.WARNING, "no metadata pull recorded yet; the collector "
                           f"pulls from the primary every 60 s; {DOC}")  # fmt: skip
    why = f"; newest attempt failed: {record.last_error}" if record.last_error else ""
    if age is None:
        return CheckResult(NAME, Status.CRITICAL, f"no metadata pull has succeeded{why}; {DOC}")
    perf = (PerfDatum("replication_metadata_age_s", round(age, 1), "s"),)
    word = (Status.CRITICAL if age >= CRIT_AFTER.total_seconds()
            else Status.WARNING if age >= WARN_AFTER.total_seconds() else Status.OK)  # fmt: skip
    source = f" from {record.primary_position}" if record.primary_position else ""
    return CheckResult(NAME, word, f"metadata{source} {age:.0f} s old{why}", perf)
