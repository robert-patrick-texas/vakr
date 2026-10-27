"""``ObservationWriter`` for the SQLite telemetry store (STORE-003, STORE-009, STORE-010).

One ``write_batch`` is one ``BEGIN IMMEDIATE`` transaction through the
store's writer gate, and it returns only after ``COMMIT``, which with
``synchronous=FULL`` means the batch is durable: the spool may then
release it (STORE-010, E.21). Everything that can be checked without the
database (the batch size, the types, the encoding) is checked first, so an
invalid batch writes nothing.

Each row goes to its family's staging table when its timestamp is at or
after the staging boundary, and straight to its day partition otherwise:
that is how a peer's backfill or a late replay lands (ADR-021). ``INSERT OR
IGNORE`` on ``(series_key, collector_key, ts_us, observation_uuid)`` makes
a replayed observation a no-op wherever its first copy is now, because
only the mover changes the boundary and it moves rows in the same
transaction (STORE-009). Duplicates inside one batch count once. A batch
that stores interface samples also unlists, in the same transaction, the
ranking-projection buckets they fall in, so a projected bucket always
matches its samples (E.28).

``write_batch`` (the local spool's path) also logs each new row for partners to
pull; ``write_replicated`` stores a partner's page unlogged with its cursor (MPOS-004).

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Codecs, keys, catalog, and the store.
    vakr.domain, vakr.ports.telemetry: Observations and the result type.

Safety:
    Table names come from ``layout``; values are bound parameters.

Example:
    >>> SqliteObservationWriter.__name__
    'SqliteObservationWriter'
"""

# INSERT statements interpolate checked partition names only.
# ruff: noqa: S608

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

from vakr.adapters.sqlite.telemetry.codec import encode
from vakr.adapters.sqlite.telemetry.codec_rows import EncodedRow
from vakr.adapters.sqlite.telemetry.keys import KeyAllocator
from vakr.adapters.sqlite.telemetry.layout import (
    KEPT_FAMILIES,
    all_columns,
    day_start,
    hot_table,
)
from vakr.adapters.sqlite.telemetry.partitions import BOUNDARY, ensure_day, state
from vakr.adapters.sqlite.telemetry.ranking_projection import forget_buckets
from vakr.adapters.sqlite.telemetry.rankings import FAMILY
from vakr.adapters.sqlite.telemetry.replication_log import (
    FAMILY_CODE,
    LogEntry,
    cursor_of,
    log_rows,
    set_cursor,
    store_part,
)
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.tuning import MAX_BATCH_ROWS
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, new_id
from vakr.domain.observations import Observation
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.results import WriteResult

type KeyedRow = tuple[object, ...]


def insert_sql(table: str, family: str) -> str:
    """Return the idempotent insert of one full native row into ``table``.

    Args:
        table: A checked partition or staging table name.
        family: Its native family.

    Returns:
        str: ``INSERT OR IGNORE`` with one placeholder per column.

    Raises:
        KeyError: If ``family`` is not native.
    """
    columns = all_columns(family)
    marks = ", ".join("?" * len(columns))
    return f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({marks})"


class SqliteObservationWriter:
    """Writes batches of observations into one ``SqliteTelemetryStore``."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the writer.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def write_batch(self, batch_id: BatchId, observations: Sequence[Observation]) -> WriteResult:
        """Store a batch atomically, skipping observations already stored.

        Args:
            batch_id: The batch UUID, recorded in the result.
            observations: Up to ``MAX_BATCH_ROWS`` observations of any types.

        Returns:
            WriteResult: Inserted and duplicate counts, after a durable commit.

        Raises:
            ValidationError: If the batch is too large, holds a non-observation,
                or a value cannot be stored; nothing is written.
            StorageError: If the database is unavailable; nothing is committed.
        """
        rows = _unique(observations)
        inserted = 0
        if rows:
            with self._store.writing() as conn:
                keys = KeyAllocator(conn, self._store.keys)
                log: list[LogEntry] = []
                inserted = _insert(conn, keys, rows, log)
                log.sort(key=lambda e: (e[3], e[4]))  # seq order follows observation time
                log_rows(conn, log)
            keys.publish()
        return WriteResult(batch_id, inserted, len(observations) - inserted)

    def write_replicated(
        self, source: CollectorId, observations: Sequence[Observation], through_seq: int
    ) -> WriteResult:
        """Store a partner's page and move its cursor in one transaction (MPOS-004).

        Args:
            source: The partner collector the page came from.
            observations: Its observations; none is logged here.
            through_seq: The page's ``next_after``.

        Returns:
            WriteResult: Inserted and duplicate counts.

        Raises:
            ValidationError: Another collector's observation, or the cursor moving back.
            StorageError: If the database is unavailable; nothing is committed.
        """
        if any(o.collector_id != source for o in observations):
            raise ValidationError("a replicated page holds another collector's observation")
        rows = _unique(observations)
        with self._store.writing() as conn:
            held = cursor_of(conn, source)
            if held is not None and through_seq < held:
                raise ValidationError(f"cursor would move back from {held} to {through_seq}")
            keys = KeyAllocator(conn, self._store.keys)
            inserted = _insert(conn, keys, rows, None) if rows else 0
            set_cursor(conn, source, through_seq, to_epoch_us(datetime.now(UTC)))
        keys.publish()
        return WriteResult(BatchId(new_id(BatchId)), inserted, len(observations) - inserted)

    def rewind(self, source: CollectorId) -> None:
        """Forget ``source``'s cursor, so the next pull starts from its oldest entry.

        For a partner whose log restarted (its newest seq is below the cursor
        held here); replaying what is already stored changes nothing.

        Args:
            source: The partner collector.

        Returns:
            None

        Raises:
            StorageError: If the database is unavailable.
        """
        with self._store.writing() as conn:
            set_cursor(conn, source, 0, to_epoch_us(datetime.now(UTC)))

    def cursor(self, source: CollectorId) -> int | None:
        """Return the newest partner seq stored for ``source``.

        Args:
            source: A partner collector.

        Returns:
            int | None: The seq, or ``None`` before the first page.

        Raises:
            StorageError: If the database is unavailable.
        """
        with self._store.reading() as conn:
            return cursor_of(conn, source)


def _unique(observations: Sequence[Observation]) -> list[EncodedRow]:
    """Check the batch size and encode each observation once.

    Args:
        observations: The batch.

    Returns:
        list[EncodedRow]: Encoded rows, the first copy of each observation UUID.

    Raises:
        ValidationError: If the batch is too large or holds a non-observation.
    """
    if len(observations) > MAX_BATCH_ROWS:
        raise ValidationError(
            f"batch of {len(observations)} exceeds max_batch_rows {MAX_BATCH_ROWS}"
        )
    rows, seen = [], set()
    for row in (encode(o) for o in observations):
        if row.observation not in seen:
            seen.add(row.observation)
            rows.append(row)
    return rows


def _keyed(row: EncodedRow, keys: KeyAllocator) -> KeyedRow:
    """Replace a row's UUID references with dictionary keys.

    Args:
        row: An encoded row.
        keys: The transaction's key allocator.

    Returns:
        KeyedRow: Every column value, keys first.

    Raises:
        sqlite3.Error: If a dictionary cannot be read or written.
    """
    collector = keys.key("collector", row.collector)
    if row.device is not None:
        device = keys.key("device", row.device)
        series = keys.interface(row.subject, device)
        values = (row.values[0], device, *row.values[2:])
    else:
        series = keys.key(row.subject_kind, row.subject)
        values = row.values
    return (series, collector, row.ts_us, row.observation, *values)


def _insert(
    conn: sqlite3.Connection, keys: KeyAllocator, rows: list[EncodedRow], log: list[LogEntry] | None
) -> int:
    """Route rows to staging or day partitions and insert them.

    Args:
        conn: Connection holding the write lock.
        keys: The transaction's key allocator.
        rows: Encoded rows, unique by observation UUID.
        log: Collects a replication-log entry per row inserted for the first
            time (the local spool's path); ``None`` logs nothing.

    Returns:
        int: Rows inserted; replays are ignored and not counted.

    Raises:
        StorageError: If the schema lacks a partition template.
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    boundary = state(conn, BOUNDARY) or 0
    groups: dict[tuple[str, str], list[KeyedRow]] = defaultdict(list)
    days: dict[tuple[str, int], str] = {}
    for row in rows:
        keyed = _keyed(row, keys)
        if row.ts_us >= boundary or row.family in KEPT_FAMILIES:
            groups[row.family, hot_table(row.family)].append(keyed)
            continue
        day = (row.family, day_start(row.ts_us))
        if day not in days:
            days[day] = ensure_day(conn, *day)
        groups[row.family, days[day]].append(keyed)
    inserted, samples = 0, 0
    for (family, table), part in groups.items():
        # Key order touches each B-tree page once: time first for staging,
        # series first for a day partition.
        hot = table.endswith("_hot")
        part.sort(key=(lambda r: (r[2], r[0], r[1], r[3])) if hot else (lambda r: r[:4]))
        # Kept families are not logged: repl_log admits families 1 to 5, so a status record
        # replicates attached to its poll's SNMP device observation instead (E.35).
        logged = log if family in FAMILY_CODE else None
        stored = store_part(conn, insert_sql(table, family), part, FAMILY_CODE.get(family, 0),
                            logged)  # fmt: skip
        samples += stored if family == FAMILY else 0
        inserted += stored
    if samples:
        # A projected bucket must keep matching its samples (E.28); a replay changes nothing.
        forget_buckets(conn, (row.ts_us for row in rows if row.family == FAMILY))
    return inserted
