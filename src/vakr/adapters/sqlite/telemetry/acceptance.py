"""``AcceptanceQuery`` for the SQLite telemetry store: lean poll, sample, and bucket reads (E.35).

Each read is one subject's primary-key range: ``(series_key,
collector_key, ts_us)`` in the day partitions plus staging for native
rows, ``(series_key, collector_key, bucket_offset_s)`` in the month
partitions for rollups. Native reads select only the columns a mark needs
and compute metric presence in SQL, so no observation is rebuilt. Bucket
reads step through each payload's counts and skip its values
(``rollup_summary``), which is the costly part of a rollup read.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.*: Layout, catalog, keys, codes, payload reader.
    vakr.ports.telemetry.acceptance: The port's marks and rules.

Safety:
    Read-only, inside one read transaction per call. Table names come from
    the catalog via ``layout.checked``; every value is a bound parameter.

Example:
    >>> SqliteAcceptanceQuery.__name__
    'SqliteAcceptanceQuery'
"""

# Arm SQL interpolates checked partition names and fixed column names only.

from typing import Any
from uuid import UUID

from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.layout import FAMILY_OF, METRIC_COLUMN, ROLLUP_FAMILY_OF
from vakr.adapters.sqlite.telemetry.partitions import ROLLUP_HORIZON, spans, state
from vakr.adapters.sqlite.telemetry.reads import native_start, sources, subject_key, union
from vakr.adapters.sqlite.telemetry.rollup_reads import rollup_dictionary
from vakr.adapters.sqlite.telemetry.rollup_summary import bucket_rows, payload_summary
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.vocab import code_of
from vakr.domain.enums import ObservationType, PollExecution
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.acceptance import (
    MAX_MARKS,
    BucketMark,
    Marks,
    PollMark,
    SampleMark,
    check_request,
)
from vakr.ports.telemetry.rollup_source import ICMP_METRICS, INTERFACE_METRICS

_US = 1_000_000
_EXECUTION = {code_of(m): m for m in PollExecution}
_POLL_COLUMNS = ("ts_us, observation_uuid, effective_interval_s, execution, quality_flags, "
                 "started_offset_us, completed_offset_us")  # fmt: skip
_METRICS = {ObservationType.INTERFACE: INTERFACE_METRICS, ObservationType.ICMP: ICMP_METRICS}


def _presence(observation_type: ObservationType) -> str:
    """Return the SQL expression of a row's metric-presence bits.

    Args:
        observation_type: ``INTERFACE`` or ``ICMP``.

    Returns:
        str: Bit ``i`` is 1 when metric ``i`` is not ``NULL``.

    Raises:
        None
    """
    metrics = _METRICS[observation_type]
    return " | ".join(f"(({METRIC_COLUMN[m]} IS NOT NULL) << {i})" for i, m in enumerate(metrics))


class SqliteAcceptanceQuery:
    """``AcceptanceQuery`` over one ``SqliteTelemetryStore``."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def poll_marks(
        self, device_id: DeviceId, collector_id: CollectorId, window: TimeWindow, *,
        limit: int = MAX_MARKS,
    ) -> Marks[PollMark]:  # fmt: skip
        """Return one device's poll records as marks; see ``AcceptanceQuery.poll_marks``.

        Args:
            device_id: The device.
            collector_id: The collector.
            window: Window on the scheduled time.
            limit: At most this many marks.

        Returns:
            Marks[PollMark]: Oldest first.

        Raises:
            ValidationError: If ``limit`` is out of range.
        """
        check_request(None, limit)
        rows = self._native("obs_poll", _POLL_COLUMNS, device_id, collector_id, window, limit,
                            descending=False)  # fmt: skip
        marks = [
            PollMark(ts, interval, _EXECUTION[code], Quality(flags),
                     None if start is None else ts + start, None if end is None else ts + end)
            for ts, _uuid, interval, code, flags, start, end in rows[:limit]
        ]  # fmt: skip
        return Marks(tuple(marks), len(rows) > limit)

    def sample_marks(
        self, observation_type: ObservationType, subject_id: UUID, collector_id: CollectorId,
        window: TimeWindow, *, limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[SampleMark]:  # fmt: skip
        """Return one subject's native observations as marks; see the port.

        Args:
            observation_type: ``INTERFACE`` or ``ICMP``.
            subject_id: The interface or device.
            collector_id: The collector.
            window: Window on the observation time.
            limit: At most this many marks.
            newest_first: Newest first.

        Returns:
            Marks[SampleMark]: The marks.

        Raises:
            ValidationError: If the type or ``limit`` is invalid.
        """
        check_request(observation_type, limit)
        columns = f"ts_us, observation_uuid, quality_flags, {_presence(observation_type)} AS bits"
        family = FAMILY_OF[observation_type]
        rows = self._native(family, columns, subject_id, collector_id, window, limit,
                            descending=newest_first)  # fmt: skip
        marks = [SampleMark(ts, Quality(flags), bits) for ts, _u, flags, bits in rows[:limit]]
        return Marks(tuple(marks), len(rows) > limit)

    def bucket_marks(
        self, observation_type: ObservationType, subject_id: UUID, collector_id: CollectorId,
        window: TimeWindow, *, limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[BucketMark]:  # fmt: skip
        """Return one subject's 15-minute rollup buckets as marks; see the port.

        Args:
            observation_type: ``INTERFACE`` or ``ICMP``.
            subject_id: The interface or device.
            collector_id: The collector.
            window: Window on the bucket start.
            limit: At most this many marks.
            newest_first: Newest first.

        Returns:
            Marks[BucketMark]: The marks.

        Raises:
            ValidationError: If the type or ``limit`` is invalid.
            StorageError: If a stored payload is damaged.
        """
        check_request(observation_type, limit)
        family = ROLLUP_FAMILY_OF[observation_type]
        found: list[BucketMark] = []
        with self._store.reading() as conn:
            s = find_key(conn, self._store.keys, rollup_dictionary(family), subject_id)
            c = find_key(conn, self._store.keys, "collector", collector_id)
            if s is None or c is None:
                return Marks((), False)
            expired = state(conn, ROLLUP_HORIZON) or -(1 << 62)
            bounds = (to_epoch_us(window.start), to_epoch_us(window.end))
            parts = spans(conn, family, *bounds)
            for name, month_us in reversed(parts) if newest_first else parts:
                rows = bucket_rows(conn, name, month_us, (s, c), bounds, newest_first)
                for offset, payload in rows:
                    start = month_us + offset * _US
                    if start + ROLLUP_BUCKET_SECONDS * _US <= expired:
                        continue  # ends at or before the rollup horizon: expired
                    if len(found) > limit:
                        break
                    found.append(BucketMark(start, *_summary(payload)))
        return Marks(tuple(found[:limit]), len(found) > limit)

    def _native(
        self, family: str, columns: str, subject: UUID, collector: UUID, window: TimeWindow,
        limit: int, *, descending: bool,
    ) -> list[tuple[Any, ...]]:  # fmt: skip
        """Fetch up to ``limit + 1`` rows of one subject and collector from a native family.

        Args:
            family: The native family.
            columns: Select list, starting with ``ts_us, observation_uuid``.
            subject: Interface or device.
            collector: The collector.
            window: The window.
            limit: Marks wanted; one more row is fetched to detect truncation.
            descending: Newest first.

        Returns:
            list[tuple[Any, ...]]: The rows, in the requested order (SQLite values).

        Raises:
            sqlite3.Error: If a query fails.
        """
        with self._store.reading() as conn:
            key = subject_key(conn, self._store.keys, family, subject)
            col = find_key(conn, self._store.keys, "collector", collector)
            if key is None or col is None:
                return []
            start = native_start(conn, to_epoch_us(window.start))
            end = to_epoch_us(window.end)
            where = "series_key = :s AND collector_key = :c AND ts_us >= :a AND ts_us < :b"
            order = "DESC" if descending else "ASC"
            sql = union(columns, sources(conn, family, start, end), where)
            sql += f" ORDER BY ts_us {order}, observation_uuid {order} LIMIT :n"
            params = {"s": key, "c": col, "a": start, "b": end, "n": limit + 1}
            return conn.execute(sql, params).fetchall()


def _summary(payload: object) -> tuple[int, int, Quality]:
    """Return a payload's sample count, presence bits, and quality as ``BucketMark`` fields.

    Args:
        payload: The stored blob.

    Returns:
        tuple[int, int, Quality]: Samples, presence, quality.

    Raises:
        StorageError: If the payload is damaged.
    """
    samples, available, quality = payload_summary(payload)
    return samples, available, Quality(quality)
