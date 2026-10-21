"""Compact telemetry defaults from Appendix E.21 and ADR-021, in one place.

Every bound the telemetry adapter applies is named here with the Appendix
E entry it comes from, so a reviewer can compare code and blueprint at a
glance. None of these are operator options yet; retention durations come
from configuration (``telemetry.native_retention_days`` and
``telemetry.rollup_retention_days``) through ``retention.retention_policy``.

Dependencies:
    datetime: Durations.
    vakr.domain.downsampling: The downsampling ladder, shared by every backend.

Safety:
    Plain constants; every PRAGMA value is an integer formatted into a fixed
    statement, never caller text.

Example:
    >>> MOVE_LAG.total_seconds(), WAL_WARN_BYTES // 2**20
    (120.0, 256)
"""

from datetime import timedelta

from vakr.domain.downsampling import LADDER_S as DOWNSAMPLE_LADDER_S

MAX_BATCH_ROWS = 50_000
"""Observations per ``write_batch`` (E.19, the STORE-011 upper target)."""

MOVE_LAG = timedelta(minutes=2)
"""A move takes staged rows older than ``now`` minus this (E.21)."""

MOVE_INTERVAL = timedelta(minutes=15)
"""How often the service loop should call ``maintain`` (E.21)."""

MOVER_LAG_WARN = 2 * MOVE_INTERVAL
"""Health warns when the oldest staged row is older than this (E.21: 30 min)."""

WAL_WARN_BYTES = 256 * 2**20
"""Health warns when the WAL file is larger than this (E.21: 256 MiB)."""

CACHE_KIB = 65_536
"""Page cache per connection, KiB (E.21: 64 MiB)."""

JOURNAL_SIZE_LIMIT = 64 * 2**20
"""WAL size left after a checkpoint reset, bytes (E.21)."""

WAL_AUTOCHECKPOINT = 1_000
"""Pages of WAL that trigger a passive checkpoint (E.21)."""

POLL_LOOKBACK = timedelta(days=1)
"""How far before an interface sample its poll record is searched for (E.21)."""

POLL_LOOKAHEAD = timedelta(minutes=5)
"""How far after an interface sample its poll record is searched for (E.21)."""

LADDER_S = DOWNSAMPLE_LADDER_S
"""Downsampling bucket widths in seconds, narrowest first (ADR-021, E.21); shared by backends."""

QUANTIZED_ROLLUPS = frozenset({"roll_iface"})
"""Rollup families whose values may be rounded to float32 (E.21, ADR-021 amendment 3)."""

PRAGMAS = (
    "PRAGMA synchronous = FULL",
    f"PRAGMA cache_size = -{CACHE_KIB}",
    f"PRAGMA journal_size_limit = {JOURNAL_SIZE_LIMIT}",
    f"PRAGMA wal_autocheckpoint = {WAL_AUTOCHECKPOINT}",
    "PRAGMA temp_store = MEMORY",
)
"""Per-connection settings on top of the shared WAL, foreign-key, and busy-timeout ones."""
