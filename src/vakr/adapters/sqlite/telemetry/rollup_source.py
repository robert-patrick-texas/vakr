"""``RollupSource`` for the SQLite telemetry store: one bucket's native evidence (RET-004, RET-009).

Each page call opens one read transaction, takes the next ``page.limit``
subjects of a dictionary in UUID order, and reads their rows in the window
with ``rollup_scan.page_rows``: one primary-key range per series and day
partition, plus the staging arm. Interface rows come back as the six metric
columns and the quality flags, not as whole observations, which is what
keeps a bucket of 1,600 interfaces cheap (ADR-021 amendment 5). ICMP rows
are decoded in full, because the job needs every probe; polls and SNMP
rows give only the schedule and the restart flag.

A page covers exactly one slice of the dictionary, so it may hold fewer
items than ``limit``, even none, when some subjects have no data; the
cursor is the last subject's UUID text, the reference adapter's form.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Scans, codecs, keys, vocabulary, and the store.
    vakr.ports.telemetry.rollup_source: The read models.

Safety:
    Read-only; every call is bounded by one window and one page of subjects.

Example:
    >>> SqliteRollupSource.__name__
    'SqliteRollupSource'
"""

import sqlite3
from collections import defaultdict
from datetime import datetime
from functools import partial
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time
from vakr.adapters.sqlite.telemetry.codec import decode
from vakr.adapters.sqlite.telemetry.codec_rows import stored_int
from vakr.adapters.sqlite.telemetry.keys import find_uuid
from vakr.adapters.sqlite.telemetry.layout import all_columns
from vakr.adapters.sqlite.telemetry.rollup_rows import (
    IFACE_COLUMNS,
    Key,
    group_interfaces,
    row_key,
)
from vakr.adapters.sqlite.telemetry.rollup_scan import candidates, native_edge, page_rows
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.vocab import code_of
from vakr.domain.enums import UptimeContinuity
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.observations import IcmpObservation
from vakr.domain.paging import Page, PageRequest, decode_cursor, encode_cursor
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import from_epoch_us, to_epoch_us
from vakr.ports.telemetry.rollup_source import (
    SCHEDULE_LOOKBACK,
    DeviceEvidence,
    InterfaceSamples,
    PollSlot,
)

_POLL = ("series_key", "collector_key", "ts_us", "observation_uuid", "effective_interval_s")
_KEYS = ("series_key", "collector_key")
_RESET = f" AND t.uptime_continuity = {code_of(UptimeContinuity.RESET_DETECTED)}"


def _after(page: PageRequest) -> bytes:
    """Decode a page cursor into the exclusive UUID bound it carries.

    Args:
        page: The request.

    Returns:
        bytes: The UUID blob, or ``b""`` for the first page.

    Raises:
        ValidationError: If the cursor is malformed.
    """
    if page.cursor is None:
        return b""
    (text,) = decode_cursor(page.cursor, 1)
    try:
        return UUID(str(text)).bytes
    except ValueError:
        raise ValidationError("cursor is not a valid page cursor", field="cursor") from None


def _cursor(chosen: list[tuple[int, bytes]], limit: int) -> str | None:
    """Return the next page's cursor, or ``None`` after the dictionary's last subject.

    Args:
        chosen: This page's subjects.
        limit: The page limit.

    Returns:
        str | None: The cursor.

    Raises:
        None
    """
    return encode_cursor((str(blob_to_uuid(chosen[-1][1])),)) if len(chosen) == limit else None


class SqliteRollupSource:
    """Native reads for the rollup job over one ``SqliteTelemetryStore``."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the source.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def first_native_time(self, window: TimeWindow) -> datetime | None:
        """Return the earliest interface or ICMP observation time in the window.

        Args:
            window: Bounded window.

        Returns:
            datetime | None: The time, or ``None``.

        Raises:
            StorageError: If the database is unavailable.
        """
        with self._store.reading() as conn:
            found = native_edge(conn, to_epoch_us(window.start), to_epoch_us(window.end),
                                newest=False)  # fmt: skip
        return None if found is None else from_epoch_us(found)

    def device_evidence(self, window: TimeWindow, page: PageRequest) -> Page[DeviceEvidence]:
        """Return a page of devices' ICMP cycles, schedules, and restart evidence.

        Args:
            window: One bucket.
            page: Limit (devices) and cursor.

        Returns:
            Page[DeviceEvidence]: The page.

        Raises:
            ValidationError: If the cursor is malformed.
            StorageError: If the database is unavailable or a row is damaged.
        """
        after = _after(page)
        span = (to_epoch_us(window.start), to_epoch_us(window.end))
        lookback = (span[0] - int(SCHEDULE_LOOKBACK.total_seconds()) * 1_000_000, span[1])
        with self._store.reading() as conn:
            chosen = candidates(conn, "device", after, page.limit)
            if not chosen:
                return Page((), None, None)
            subjects = (after, chosen[-1][1])
            icmp = page_rows(conn, "obs_icmp", all_columns("obs_icmp"), span, subjects)
            polls = page_rows(conn, "obs_poll", _POLL, lookback, subjects)
            resets = page_rows(conn, "obs_snmp", _KEYS, span, subjects, _RESET)
            items = self._devices(conn, icmp, polls, {row_key(r) for r in resets})
        return Page(tuple(items), _cursor(chosen, page.limit), None)

    def _devices(
        self, conn: sqlite3.Connection, icmp: list[tuple[object, ...]],
        polls: list[tuple[object, ...]], resets: set[Key],
    ) -> list[DeviceEvidence]:  # fmt: skip
        """Group one page's device rows into evidence items, in UUID order.

        Args:
            conn: The read connection.
            icmp: Full ICMP rows.
            polls: Poll rows (``_POLL`` columns).
            resets: Keys with an SNMP restart.

        Returns:
            list[DeviceEvidence]: One item per device and collector.

        Raises:
            StorageError: If a row is damaged.
        """
        uuid_of = partial(find_uuid, conn, self._store.keys)
        cycles: dict[Key, list[IcmpObservation]] = defaultdict(list)
        for row in icmp:
            o = decode("obs_icmp", row, uuid_of)
            if isinstance(o, IcmpObservation):
                cycles[row_key(row)].append(o)
        latest: dict[Key, tuple[int, UUID, int]] = {}
        for row in polls:
            mark = (stored_int(row[2]), blob_to_uuid(row[3]), stored_int(row[4]))
            if row_key(row) not in latest or mark[:2] > latest[row_key(row)][:2]:
                latest[row_key(row)] = mark
        items = []
        for key in {*cycles, *latest, *resets}:
            mark = latest.get(key)
            slot = None if mark is None else PollSlot(decode_time(mark[0]), mark[2])
            ordered = sorted(cycles.get(key, ()), key=lambda c: (c.timestamp, c.observation_id))
            items.append(DeviceEvidence(uuid_of("device", key[0]),
                                        CollectorId(uuid_of("collector", key[1])),
                                        tuple(ordered), slot, key in resets))  # fmt: skip
        return sorted(items, key=lambda d: (str(d.device_id), str(d.collector_id)))

    def interface_samples(self, window: TimeWindow, page: PageRequest) -> Page[InterfaceSamples]:
        """Return a page of interfaces' samples in the window.

        Args:
            window: One bucket.
            page: Limit (interfaces) and cursor.

        Returns:
            Page[InterfaceSamples]: The page.

        Raises:
            ValidationError: If the cursor is malformed.
            StorageError: If the database is unavailable or a row is damaged.
        """
        after = _after(page)
        span = (to_epoch_us(window.start), to_epoch_us(window.end))
        with self._store.reading() as conn:
            chosen = candidates(conn, "interface", after, page.limit)
            if not chosen:
                return Page((), None, None)
            rows = page_rows(conn, "obs_iface", IFACE_COLUMNS, span, (after, chosen[-1][1]))
            items = group_interfaces(partial(find_uuid, conn, self._store.keys), rows)
        return Page(tuple(items), _cursor(chosen, page.limit), None)
