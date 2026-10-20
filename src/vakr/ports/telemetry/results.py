"""Value types the telemetry ports exchange: capabilities, write and retention results.

Backends differ: ClickHouse has native TTLs, SQLite Compact drops daily
partitions, and a backend under construction may not have rollups yet. Those
differences are declared once in ``TelemetryCapabilities`` and read by the
code that cares, instead of ``if backend == "sqlite"`` checks spread through
the application (5.2 rule 5). The contract suites read the same object to
decide which shared tests apply.

Dependencies:
    vakr.domain: IDs, timestamps, and invariant checks.

Safety:
    Results carry counts and times only.

Example:
    >>> from vakr.domain.ids import BatchId
    >>> caps = TelemetryCapabilities(rollups=False, max_batch_rows=50_000)
    >>> caps.rollups, WriteResult(new_id(BatchId), 3, 1).total
    (False, 4)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.domain.checks import require_bool, require_int, require_uuid
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId
from vakr.domain.timestamps import require_optional_utc, require_utc


@dataclass(frozen=True, slots=True)
class TelemetryCapabilities:
    """What a telemetry backend can do (5.2 rule 5).

    Attributes:
        rollups: ``RollupRepository`` stores and serves buckets, series use
            rollups before the native horizon, and retention waits for rollup
            coverage. ``False`` makes DB-006 not applicable.
        max_batch_rows: Largest batch ``write_batch`` accepts.
        time_partitioned: Native data is stored in time partitions (Compact).
    """

    rollups: bool
    max_batch_rows: int
    time_partitioned: bool = False

    def __post_init__(self) -> None:
        """Validate the capabilities.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_bool(self.rollups, "rollups")
        require_int(self.max_batch_rows, "max_batch_rows", minimum=1)
        require_bool(self.time_partitioned, "time_partitioned")


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Outcome of one idempotent batch write (STORE-009, STORE-010).

    Attributes:
        batch_id: The batch written.
        inserted: Observations stored for the first time.
        duplicates: Observations whose ID was already stored; not rewritten.
    """

    batch_id: BatchId
    inserted: int
    duplicates: int

    def __post_init__(self) -> None:
        """Validate the result.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a count is negative.
        """
        require_uuid(self.batch_id, "batch_id")
        require_int(self.inserted, "inserted", minimum=0)
        require_int(self.duplicates, "duplicates", minimum=0)

    @property
    def total(self) -> int:
        """Return the number of observations in the batch.

        Args:
            None

        Returns:
            int: ``inserted + duplicates``.

        Raises:
            None
        """
        return self.inserted + self.duplicates


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How long each tier is kept (RET-001..003, Appendix E.10).

    Attributes:
        native: Native observation retention (30 days in Compact, 24 h in Lite).
        rollup: Rollup retention (400 days in Compact).
    """

    native: timedelta
    rollup: timedelta

    def __post_init__(self) -> None:
        """Validate the policy.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a duration is not positive.
        """
        if self.native <= timedelta(0) or self.rollup <= timedelta(0):
            raise ValidationError("retention durations must be positive")


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """What one retention pass removed (DB-009, RET-007).

    Attributes:
        applied_at: The ``now`` the pass used (UTC).
        native_cutoff: Native observations before this were removed, or ``None``
            when none were eligible (no rollup coverage yet).
        rollup_cutoff: Rollup buckets ending at or before this were removed.
        native_removed: Native observations removed.
        rollups_removed: Rollup buckets removed.
    """

    applied_at: datetime
    native_cutoff: datetime | None
    rollup_cutoff: datetime | None
    native_removed: int
    rollups_removed: int

    def __post_init__(self) -> None:
        """Validate the report.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_utc(self.applied_at, "applied_at")
        require_optional_utc(self.native_cutoff, "native_cutoff")
        require_optional_utc(self.rollup_cutoff, "rollup_cutoff")
        require_int(self.native_removed, "native_removed", minimum=0)
        require_int(self.rollups_removed, "rollups_removed", minimum=0)
