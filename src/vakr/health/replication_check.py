"""The ``replication`` health check: how old a peer's metadata is (MPOS-006, E.13).

- OK: not an installed node; a primary (a single node is one), which pulls
  nothing; or a peer whose newest successful metadata pull is younger than
  ``WARN_AFTER`` (5 minutes).
- WARNING: a peer with no pull recorded yet, or whose newest success is
  5 to 30 minutes old.
- CRITICAL: a peer whose newest success is ``CRIT_AFTER`` (30 minutes) or
  more old, or that never succeeded since its record began.

A failing pull is node health: the peer keeps polling every device with its
last-known metadata, and no target ever changes state because of it.

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

from datetime import timedelta

from vakr.config.loader import ConfigError, load_effective_config
from vakr.config.node import node_settings
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.replication import status
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
    if not node.is_peer:
        return CheckResult(NAME, Status.OK, f"{node.role} node; it pulls no metadata")
    record = status.read_status(paths.state_dir / status.FILE)
    return judge(record, _age(record))


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
