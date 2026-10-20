"""Small-database helpers shared by the ADR-021 benchmark unit tests.

The tests check the benchmark's generator, writer, queries and rollups on
a three-device inventory, so they run in about a second while exercising
the same code paths as the full 3,200-series runs.

Dependencies:
    sqlite3: Standard-library driver.
    sqlc_*: The benchmark modules under test.

Safety:
    Databases live in pytest's temporary directory.

Example:
    >>> topo = small_topology()
    >>> len(topo.interfaces)
    60
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db
from tests.performance.sqlite_compact.sqlc_topology import Topology, build_topology
from tests.performance.sqlite_compact.sqlc_workload import MinuteBatch, Workload
from tests.performance.sqlite_compact.sqlc_writer import Writer, create_base

START_US = 1_788_220_800_000_000  # 2026-09-01T00:00:00Z
SEED = 21

OPEN: list[sqlite3.Connection] = []
"""Connections opened for the current test; the autouse fixture closes them."""

try:
    with closing(sqlite3.connect(":memory:")) as _probe:
        _probe.execute("SELECT percentile_cont(1, 0.5)")
    HAS_PERCENTILE = True
except sqlite3.OperationalError:
    HAS_PERCENTILE = False


def track(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Register a connection to be closed when the current test ends.

    Args:
        conn (sqlite3.Connection): An open connection.

    Returns:
        sqlite3.Connection: The same connection.

    Raises:
        None
    """
    OPEN.append(conn)
    return conn


def small_topology() -> Topology:
    """Build a three-device inventory: two routers and one switch (60 interfaces).

    Args:
        None

    Returns:
        Topology: The inventory, two collectors.

    Raises:
        None
    """
    return build_topology(SEED, routers=2, switches=1)


def load(
    path: Path, layout: Layout, minutes: range, *, project: bool = False, move_every: int = 0
) -> tuple[sqlite3.Connection, Writer, list[MinuteBatch]]:
    """Create a database and ingest the given minutes in one-minute batches.

    Args:
        path (Path): Database file.
        layout (Layout): Candidate layout.
        minutes (range): Minute indexes since ``START_US``.
        project (bool): Maintain the ``cur_iface`` projection.
        move_every (int): With a hot layout, drain staging every N minutes
            (boundary two minutes behind); 0 never drains.

    Returns:
        tuple: The writer connection, the writer, and the generated batches.

    Raises:
        sqlite3.Error: If ingest fails.
    """
    conn = track(open_db(str(path), Pragmas()))
    topo = small_topology()
    create_base(conn, topo, layout, project=project)
    writer = Writer(conn, layout, project=project)
    wl, batches = Workload(topo, START_US), []
    for m in minutes:
        b = wl.minute(m)
        batches.append(b)
        writer.write({"iface": b.iface, "icmp": b.icmp, "snmp": b.snmp})
        if layout.hot and move_every and (m + 1) % move_every == 0:
            writer.move(START_US + (m - 1) * 60_000_000)
    return conn, writer, batches


def all_iface_rows(batches: list[MinuteBatch]) -> list[tuple]:
    """Flatten generated interface rows, sorted by natural key.

    Args:
        batches (list[MinuteBatch]): Generated minutes.

    Returns:
        list[tuple]: Interface rows ordered by ``(interface, collector, ts)``.

    Raises:
        None
    """
    return sorted(r for b in batches for r in b.iface)
