"""Bounds of admin operations (Appendix E.32, API-011, DISC-010).

Every list, wait, and table of operations is bounded here, in one place,
so the blueprint table and the code cannot drift apart. None of these is an
operator option.

Dependencies:
    datetime: Durations.

Safety:
    Constants only.

Example:
    >>> MAX_ACTIVE, PICKUP_LIMIT.total_seconds()
    (1000, 3600.0)
"""

from datetime import timedelta

MAX_ACTIVE = 1_000
"""Operations queued or running at once; a request beyond them is refused."""
PICKUP_LIMIT = timedelta(hours=1)
"""A queued operation no collector claims within this becomes ``expired``."""
KEEP_FINISHED = timedelta(days=30)
"""Finished operations are purged this long after they ended."""
MAX_FINISHED = 10_000
"""Finished operations kept at most; older ones are purged first."""
PURGE_BATCH = 500
"""Rows one purge statement deletes at most."""
PURGE_BATCHES = 10
"""Purge statements one pass runs at most, so a large backlog drains over several passes."""
PICKUP_READ = 100
"""Queued operations one collector pass reads, oldest first."""
RECOVER_READ = 100
"""Running operations of this collector read at start, per statement."""
COLLECTOR_PURGE_INTERVAL_S = 3600.0
"""How often the collector purges finished operations."""
WAIT_POLL_S = 1.0
"""How often ``vakr inventory rediscover --wait`` reads the operation."""
WAIT_DEFAULT_S = 300.0
WAIT_MAX_S = 3600.0
"""``--timeout`` default and maximum, in seconds (minimum 1)."""

EXPIRED_REASON = "no collector picked this up within 1 h; is vakr-collector running?"
RESTART_REASON = "the collector restarted during the rediscovery"
NOT_HERE_REASON = (
    "this collector does not discover the device: it is disabled, retired, icmp_only, "
    "or has no SNMP credential"
)
