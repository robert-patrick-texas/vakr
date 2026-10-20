"""Retention: horizons, whole-partition drops, catalog consistency, and coverage (E.10).

Covers RET-001..003 (30 days native, rollups through day 400), RET-007
(only expired telemetry goes), ROLL-T04 (native expires only behind
rollup coverage), and the Compact rule that partitions are dropped whole,
never ``DELETE``d or ``VACUUM``ed, while reads see exactly the policy.

Dependencies:
    pytest; vakr.adapters.sqlite.telemetry; vakr.config.loader; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_retention.py
"""

from datetime import timedelta
from pathlib import Path

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import (
    BUCKET,
    T0,
    at,
    bucket,
    open_store,
    raw,
    tables_named,
    write,
)
from vakr.adapters.sqlite.telemetry import SqliteTelemetry, retention_policy
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.observations import Observation
from vakr.domain.paging import PageRequest
from vakr.domain.series import Metric, TimeWindow

EVERYTHING = TimeWindow(at(-30), at(500))


def _day_of_rows(
    day: int, dev: DeviceId, iface: InterfaceId, col: CollectorId
) -> list[Observation]:
    """Build one row of every native type at 06:00 and 18:00 of a day.

    Args:
        day: Days after ``T0``.
        dev: Device UUID.
        iface: Interface UUID.
        col: Collector UUID.

    Returns:
        list[Observation]: Ten observations.

    Raises:
        None
    """
    out: list[Observation] = []
    for hour in (6, 18):
        t = at(day, hour)
        out += [s.sample(dev, iface, col, t), s.icmp(dev, col, t), s.snmp(dev, col, t),
                s.poll(dev, col, t), s.health(col, t)]  # fmt: skip
    return out


def _catalog_matches_schema(path: Path) -> None:
    """Assert every catalogued partition exists and every partition is catalogued.

    Args:
        path: Database file.

    Returns:
        None

    Raises:
        AssertionError: If the catalog and ``sqlite_schema`` disagree.
    """
    catalog = {r[0] for r in raw(path, "SELECT table_name FROM partition_catalog")}
    tables = {t for t in tables_named(path, "obs_") + tables_named(path, "roll_")
              if not t.endswith("_hot")}  # fmt: skip
    assert catalog == tables


def test_roll_t04_nothing_expires_without_rollup_coverage(tmp_path: Path) -> None:
    """ROLL-T04: native data far past 30 days stays while no rollup covers it.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    write(backend, _day_of_rows(0, dev, iface, col))
    backend.maintain(at(1))
    report = backend.retention.apply(at(90))
    assert (report.native_cutoff, report.native_removed) == (None, 0)
    assert backend.retention.native_horizon() is None
    assert len(tables_named(store.path, "obs_icmp_d")) == 1
    assert (
        len(backend.query.read_raw(ObservationType.ICMP, dev, EVERYTHING, PageRequest()).items) == 2
    )
    store.close()


def test_ret001_whole_days_drop_behind_the_horizon(tmp_path: Path) -> None:
    """RET-001, RET-007, E.10: whole expired days are dropped; the partial day is hidden.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    for day in range(4):
        write(backend, _day_of_rows(day, dev, iface, col))
    backend.maintain(at(4))  # everything into day partitions
    backend.rollups.write([bucket(iface, col, Metric.IN_BPS, at(3, 12), 1.0)])
    report = backend.retention.apply(at(33, 12))  # 30 days back is day 3 12:00
    assert report.native_cutoff == at(3, 12)
    assert report.native_removed == 35  # days 0-2 whole (30 rows) and day 3's 06:00 rows (5)
    assert backend.retention.native_horizon() == at(3, 12)
    for family in ("iface", "icmp", "snmp", "poll", "health"):
        assert tables_named(store.path, f"obs_{family}_d") == [f"obs_{family}_d20261004"]
    _catalog_matches_schema(store.path)
    assert raw(store.path, "PRAGMA freelist_count")[0][0] > 0  # pages freed for reuse, no VACUUM
    page = backend.query.read_raw(ObservationType.ICMP, dev, EVERYTHING, PageRequest())
    assert [o.timestamp for o in page.items] == [at(3, 18)]  # day 3's 06:00 row is expired
    current = backend.current.device_state(dev, col, now=at(33, 12))
    assert current is not None
    assert current.icmp is not None
    assert current.icmp.timestamp == at(3, 18)
    again = backend.retention.apply(at(33, 12))
    assert (again.native_cutoff, again.native_removed) == (at(3, 12), 0)
    earlier = backend.retention.apply(at(32))
    assert backend.retention.native_horizon() == at(3, 12)  # the horizon never moves back
    assert earlier.native_removed == 0
    store.close()


def test_ret002_rollups_expire_by_bucket_and_drop_by_month(tmp_path: Path) -> None:
    """RET-002, RET-003, E.10: buckets past day 400 vanish; a month drops once wholly past it.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    iface, col = new_id(InterfaceId), new_id(CollectorId)
    metrics = (Metric.IN_BPS, Metric.OUT_PPS)
    october = [bucket(iface, col, m, at(d), 1.0) for d in (0, 30) for m in metrics]
    november = [bucket(iface, col, Metric.IN_BPS, at(31 + d), 2.0) for d in (0, 10)]
    backend.rollups.write([*october, *november])
    assert tables_named(store.path, "roll_iface_m") == ["roll_iface_m202610", "roll_iface_m202611"]
    report = backend.retention.apply(at(31 + 5) + timedelta(days=400))
    assert report.rollup_cutoff == at(36)
    assert report.rollups_removed == 5  # all four October buckets and November 1st
    assert tables_named(store.path, "roll_iface_m") == ["roll_iface_m202611"]
    _catalog_matches_schema(store.path)
    window = TimeWindow(at(0), at(60))
    assert [b.bucket_start for b in backend.rollups.read(iface, col, Metric.IN_BPS, window,
                                                          limit=10)] == [at(41)]  # fmt: skip
    assert backend.rollups.coverage_end() == at(41) + BUCKET
    assert backend.retention.apply(at(41) + BUCKET + timedelta(days=400)).rollups_removed == 1
    assert backend.rollups.coverage_end() is None
    assert tables_named(store.path, "roll_iface_m") == []
    store.close()


def test_retention_policy_comes_from_configuration() -> None:
    """E.10: the policy reads ``telemetry.native_retention_days`` and ``rollup_retention_days``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    config = EffectiveConfig({"telemetry": {"native_retention_days": 7,
                                            "rollup_retention_days": 90}})  # fmt: skip
    policy = retention_policy(config)
    assert (policy.native, policy.rollup) == (timedelta(days=7), timedelta(days=90))
    default = retention_policy(EffectiveConfig({}))
    assert (default.native.days, default.rollup.days) == (30, 400)
    assert T0.tzinfo is not None
