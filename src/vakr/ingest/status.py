"""Spool and ingestion health: one snapshot for metrics and health checks (OBS-001, OBS-007).

Operators need to see, before anything is lost, that the telemetry database
has stopped accepting writes and how long the spool can absorb it: bytes and
fill level, how old the oldest unacknowledged record is, ingest lag, queue
saturation, and the last storage error. ``SpoolHealth`` carries exactly that.
The collector copies the fields ``CollectorHealthObservation`` has
(``collector_health_fields``); the ingester also writes the snapshot to
``status.json`` in the spool directory every cycle, so ``vakr check health``,
a separate process, can read it.

Storage trouble here is a collector condition, never a target state
(invariant 2): ``collector_state`` maps it to DEGRADED, nothing more.

Dependencies:
    vakr.ingest: Capacity states and the advisory status file.
    vakr.domain.enums: ``CollectorState``.

Safety:
    Error text comes from ``StorageError`` messages, which by the port
    contract never contain SQL, values, or credentials, or else is only an
    exception type name.

Example:
    >>> h = SpoolHealth.empty("c1", 0, 4096)
    >>> h.collector_health_fields()["spool_used_pct"], collector_state(h).value
    (0.0, 'healthy')
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vakr.domain.enums import CollectorState
from vakr.ingest.capacity import CapacityState
from vakr.ingest.fileops import FileOps
from vakr.ingest.ingest_steps import IngestStatus
from vakr.ingest.spool import Spool
from vakr.ingest.state import STATUS_FILE, read_json, write_json_atomic


@dataclass(frozen=True, slots=True)
class SpoolHealth:
    """Point-in-time spool and ingestion health. Immutable.

    Unknown values are ``None``, never zero (invariant 1).

    Attributes:
        collector_id: The collector, as text.
        taken_us: When the snapshot was taken (epoch microseconds, UTC).
        spool_bytes: Bytes on disk, quarantine included.
        max_bytes: The size limit.
        used_pct: Fill level, 0 to 100.
        state: Capacity state (6.4).
        admits_new_work: Whether normal polling may continue.
        queue_depth: Observations in memory awaiting fsync.
        queue_capacity: Bound of that queue.
        producers_waiting: Appends waiting for queue room (saturation).
        durable_seq: Highest fsynced sequence number.
        acked_seq: Highest acknowledged sequence number.
        backlog_records: Durable records not yet acknowledged.
        oldest_unacked_age_s: Age of the oldest unacknowledged record, or
            ``None`` when nothing is waiting.
        ingest_lag_s: Append-to-acknowledge time of the newest stored record.
        storage_available: Last write outcome; ``None`` before any attempt.
        consecutive_failures: Failed write attempts since the last success.
        last_error: Last storage error text, or ``None``.
        last_error_us: When it happened, or ``None``.
        incidents: Loss incidents recorded since the spool opened.
        unsaved_incidents: Incidents that could not be written to disk.
    """

    collector_id: str
    taken_us: int
    spool_bytes: int
    max_bytes: int
    used_pct: float
    state: CapacityState
    admits_new_work: bool
    queue_depth: int
    queue_capacity: int
    producers_waiting: int
    durable_seq: int
    acked_seq: int
    backlog_records: int
    oldest_unacked_age_s: float | None
    ingest_lag_s: float | None
    storage_available: bool | None
    consecutive_failures: int
    last_error: str | None
    last_error_us: int | None
    incidents: int
    unsaved_incidents: int

    @classmethod
    def empty(cls, collector_id: str, taken_us: int, max_bytes: int) -> SpoolHealth:
        """Return the snapshot of a spool that holds nothing.

        Args:
            collector_id: The collector, as text.
            taken_us: Snapshot time.
            max_bytes: The size limit.

        Returns:
            SpoolHealth: Zero bytes, NORMAL, nothing waiting, no errors.

        Raises:
            None
        """
        return cls(collector_id, taken_us, 0, max_bytes, 0.0, CapacityState.NORMAL, True, 0, 1,
                   0, 0, 0, 0, None, None, None, 0, None, None, 0, 0)  # fmt: skip

    def collector_health_fields(self) -> dict[str, Any]:
        """Return the fields ``CollectorHealthObservation`` carries (10.4 subset).

        Args:
            None

        Returns:
            dict[str, Any]: ``spool_bytes``, ``spool_used_pct``, ``queue_depth``,
            and ``queue_capacity``, ready as keyword arguments.

        Raises:
            None
        """
        return {
            "spool_bytes": self.spool_bytes, "spool_used_pct": float(self.used_pct),
            "queue_depth": min(self.queue_depth, self.queue_capacity),
            "queue_capacity": self.queue_capacity,
        }  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        """Encode for ``status.json``.

        Args:
            None

        Returns:
            dict[str, Any]: Every field; ``state`` as its value.

        Raises:
            None
        """
        return dataclasses.asdict(self) | {"state": self.state.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpoolHealth:
        """Decode ``to_dict`` output.

        Args:
            data: The dict.

        Returns:
            SpoolHealth: The snapshot.

        Raises:
            KeyError, TypeError, ValueError: If the dict is malformed.
        """
        known = {f.name for f in dataclasses.fields(cls)}
        fields: dict[str, Any] = {k: v for k, v in data.items() if k in known}
        fields["state"] = CapacityState(data["state"])
        return cls(**fields)


def spool_health(spool: Spool, status: IngestStatus, now_us: int) -> SpoolHealth:
    """Take a health snapshot of a running spool and its ingester.

    Args:
        spool: The spool.
        status: The ingester's counters.
        now_us: Snapshot time.

    Returns:
        SpoolHealth: The snapshot.

    Raises:
        None
    """
    oldest = status.oldest_unacked_us
    age = None if oldest is None else max(0, now_us - oldest) / 1e6
    log, durable = spool.incidents, spool.durable_seq
    return SpoolHealth(
        str(spool.collector_id), now_us, spool.usage.total, spool.settings.max_bytes,
        round(spool.usage.used_pct(), 2), spool.usage.state(), spool.admits_new_work(),
        spool.gate.depth, spool.gate.capacity, spool.gate.waiting, durable, status.acked_seq,
        max(0, durable - status.acked_seq), age, status.lag_s, status.storage_available,
        status.consecutive_failures, status.last_error, status.last_error_us,
        0 if log is None else log.recorded, 0 if log is None else len(log.unsaved),
    )  # fmt: skip


def collector_state(health: SpoolHealth) -> CollectorState:
    """Map spool health onto the collector's own state, never a target's (OBS-003).

    Args:
        health: The snapshot.

    Returns:
        CollectorState: DEGRADED at CRITICAL or EXHAUSTED capacity, or while
        the telemetry database refuses writes; otherwise HEALTHY.

    Raises:
        None
    """
    if health.state in {CapacityState.CRITICAL, CapacityState.EXHAUSTED}:
        return CollectorState.DEGRADED
    if health.storage_available is False:
        return CollectorState.DEGRADED
    return CollectorState.HEALTHY


def write_status(directory: Path, health: SpoolHealth, ops: FileOps) -> None:
    """Replace ``status.json`` with the snapshot (advisory, so not fsynced).

    Args:
        directory: The collector's spool directory.
        health: The snapshot.
        ops: File operations.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written.
    """
    write_json_atomic(directory / STATUS_FILE, health.to_dict(), ops, durable=False)


def read_status(directory: Path) -> SpoolHealth | None:
    """Read ``status.json``.

    Args:
        directory: The collector's spool directory.

    Returns:
        SpoolHealth | None: The last snapshot, or ``None`` if absent or unreadable.

    Raises:
        None
    """
    data = read_json(directory / STATUS_FILE)
    if data is None:
        return None
    try:
        return SpoolHealth.from_dict(data)
    except KeyError, TypeError, ValueError:
        return None
