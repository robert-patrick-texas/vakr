"""Spool capacity: bytes in use and the NORMAL/WARNING/CRITICAL/EXHAUSTED states (6.4, SPOOL-T07).

When the telemetry database is unavailable, polling continues and the
spool grows (STORE-014). The fill level tells operators how long they have:
WARNING and CRITICAL are reported by ``vakr check health``; at EXHAUSTED the
collector stops scheduling new normal polling, so the remaining space
absorbs observations already in flight instead of losing them. A target
not polled for that reason is *not attempted* and unknown, never down
(invariant 2).

Dependencies:
    threading: The writer thread adds bytes while ingestion frees them.
    vakr.ingest.settings: Limits and thresholds.

Safety:
    Pure accounting; no I/O.

Example:
    >>> from pathlib import Path
    >>> from vakr.ingest.settings import SpoolSettings
    >>> usage = SpoolUsage(SpoolSettings.defaults(Path("/var/lib/vakr/spool")))
    >>> usage.add(3 * 1024 ** 3)
    >>> usage.state(), round(usage.used_pct(), 1)
    (<CapacityState.WARNING: 'warning'>, 75.0)
"""

import threading
from enum import StrEnum

from vakr.ingest.settings import SpoolSettings


class CapacityState(StrEnum):
    """Spool fill state (6.4). Ordered from least to most severe."""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"


def capacity_state(used_pct: float, settings: SpoolSettings) -> CapacityState:
    """Classify a fill level by the configured thresholds.

    Args:
        used_pct: Bytes in use as a percentage of the size limit.
        settings: Thresholds.

    Returns:
        CapacityState: The state; a threshold value itself belongs to the higher state.

    Raises:
        None
    """
    if used_pct >= settings.exhausted_pct:
        return CapacityState.EXHAUSTED
    if used_pct >= settings.critical_pct:
        return CapacityState.CRITICAL
    if used_pct >= settings.warning_pct:
        return CapacityState.WARNING
    return CapacityState.NORMAL


class SpoolUsage:
    """Thread-safe count of the bytes a spool holds on disk.

    Segment bytes change with every append and deletion; quarantine bytes
    are re-measured from disk, because an operator removes those files by
    hand.
    """

    def __init__(self, settings: SpoolSettings, segment_bytes: int = 0) -> None:
        """Start counting.

        Args:
            settings: Size limit and thresholds.
            segment_bytes: Bytes already held in segment files.

        Returns:
            None

        Raises:
            None
        """
        self._settings = settings
        self._lock = threading.Lock()
        self._segments = segment_bytes
        self._quarantine = 0

    def add(self, count: int) -> None:
        """Account bytes appended to a segment.

        Args:
            count: Bytes written.

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._segments += count

    def remove(self, count: int) -> None:
        """Account bytes freed by deleting or truncating a segment.

        Args:
            count: Bytes removed.

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._segments = max(0, self._segments - count)

    def set_quarantine(self, count: int) -> None:
        """Record the measured size of the quarantine directory.

        Args:
            count: Bytes in quarantined files.

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._quarantine = count

    @property
    def total(self) -> int:
        """Bytes the spool holds, quarantine included.

        Args:
            None

        Returns:
            int: Total bytes.

        Raises:
            None
        """
        with self._lock:
            return self._segments + self._quarantine

    def fits(self, count: int) -> bool:
        """Report whether ``count`` more bytes stay within the size limit.

        Args:
            count: Bytes about to be written.

        Returns:
            bool: ``True`` if the total would not exceed ``max_bytes``.

        Raises:
            None
        """
        return self.total + count <= self._settings.max_bytes

    def used_pct(self) -> float:
        """Fill level as a percentage of the size limit, capped at 100.

        Args:
            None

        Returns:
            float: 0.0 to 100.0.

        Raises:
            None
        """
        return min(100.0, 100.0 * self.total / self._settings.max_bytes)

    def state(self) -> CapacityState:
        """Current capacity state.

        Args:
            None

        Returns:
            CapacityState: From the thresholds.

        Raises:
            None
        """
        return capacity_state(self.used_pct(), self._settings)
