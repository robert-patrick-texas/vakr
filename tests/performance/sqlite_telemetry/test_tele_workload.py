"""The adapter benchmark's workload stays the MVP workload (27.2) and its helpers stay sound.

Runs in the default suite in well under a second; the benchmark itself is
``python -m tests.performance.sqlite_telemetry.bench_adapter``.

Dependencies:
    pytest; tests.performance.sqlite_telemetry.

Example:
    $ uv run pytest tests/performance/sqlite_telemetry
"""

from collections import Counter
from pathlib import Path

from tests.performance.sqlite_telemetry.bench_probe import family_of, project, sizes
from tests.performance.sqlite_telemetry.tele_workload import Generator, Rollups, build_topology
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    telemetry_migrations,
)
from vakr.domain.ids import BatchId, new_id


def test_workload_is_the_mvp_workload() -> None:
    """27.2: 3,200 interface, 200 ICMP/SNMP/poll series, 2 health series, one row each a minute.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    topo = build_topology(21)
    gen, roll = Generator(topo, 21), Rollups()
    first, second = gen.minute(0), gen.minute(1)
    kinds = Counter(type(o).__name__ for o in second)
    assert kinds == {"InterfaceObservation": 3200, "IcmpObservation": 200,
                     "SnmpDeviceObservation": 200, "DevicePoll": 200,
                     "CollectorHealthObservation": 2}  # fmt: skip
    assert [o.timestamp for o in second] == sorted(o.timestamp for o in second)
    roll.add(first + second)
    buckets = roll.take(0)
    assert len(buckets) == 3200 * 6 + 200 * 3
    assert roll.take(0) == []


def test_bench_helpers_measure_a_small_store(tmp_path: Path) -> None:
    """The size probe and projection read a real store written through the adapter.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    store = SqliteTelemetryStore(path)
    backend = SqliteTelemetry(store)
    gen = Generator(build_topology(21), 21)
    for m in range(3):
        backend.writer.write_batch(new_id(BatchId), gen.minute(m))
    backend.maintain(gen.minute(20)[0].timestamp)
    store.close()
    measured = sizes(path)
    assert measured["obs_iface"]["rows"] == 3 * 3200
    assert project(measured, 3 / 1440)["native_mb_per_day"] > 0
    assert (family_of("obs_poll_d20260901"), family_of("dict_device")) == ("obs_poll", None)
