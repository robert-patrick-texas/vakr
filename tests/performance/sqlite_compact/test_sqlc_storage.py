"""Tests for the ADR-021 layouts: routing, idempotent replay, queries, rollups, retention.

These pin the behaviour the benchmark numbers depend on: partitions are
chosen by UTC day, the hot staging boundary never shows a row twice,
replayed batches insert nothing (STORE-009), the three p95 methods agree
exactly (ROLL-T01), rankings use valid deltas only (DASH-003, DASH-004),
and dropping a day removes exactly that day (E.10).

Dependencies:
    pytest: ``tmp_path`` and ``skipif``.
    sqlc_testkit: Small-inventory database builder.
    sqlc_*: Modules under test.

Example:
    $ uv run pytest tests/performance/sqlite_compact/test_sqlc_storage.py
"""

from pathlib import Path

import pytest

from tests.performance.sqlite_compact import sqlc_queries as q
from tests.performance.sqlite_compact.bench_retention import delete_day, drop_day
from tests.performance.sqlite_compact.bench_rollup import same
from tests.performance.sqlite_compact.sqlc_current import latest_by_seek, latest_from_projection
from tests.performance.sqlite_compact.sqlc_measure import quantile
from tests.performance.sqlite_compact.sqlc_rollup import bit_or_sql, compute, rollup_interfaces
from tests.performance.sqlite_compact.sqlc_rollup_icmp import rollup_icmp
from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db, tables_for
from tests.performance.sqlite_compact.sqlc_testkit import (
    HAS_PERCENTILE,
    START_US,
    all_iface_rows,
    load,
    small_topology,
    track,
)
from tests.performance.sqlite_compact.sqlc_workload import Workload
from tests.performance.sqlite_compact.sqlc_writer import Writer, create_base
from vakr.domain.quality import Quality

MIN = 60_000_000
DAY = 86_400_000_000
needs_percentile = pytest.mark.skipif(not HAS_PERCENTILE, reason="SQLite lacks percentile_cont")


def test_day_partitions_are_catalogued_and_resolved_by_window(tmp_path: Path) -> None:
    """Minutes spanning midnight land in two day tables; windows resolve to the right ones.

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    conn, _, batches = load(tmp_path / "c.db", Layout("C"), range(1435, 1445))
    assert tables_for(conn, "iface", START_US, START_US + 2 * DAY) == [
        "obs_iface_d20260901",
        "obs_iface_d20260902",
    ]
    assert tables_for(conn, "iface", START_US + DAY, START_US + DAY + MIN) == [
        "obs_iface_d20260902"
    ]
    stored = conn.execute("SELECT COUNT(*) FROM obs_iface_d20260901").fetchone()[0]
    assert stored == sum(len(b.iface) for b in batches[:5])


@pytest.mark.parametrize(
    "layout",
    [Layout("A"), Layout("B"), Layout("C"), Layout("C", uuid_index=True), Layout("C", hot=True)],
)
def test_replayed_batches_insert_nothing(tmp_path: Path, layout: Layout) -> None:
    """Re-submitting committed batches adds no logical duplicates (STORE-009, SPOOL-T05).

    Args:
        tmp_path (Path): pytest temporary directory.
        layout (Layout): Candidate under test.

    Returns:
        None

    Raises:
        None
    """
    _, writer, batches = load(tmp_path / "x.db", layout, range(30), move_every=10)
    again = sum(writer.write({"iface": b.iface, "icmp": b.icmp, "snmp": b.snmp}) for b in batches)
    assert again == 0


def test_hot_staging_keeps_each_row_in_exactly_one_table(tmp_path: Path) -> None:
    """Rows older than the boundary are clustered; newer ones are staged; none twice.

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    layout = Layout("C", hot=True)
    conn, writer, batches = load(tmp_path / "h.db", layout, range(25), move_every=10)
    staged = conn.execute("SELECT MIN(ts_us) FROM obs_iface_hot").fetchone()[0]
    clustered = conn.execute("SELECT MAX(ts_us) FROM obs_iface_d20260901").fetchone()[0]
    assert clustered < writer.boundary <= staged
    expected = all_iface_rows(batches)
    assert q.raw(conn, layout, (1, 1), (START_US, START_US + 30 * MIN)) == [
        (r[2], *r[15:21], r[27]) for r in expected if r[0] == 1 and r[1] == 1
    ]


def test_downsampled_envelope_matches_python(tmp_path: Path) -> None:
    """Bucket min/max/avg and counts equal a Python recomputation; gaps stay NULL.

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    conn, _, batches = load(tmp_path / "c.db", Layout("C"), range(40))
    window = (START_US, START_US + 7 * DAY)  # 10-minute buckets
    got = q.downsampled(conn, Layout("C"), (5, 2), window)
    rows = [r for r in all_iface_rows(batches) if r[0] == 5 and r[1] == 2]
    for b, n, valid, lo, hi, *_ in got:
        vals = [r[15] for r in rows if (r[2] - START_US) // 600_000_000 == b]
        rates = [v for v in vals if v is not None]
        assert (n, valid) == (len(vals), len(rates))
        assert (lo, hi) == ((min(rates), max(rates)) if rates else (None, None))


@needs_percentile
def test_top_utilization_methods_agree_and_rank_by_higher_direction(tmp_path: Path) -> None:
    """Built-in, window-function and Python p95 rankings are identical (DASH-003).

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    layout = Layout("C", hot=True)
    conn, _, batches = load(tmp_path / "h.db", layout, range(30), move_every=10)
    window = (START_US + 15 * MIN, START_US + 30 * MIN)
    results = [
        q.top_utilization(conn, layout, window, m, 200) for m in ("builtin", "window", "python")
    ]
    for other in results[1:]:
        assert [r[:4] for r in other] == [r[:4] for r in results[0]]
        assert all(abs(a[6] - b[6]) < 1e-9 for a, b in zip(results[0], other, strict=True))
    top = results[0][0]
    rows = [
        r
        for r in all_iface_rows(batches)
        if r[:2] == top[:2] and window[0] <= r[2] < window[1] and r[15] is not None
    ]
    assert top[4] == pytest.approx(quantile(sorted(r[15] for r in rows), 0.95))
    assert top[6] == pytest.approx(
        max(top[4], top[5])
        / conn.execute(
            "SELECT capacity_bps FROM bench_capacity WHERE interface_key = ?", top[:1]
        ).fetchone()[0]
    )


def test_top_errors_sum_valid_deltas_only(tmp_path: Path) -> None:
    """Rankings use deltas from valid baselines, never cumulative counters (DASH-004).

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    conn, _, batches = load(tmp_path / "c.db", Layout("C"), range(30))
    window = (START_US, START_US + 30 * MIN)
    got = q.top_errors(conn, Layout("C"), window, 500)
    want: dict[tuple[int, int], int] = {}
    for r in all_iface_rows(batches):
        if r[19] is not None:
            want[r[0], r[1]] = want.get((r[0], r[1]), 0) + r[19] + r[20]
    assert {(r[0], r[1]): r[2] for r in got} == {k: v for k, v in want.items() if v}


@needs_percentile
def test_rollup_methods_agree_and_match_known_samples(tmp_path: Path) -> None:
    """All three methods give identical rows with exact max, p95 and counts (ROLL-T01).

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    layout = Layout("C")
    conn, _, batches = load(tmp_path / "c.db", layout, range(32))
    bucket = (START_US + 15 * MIN, START_US + 30 * MIN)
    rows = {m: compute(conn, layout, bucket, m) for m in ("builtin", "window", "python")}
    assert same(rows["builtin"], rows["window"])
    assert same(rows["builtin"], rows["python"])
    one = next(r for r in rows["builtin"] if r[:2] == (7, 1))
    native = [
        r for r in all_iface_rows(batches) if r[:2] == (7, 1) and bucket[0] <= r[2] < bucket[1]
    ]
    rates = sorted(r[15] for r in native if r[15] is not None)
    assert one[4:7] == (len(native), 15, len(rates))
    assert one[8:10] == (max(rates), pytest.approx(quantile(rates, 0.95)))
    flags = 0
    for r in native:
        flags |= r[27]
    assert one[17] == flags
    assert rollup_interfaces(conn, layout, bucket, "builtin") == len(rows["builtin"])
    assert rollup_interfaces(conn, layout, bucket, "builtin") == len(rows["builtin"])
    assert rollup_icmp(conn, layout, bucket) == 6
    assert bit_or_sql("f").count("|") == 22


@pytest.mark.parametrize("layout", [Layout("C"), Layout("C", hot=True)])
def test_current_state_seek_matches_projection(tmp_path: Path, layout: Layout) -> None:
    """Seeking retained data and the upserted projection return the same latest rows.

    Args:
        tmp_path (Path): pytest temporary directory.
        layout (Layout): Plain or staged layout C.

    Returns:
        None

    Raises:
        None
    """
    conn, _, _ = load(tmp_path / "h.db", layout, range(1430, 1450), project=True, move_every=7)
    assert latest_by_seek(conn, layout) == latest_from_projection(conn)
    assert latest_by_seek(conn, layout, 3) == latest_from_projection(conn, 3)
    assert len(latest_from_projection(conn, 3)) == 80


def test_retention_drops_one_day_or_deletes_it(tmp_path: Path) -> None:
    """C drops exactly one day's tables; B deletes exactly that day's rows (E.10).

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    conn, _, _ = load(tmp_path / "c.db", Layout("C"), range(1435, 1445))
    assert drop_day(conn, START_US) == [
        "obs_iface_d20260901",
        "obs_icmp_d20260901",
        "obs_snmp_d20260901",
    ]
    assert tables_for(conn, "iface", 0, 2**62) == ["obs_iface_d20260902"]
    conn_b, _, batches = load(tmp_path / "b.db", Layout("B"), range(1435, 1445))
    day_rows = sum(len(b.iface) + len(b.icmp) + len(b.snmp) for b in batches[:5])
    assert delete_day(conn_b, START_US + DAY) == day_rows
    assert q.choose_width(0, 400 * DAY) == 28_800_000_000


@needs_percentile
def test_rollup_of_a_bucket_inside_an_outage_reports_zero_coverage(tmp_path: Path) -> None:
    """A bucket whose polls all failed has 0 valid and 0 available samples, not NULL.

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    layout, topo = Layout("C"), small_topology()
    conn = track(open_db(str(tmp_path / "o.db"), Pragmas()))
    create_base(conn, topo, layout)
    writer, wl = Writer(conn, layout), Workload(topo, START_US)
    for m in range(30):
        b = wl.minute(m)
        # Device 1 fails every poll of the second bucket, as during an outage.
        iface = [
            (*r[:6], *([None] * 20), r[26], int(Quality.NOT_ATTEMPTED))
            if r[5] == 1 and m >= 15
            else r
            for r in b.iface
        ]
        writer.write({"iface": iface, "icmp": b.icmp, "snmp": b.snmp})
    bucket = (START_US + 15 * MIN, START_US + 30 * MIN)
    rows = {m: compute(conn, layout, bucket, m) for m in ("builtin", "window", "python")}
    assert same(rows["builtin"], rows["window"])
    assert same(rows["builtin"], rows["python"])
    down = next(r for r in rows["builtin"] if r[3] == 1)
    assert down[6:9] == (0, 0, None)
    assert rollup_interfaces(conn, layout, bucket, "builtin") == len(rows["builtin"])
