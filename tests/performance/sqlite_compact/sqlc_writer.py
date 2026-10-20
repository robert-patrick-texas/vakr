# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
"""Batch writer for the candidate layouts: one transaction per flushed batch.

The ingest path (blueprint 6.1, STORE-004) hands the telemetry adapter a
batch of completed observations; the adapter commits it as one transaction
and only then acknowledges the spool (STORE-010). ``Writer.write`` does the
SQLite half of that: route rows to their table (one per UTC day under
layout C), ``INSERT OR IGNORE`` so a replayed batch adds nothing
(STORE-009), and optionally keep a latest-observation projection.

With ``Layout.hot`` the writer keeps one invariant: a row is in the
``obs_<kind>_hot`` staging table exactly when ``ts_us >= boundary``, and
in its clustered day table otherwise. Only ``move`` advances the boundary,
inside the same transaction that moves the rows, so readers never see a
row twice or not at all, and a replayed row always meets its earlier copy
in the same table, where the primary key ignores it.

Dependencies:
    sqlite3: Standard-library driver.
    sqlc_schema, sqlc_columns: Layout naming and DDL.
    sqlc_topology: Inventory loaded into the dictionary tables.
    vakr.domain.enums: Vocabularies recorded in ``vocab_code``.

Safety:
    A failed batch rolls back completely, so a partial batch is never
    acknowledged.

Example:
    >>> import sqlite3
    >>> from tests.performance.sqlite_compact.sqlc_schema import Layout
    >>> from tests.performance.sqlite_compact.sqlc_topology import build_topology
    >>> conn = sqlite3.connect(":memory:", isolation_level=None)
    >>> create_base(conn, build_topology(21), Layout("C"))
    >>> Writer(conn, Layout("C")).write({"iface": []})
    0
"""

import sqlite3
from bisect import bisect_left

from tests.performance.sqlite_compact.sqlc_columns import COLUMNS, DICTIONARY_DDL, NATIVE_KINDS
from tests.performance.sqlite_compact.sqlc_schema import CATALOG_DDL, Layout
from tests.performance.sqlite_compact.sqlc_topology import DAY_US, Topology, vocab_codes
from vakr.domain.enums import (
    AddressFamily,
    BaselineState,
    CounterFamily,
    IcmpState,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)

VOCABS = (
    AddressFamily,
    BaselineState,
    CounterFamily,
    IcmpState,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
_NAMES = [n for n, _ in COLUMNS["iface"]]
PROJECTION_DDL = (
    "CREATE TABLE IF NOT EXISTS cur_iface ("
    + ", ".join(f"{n} {d}" for n, d in COLUMNS["iface"])
    + ", PRIMARY KEY (interface_key, collector_key)) WITHOUT ROWID, STRICT"
)
# Only a newer observation may replace the projected one, so replaying an old
# batch or backfilling a peer's history never moves current state backwards.
PROJECTION_UPSERT = (
    f"INSERT INTO cur_iface VALUES ({', '.join('?' * len(_NAMES))})"
    f" ON CONFLICT (interface_key, collector_key) DO UPDATE SET ({', '.join(_NAMES[2:])})"
    f" = ({', '.join('excluded.' + n for n in _NAMES[2:])})"
    " WHERE excluded.ts_us > cur_iface.ts_us"
)
HOT_STATE_DDL = (
    "CREATE TABLE IF NOT EXISTS hot_state (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
    " WITHOUT ROWID, STRICT"
)


def _load(conn: sqlite3.Connection, topo: Topology) -> None:
    """Fill the dictionary, capacity and vocabulary tables (idempotent).

    Args:
        conn (sqlite3.Connection): Writer connection inside a transaction.
        topo (Topology): Inventory to load.

    Returns:
        None

    Raises:
        sqlite3.Error: If an insert fails.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO dict_collector VALUES (?, ?)", enumerate(topo.collectors, 1)
    )
    conn.executemany(
        "INSERT OR IGNORE INTO dict_device VALUES (?, ?)", ((d.key, d.uuid) for d in topo.devices)
    )
    conn.executemany(
        "INSERT OR IGNORE INTO dict_interface VALUES (?, ?, ?)",
        ((i.key, i.uuid, i.device_key) for i in topo.interfaces),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO bench_capacity VALUES (?, ?)",
        ((i.key, i.capacity_bps) for i in topo.interfaces),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO vocab_code VALUES (?, ?, ?)",
        ((e.__name__, m.value, c) for e in VOCABS for m, c in vocab_codes(e).items()),
    )


def create_base(
    conn: sqlite3.Connection, topo: Topology, layout: Layout, project: bool = False
) -> None:
    """Create the catalog, dictionaries, vocabulary codes and fixed tables.

    Args:
        conn (sqlite3.Connection): Writer connection in autocommit mode.
        topo (Topology): Inventory to load into the dictionary tables.
        layout (Layout): Candidate layout.
        project (bool): Also create the ``cur_iface`` projection table.

    Returns:
        None

    Raises:
        sqlite3.Error: If any statement fails; the transaction rolls back.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        for stmt in (CATALOG_DDL, *DICTIONARY_DDL, *((PROJECTION_DDL,) if project else ())):
            conn.execute(stmt)
        _load(conn, topo)
        if not layout.partitioned:
            for kind in COLUMNS:
                layout.ensure(conn, kind, 0)
        if layout.hot:
            conn.execute(HOT_STATE_DDL)
            conn.execute("INSERT OR IGNORE INTO hot_state VALUES ('boundary_us', 0)")
            for kind in NATIVE_KINDS:
                conn.execute(Layout.hot_ddl(kind))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _rounded(rows: list[tuple]) -> list[tuple]:
    """Round interface bit rates to whole bits per second.

    SQLite stores an integral ``REAL`` as an integer, so a rounded rate
    takes 3-6 bytes instead of 8.

    Args:
        rows (list[tuple]): Interface rows in ``COLUMNS["iface"]`` order.

    Returns:
        list[tuple]: Rows with ``in_bps`` and ``out_bps`` rounded.

    Raises:
        None
    """
    return [
        r if r[15] is None else (*r[:15], float(round(r[15])), float(round(r[16])), *r[17:])
        for r in rows
    ]


def _ts(row: tuple) -> int:
    """Return a native row's timestamp (third column).

    Args:
        row (tuple): Native row.

    Returns:
        int: ``ts_us``.

    Raises:
        None
    """
    return row[2]


class Writer:
    """Writes batches of native rows; one ``write`` or ``move`` is one transaction."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        layout: Layout,
        *,
        round_rates: bool = False,
        project: bool = False,
    ) -> None:
        """Bind a writer to a connection and layout.

        Args:
            conn (sqlite3.Connection): Writer connection in autocommit mode.
            layout (Layout): Candidate layout.
            round_rates (bool): Round bit rates before storing (size variant).
            project (bool): Maintain the ``cur_iface`` projection.

        Returns:
            None

        Raises:
            sqlite3.Error: If the staging boundary cannot be read.
        """
        self.conn, self.layout = conn, layout
        self.round_rates, self.project = round_rates, project
        self._sql: dict[str, str] = {}
        self.boundary = 0
        if layout.hot:
            self.boundary = conn.execute(
                "SELECT value FROM hot_state WHERE name = 'boundary_us'"
            ).fetchone()[0]
            for kind in NATIVE_KINDS:
                marks = ", ".join("?" * len(COLUMNS[kind]))
                self._sql[Layout.hot_table(kind)] = (
                    f"INSERT OR IGNORE INTO {Layout.hot_table(kind)} VALUES ({marks})"
                )

    def _table(self, kind: str, ts_us: int) -> str:
        """Return the clustered table for a row, creating its partition on first use.

        Args:
            kind (str): Native kind.
            ts_us (int): Row timestamp.

        Returns:
            str: Table name, with its prepared ``INSERT`` cached.

        Raises:
            sqlite3.Error: If partition creation fails.
        """
        table = self.layout.native_table(kind, ts_us)
        if table not in self._sql:
            self.layout.ensure(self.conn, kind, ts_us)
            marks = ", ".join("?" * len(COLUMNS[kind]))
            self._sql[table] = f"INSERT OR IGNORE INTO {table} VALUES ({marks})"
        return table

    def _route(self, kind: str, rows: list[tuple]) -> list[tuple[str, list[tuple]]]:
        """Split timestamp-sorted rows by destination table.

        Args:
            kind (str): Native kind.
            rows (list[tuple]): Rows sorted by ``ts_us``.

        Returns:
            list[tuple[str, list[tuple]]]: ``(table, rows)`` groups.

        Raises:
            sqlite3.Error: If partition creation fails.
        """
        groups, lo = [], 0
        if self.layout.hot:
            lo = bisect_left(rows, self.boundary, key=_ts)
            if lo < len(rows):
                groups.append((Layout.hot_table(kind), rows[lo:]))
            rows, lo = rows[:lo], 0
        if rows and (
            not self.layout.partitioned or _ts(rows[0]) // DAY_US == _ts(rows[-1]) // DAY_US
        ):
            return [(self._table(kind, _ts(rows[0])), rows), *groups]
        while lo < len(rows):
            hi = bisect_left(rows, (_ts(rows[lo]) // DAY_US + 1) * DAY_US, lo=lo, key=_ts)
            groups.append((self._table(kind, _ts(rows[lo])), rows[lo:hi]))
            lo = hi
        return groups

    def write(self, batch: dict[str, list[tuple]]) -> int:
        """Commit one batch: every kind's rows in a single transaction.

        Args:
            batch (dict[str, list[tuple]]): Native kind to timestamp-sorted rows.

        Returns:
            int: Rows inserted; replayed duplicates are ignored and not counted.

        Raises:
            sqlite3.Error: If the transaction fails; it is rolled back.
        """
        conn, inserted = self.conn, 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for kind, rows in batch.items():
                if not rows:
                    continue
                stored = _rounded(rows) if kind == "iface" and self.round_rates else rows
                for table, part in self._route(kind, stored):
                    inserted += conn.executemany(self._sql[table], part).rowcount
                if kind == "iface" and self.project:
                    conn.executemany(PROJECTION_UPSERT, rows)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return inserted

    def move(self, new_boundary: int) -> int:
        """Drain staged rows older than ``new_boundary`` into the clustered tables.

        Rows are inserted in primary-key order, so each series' tail page
        is touched once per move instead of once per batch.

        Args:
            new_boundary (int): New staging boundary, Unix microseconds.

        Returns:
            int: Rows moved.

        Raises:
            sqlite3.Error: If the transaction fails; it is rolled back.
        """
        conn, moved = self.conn, 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for kind in NATIVE_KINDS:
                hot, key = Layout.hot_table(kind), ", ".join(COLUMNS[kind][i][0] for i in range(3))
                first = conn.execute(f"SELECT MIN(ts_us) FROM {hot}").fetchone()[0]
                day = (first // DAY_US) * DAY_US if first is not None else new_boundary
                while day < new_boundary:
                    table = self._table(kind, day)
                    moved += conn.execute(
                        f"INSERT OR IGNORE INTO {table} SELECT * FROM {hot}"
                        f" WHERE ts_us >= ? AND ts_us < ? ORDER BY {key}",
                        (day, min(day + DAY_US, new_boundary)),
                    ).rowcount
                    day += DAY_US
                conn.execute(f"DELETE FROM {hot} WHERE ts_us < ?", (new_boundary,))
            conn.execute(
                "UPDATE hot_state SET value = ? WHERE name = 'boundary_us'", (new_boundary,)
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        self.boundary = new_boundary
        return moved
