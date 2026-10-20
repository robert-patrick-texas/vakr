"""Candidate SQLite layouts for Compact telemetry: DDL, partition names, and PRAGMAs.

Three layouts are compared (ADR-021):

* ``A``: one rowid table per observation type plus a unique secondary index
  on the natural key ``(series_key, collector_key, ts_us)``.
* ``B``: one ``WITHOUT ROWID`` table per type, clustered on that natural key,
  so one series' rows sit together on disk.
* ``C``: ``B`` split into one table set per UTC day (native) and per UTC
  month (rollups). A partition catalog lists them; retention drops tables.

Interfaces, devices and collectors map to small integer surrogate keys
through dictionary tables, so telemetry rows never repeat 16-byte UUIDs or
mutable names (STORE-008). Column lists follow blueprint 10.1-10.3 and 10.5.

Dependencies:
    sqlite3: Standard-library driver; DDL uses ``STRICT`` and
        ``WITHOUT ROWID`` (SQLite 3.37 or later).
    sqlc_columns: Column definitions shared with the generators.

Safety:
    Table names are built only from fixed prefixes and ``datetime``-formatted
    dates, never from user input, so the f-string SQL here cannot be
    injected (hence the ``S608`` suppressions).

Example:
    >>> Layout("C").native_table("iface", 1_788_220_800_000_000)
    'obs_iface_d20260901'
    >>> Layout("B").native_table("iface", 0)
    'obs_iface'
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from tests.performance.sqlite_compact.sqlc_columns import COLUMNS, KEYS, NATIVE_KINDS, ROLLUP_KINDS

CATALOG_DDL = """CREATE TABLE IF NOT EXISTS partition_catalog (
  table_name TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  start_us INTEGER NOT NULL,
  end_us INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'open'
) WITHOUT ROWID, STRICT"""


def month_start_us(ts_us: int) -> tuple[int, int]:
    """Return the UTC month containing a timestamp as ``[start, end)`` microseconds.

    Args:
        ts_us (int): Unix microseconds.

    Returns:
        tuple[int, int]: Month start and next month start.

    Raises:
        None
    """
    d = datetime.fromtimestamp(ts_us / 1e6, UTC)
    start = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + start.month // 12, month=start.month % 12 + 1)
    return int(start.timestamp()) * 1_000_000, int(nxt.timestamp()) * 1_000_000


@dataclass(frozen=True, slots=True)
class Layout:
    """One candidate layout and its options. Immutable.

    Attributes:
        candidate (str): ``"A"``, ``"B"``, or ``"C"``.
        uuid_index (bool): Add a unique index on ``observation_uuid``.
        hot (bool): Stage recent native rows in time-ordered ``obs_<kind>_hot``
            tables that a mover drains into the clustered tables (see
            ``sqlc_writer.Writer.move``).
    """

    candidate: str
    uuid_index: bool = False
    hot: bool = False

    @property
    def partitioned(self) -> bool:
        """Whether native and rollup data live in per-day and per-month tables.

        Args:
            None

        Returns:
            bool: True for candidate C.

        Raises:
            None
        """
        return self.candidate == "C"

    def native_table(self, kind: str, ts_us: int) -> str:
        """Name the table holding a native row of ``kind`` at ``ts_us``.

        Args:
            kind (str): ``"iface"``, ``"icmp"``, or ``"snmp"``.
            ts_us (int): Row timestamp in Unix microseconds.

        Returns:
            str: ``obs_<kind>`` or, partitioned, ``obs_<kind>_dYYYYMMDD``.

        Raises:
            None
        """
        if not self.partitioned:
            return f"obs_{kind}"
        return f"obs_{kind}_d{datetime.fromtimestamp(ts_us / 1e6, UTC):%Y%m%d}"

    @staticmethod
    def hot_table(kind: str) -> str:
        """Name the staging table for recent native rows of ``kind``.

        Args:
            kind (str): Native kind.

        Returns:
            str: ``obs_<kind>_hot``.

        Raises:
            None
        """
        return f"obs_{kind}_hot"

    @staticmethod
    def hot_ddl(kind: str) -> str:
        """Return the DDL of a staging table, clustered on time first.

        Leading with ``ts_us`` makes every insert land at the right edge of
        the B-tree, so a commit dirties a few pages instead of one page per
        series, while the full natural key still rejects replayed rows.

        Args:
            kind (str): Native kind.

        Returns:
            str: ``CREATE TABLE`` statement, safe to re-run.

        Raises:
            None
        """
        cols = ",\n  ".join(f"{name} {decl}" for name, decl in COLUMNS[kind])
        series, coll, ts = KEYS[kind]
        return (
            f"CREATE TABLE IF NOT EXISTS {Layout.hot_table(kind)} (\n  {cols},\n"
            f"  PRIMARY KEY ({ts}, {series}, {coll})\n) WITHOUT ROWID, STRICT"
        )

    def rollup_table(self, kind: str, ts_us: int) -> str:
        """Name the table holding a rollup row of ``kind`` whose bucket starts at ``ts_us``.

        Args:
            kind (str): ``"iface_15m"`` or ``"icmp_15m"``.
            ts_us (int): Bucket start in Unix microseconds.

        Returns:
            str: ``roll_<kind>`` or, partitioned, ``roll_<kind>_mYYYYMM``.

        Raises:
            None
        """
        if not self.partitioned:
            return f"roll_{kind}"
        return f"roll_{kind}_m{datetime.fromtimestamp(ts_us / 1e6, UTC):%Y%m}"

    def ddl(self, kind: str, table: str) -> list[str]:
        """Return the statements that create one table and its indexes.

        Args:
            kind (str): Native or rollup kind (a key of ``COLUMNS``).
            table (str): Table name from ``native_table`` or ``rollup_table``.

        Returns:
            list[str]: ``CREATE`` statements, safe to re-run.

        Raises:
            None
        """
        cols = ",\n  ".join(f"{name} {decl}" for name, decl in COLUMNS[kind])
        key = ", ".join(KEYS[kind])
        if self.candidate == "A":
            out = [
                f"CREATE TABLE IF NOT EXISTS {table} (\n  {cols}\n) STRICT",
                f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_key ON {table} ({key})",
            ]
        else:
            out = [
                f"CREATE TABLE IF NOT EXISTS {table} (\n  {cols},\n  PRIMARY KEY ({key})\n)"
                " WITHOUT ROWID, STRICT"
            ]
        if self.uuid_index and kind in NATIVE_KINDS:
            out.append(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_uuid ON {table} (observation_uuid)"
            )
        return out

    def ensure(self, conn: sqlite3.Connection, kind: str, ts_us: int) -> str:
        """Create the table for ``kind`` at ``ts_us`` if needed and record it.

        Args:
            conn (sqlite3.Connection): Writer connection, inside a transaction.
            kind (str): Native or rollup kind.
            ts_us (int): Timestamp the table must cover.

        Returns:
            str: The table name.

        Raises:
            sqlite3.Error: If the DDL fails.
        """
        if kind in ROLLUP_KINDS:
            table = self.rollup_table(kind, ts_us)
            start, end = month_start_us(ts_us) if self.partitioned else (0, 2**62)
        else:
            table = self.native_table(kind, ts_us)
            day = 86_400_000_000
            start, end = (
                ((ts_us // day) * day, (ts_us // day + 1) * day) if self.partitioned else (0, 2**62)
            )
        for stmt in self.ddl(kind, table):
            conn.execute(stmt)
        conn.execute(
            "INSERT OR IGNORE INTO partition_catalog (table_name, kind, start_us, end_us)"
            " VALUES (?, ?, ?, ?)",
            (table, kind, start, end),
        )
        return table


def tables_for(conn: sqlite3.Connection, kind: str, t0_us: int, t1_us: int) -> list[str]:
    """List the catalogued tables of ``kind`` overlapping ``[t0_us, t1_us)``, oldest first.

    Args:
        conn (sqlite3.Connection): Any connection to the telemetry database.
        kind (str): Native or rollup kind.
        t0_us (int): Window start, inclusive.
        t1_us (int): Window end, exclusive.

    Returns:
        list[str]: Table names in time order.

    Raises:
        sqlite3.Error: If the catalog cannot be read.
    """
    rows = conn.execute(
        "SELECT table_name FROM partition_catalog WHERE kind = ? AND start_us < ? AND end_us > ?"
        " ORDER BY start_us",
        (kind, t1_us, t0_us),
    )
    return [r[0] for r in rows]


@dataclass(frozen=True, slots=True)
class Pragmas:
    """Connection settings under test. Immutable.

    Attributes:
        synchronous (str): ``"NORMAL"`` or ``"FULL"``.
        page_size (int): Bytes per page; takes effect only on a new file.
        auto_vacuum (str): ``"NONE"`` or ``"INCREMENTAL"``; new files only.
        cache_mib (int): Page cache per connection, MiB.
        journal_size_limit (int): Bytes the WAL is truncated to after a checkpoint.
        wal_autocheckpoint (int): Pages of WAL that trigger an automatic checkpoint.
        mmap_mib (int): Memory-mapped I/O size, MiB; 0 disables it.
    """

    synchronous: str = "NORMAL"
    page_size: int = 4096
    auto_vacuum: str = "NONE"
    cache_mib: int = 64
    journal_size_limit: int = 64 * 1024 * 1024
    wal_autocheckpoint: int = 1000
    mmap_mib: int = 0


def open_db(path: str, pragmas: Pragmas, readonly: bool = False) -> sqlite3.Connection:
    """Open a telemetry database with WAL and the given PRAGMAs (blueprint 5.3).

    The connection is in autocommit mode; writers use explicit
    ``BEGIN IMMEDIATE`` ... ``COMMIT`` so a batch is one transaction.

    Args:
        path (str): Database file path.
        pragmas (Pragmas): Settings to apply.
        readonly (bool): Open with ``mode=ro`` for reader processes.

    Returns:
        sqlite3.Connection: The open connection.

    Raises:
        sqlite3.Error: If the file cannot be opened or a PRAGMA fails.
    """
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, cached_statements=512)
    if not readonly:
        conn.execute(f"PRAGMA page_size = {pragmas.page_size}")
        conn.execute(f"PRAGMA auto_vacuum = {pragmas.auto_vacuum}")
        conn.execute("PRAGMA journal_mode = WAL")
    for stmt in (
        f"PRAGMA synchronous = {pragmas.synchronous}",
        f"PRAGMA journal_size_limit = {pragmas.journal_size_limit}",
        f"PRAGMA wal_autocheckpoint = {pragmas.wal_autocheckpoint}",
        f"PRAGMA cache_size = -{pragmas.cache_mib * 1024}",
        f"PRAGMA mmap_size = {pragmas.mmap_mib * 1024 * 1024}",
        "PRAGMA temp_store = MEMORY",
        "PRAGMA foreign_keys = ON",
        "PRAGMA busy_timeout = 5000",
    ):
        conn.execute(stmt)
    return conn
