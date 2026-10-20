"""Tests for the ADR-021 workload generator: shape, determinism, and missing data.

A benchmark is only reproducible if the same seed yields the same rows,
and only representative if failed polls look like they do in production:
``NULL`` values plus a quality flag, never zeros (blueprint 4.3).

Dependencies:
    sqlc_topology, sqlc_workload, sqlc_icmp: The generator under test.
    vakr.domain: Quality flags.

Example:
    $ uv run pytest tests/performance/sqlite_compact/test_sqlc_workload.py
"""

import uuid

from tests.performance.sqlite_compact.sqlc_columns import COLUMNS
from tests.performance.sqlite_compact.sqlc_icmp import ICMP_STATE, _state, summarize
from tests.performance.sqlite_compact.sqlc_testkit import START_US, small_topology
from tests.performance.sqlite_compact.sqlc_topology import build_topology, uuid7_bytes
from tests.performance.sqlite_compact.sqlc_workload import Workload, day_events, traffic_level
from vakr.domain.enums import IcmpState
from vakr.domain.quality import Quality


def test_topology_matches_the_two_position_reference_workload() -> None:
    """80 routers x 10 plus 20 switches x 40 interfaces, two collectors (27.2, 27.5).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    topo = build_topology(21)
    assert len(topo.devices) == 100
    assert len(topo.interfaces) == 1600
    assert len(topo.collectors) == 2
    assert sum(len(d.iface_keys) for d in topo.devices if d.is_switch) == 800
    assert len({i.uuid for i in topo.interfaces}) == 1600


def test_same_seed_generates_identical_rows() -> None:
    """Two generators with one seed produce byte-identical minutes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    a, b = Workload(small_topology(), START_US), Workload(small_topology(), START_US)
    for m in range(3):
        assert a.minute(m) == b.minute(m)


def test_minute_rows_have_contract_columns_and_uuid7_ids() -> None:
    """Rows match the column lists, are time-sorted, and carry unique UUIDv7 IDs.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    batch = Workload(small_topology(), START_US).minute(0)
    assert len(batch.iface) == 120
    assert len(batch.icmp) == len(batch.snmp) == 6
    for kind, rows in (("iface", batch.iface), ("icmp", batch.icmp), ("snmp", batch.snmp)):
        assert all(len(r) == len(COLUMNS[kind]) for r in rows)
        assert [r[2] for r in rows] == sorted(r[2] for r in rows)
    ids = [uuid.UUID(bytes=r[3]) for r in batch.iface]
    assert len(set(ids)) == len(ids)
    assert all(u.version == 7 for u in ids)
    assert int.from_bytes(batch.iface[0][3][:6]) == batch.iface[0][2] // 1000


def test_failed_polls_store_null_rates_and_long_gaps_invalidate_baselines() -> None:
    """Failed polls are NULL plus NOT_ATTEMPTED; the next sample after >5 min is a gap.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    topo = small_topology()
    day = START_US // 86_400_000_000
    windows = {}
    for d in range(day, day + 60):
        windows = {
            k: v
            for k, v in day_events(21, d, 3, 2).items()
            if len(v[0]) > 5 and not v[1] and v[0].start > 10
        }
        if windows:
            day_index = d - day
            break
    (dev, coll), (window, _) = next(iter(windows.items()))
    wl = Workload(topo, START_US)
    first = day_index * 1440 + window.start - 3
    rows = [
        r
        for m in range(first, day_index * 1440 + window.stop + 1)
        for r in wl.minute(m).iface
        if r[5] == dev and r[1] == coll
    ]
    failed = [r for r in rows if r[27] == int(Quality.NOT_ATTEMPTED)]
    assert failed
    assert all(r[15] is None and r[9] is None for r in failed)
    after = rows[-len(topo.devices[dev - 1].iface_keys) :]
    assert all(r[27] == int(Quality.BASELINE_GAP) and r[15] is None for r in after)
    assert not [r for r in rows if r[15] == 0.0]


def test_icmp_summary_follows_blueprint_formulas_and_states() -> None:
    """``-1`` probes are excluded; reply counts map to Appendix E.1 states.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    received, lo, avg, med, hi, jitter = summarize([1000, -1, 1200, 1100, 900])
    assert (received, lo, avg, med, hi) == (4, 900, 1050, 1050, 1200)
    assert jitter == 112  # population standard deviation, rounded
    assert summarize([-1] * 5) == (0, None, None, None, None, None)
    assert _state(4) == (ICMP_STATE[IcmpState.REACHABLE], int(Quality.PARTIAL_LOSS))
    assert _state(1) == (ICMP_STATE[IcmpState.IMPAIRED], int(Quality.SINGLE_REPLY))
    assert _state(0)[0] == ICMP_STATE[IcmpState.UNREACHABLE]


def test_traffic_level_is_diurnal_and_quieter_at_weekends() -> None:
    """Peak at 16:00 local beats 04:00; a Saturday peak is 55% of a weekday's.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tuesday = START_US  # 2026-09-01 is a Tuesday
    hour = 3_600_000_000
    assert traffic_level(tuesday + 22 * hour) > 3 * traffic_level(tuesday + 10 * hour)
    saturday = tuesday + 4 * 24 * hour
    assert (
        abs(traffic_level(saturday + 22 * hour) / traffic_level(tuesday + 22 * hour) - 0.55) < 0.01
    )
    assert uuid.UUID(bytes=uuid7_bytes(1, 0)).version == 7
