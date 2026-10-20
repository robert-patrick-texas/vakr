# A seeded, reproducible PRNG is the point: the workload must be identical on
# every re-run. Nothing generated here is a secret.
# ruff: noqa: S311
"""Synthetic 15-minute rollup rows for sizing the 400-day store (blueprint 10.5, 27.3).

Computing 400 days of rollups from native data would mean generating 400
days of native rows first, so this module draws rollup rows straight from
the same traffic model: per bucket an average, a p95 above it and a
maximum above that, with the diurnal and weekly cycle, occasional
incomplete buckets (fewer valid samples) and, rarely, a bucket with no
row at all. Values are full-precision floats, like computed rollups.

Dependencies:
    random: One seeded stream per UTC day.
    sqlc_topology, sqlc_workload: Inventory and the traffic model.

Safety:
    Synthetic values only; no secrets.

Example:
    >>> from tests.performance.sqlite_compact.sqlc_topology import build_topology
    >>> rows = day_rows(build_topology(21), 1_788_220_800_000_000)
    >>> len(rows[0]) > 300_000, len(rows[1]) > 19_000
    (True, True)
"""

import random

from tests.performance.sqlite_compact.sqlc_topology import DAY_US, Topology
from tests.performance.sqlite_compact.sqlc_workload import traffic_level

BUCKET_US = 900_000_000
BUCKETS = 96


def _iface_rows(rng: random.Random, topo: Topology, day0: int, levels: list[float]) -> list[tuple]:
    """Build one day of interface rollup rows, sorted by key.

    Args:
        rng (random.Random): The day's generator.
        topo (Topology): Inventory.
        day0 (int): Day start, Unix microseconds.
        levels (list[float]): Traffic multiplier per bucket.

    Returns:
        list[tuple]: Rows in ``IFACE_15M`` column order.

    Raises:
        None
    """
    rand, out = rng.random, []
    for itf in topo.interfaces:
        cap = itf.capacity_bps * 0.97
        for ck in range(1, len(topo.collectors) + 1):
            for b in range(BUCKETS):
                roll = rand()
                if roll < 0.0005:
                    continue  # no row at all: the whole bucket was missed
                valid = 15 if roll > 0.004 else int(rand() * 15)
                avg = min(cap, itf.capacity_bps * itf.base_util * levels[b] * (0.9 + 0.2 * rand()))
                o_avg = min(cap, avg * itf.out_ratio * (0.9 + 0.2 * rand()))
                p95, o_p95 = min(cap, avg * (1.08 + 0.2 * rand())), min(cap, o_avg * 1.15)
                errs = int(rand() * 40) if itf.error_prone and rand() < 0.3 else 0
                out.append(
                    (
                        itf.key,
                        ck,
                        day0 + b * BUCKET_US,
                        itf.device_key,
                        15,
                        15,
                        valid,
                        15,
                        min(cap, p95 * (1.0 + 0.15 * rand())) if valid else None,
                        p95 if valid else None,
                        avg if valid else None,
                        min(cap, o_p95 * 1.1) if valid else None,
                        o_p95 if valid else None,
                        o_avg if valid else None,
                        errs,
                        0,
                        15 - valid,
                        0 if valid == 15 else 1 << 9,
                    )
                )
    return out


def _icmp_rows(rng: random.Random, topo: Topology, day0: int) -> list[tuple]:
    """Build one day of ICMP rollup rows, sorted by key.

    Args:
        rng (random.Random): The day's generator.
        topo (Topology): Inventory.
        day0 (int): Day start, Unix microseconds.

    Returns:
        list[tuple]: Rows in ``ICMP_15M`` column order.

    Raises:
        None
    """
    rand, out = rng.random, []
    for dev in topo.devices:
        for ck in range(1, len(topo.collectors) + 1):
            base = dev.rtt_us[ck - 1]
            for b in range(BUCKETS):
                lost = 1 if rand() < 0.25 else 0
                lo = int(base * (1 + 0.005 * rand()))
                med = int(base * (1.02 + 0.02 * rand()))
                p95 = int(med * (1.05 + 0.1 * rand()))
                out.append(
                    (
                        dev.key,
                        ck,
                        day0 + b * BUCKET_US,
                        15,
                        15,
                        75,
                        75 - lost,
                        lost,
                        lo,
                        med,
                        p95,
                        int(p95 * (1.0 + 0.5 * rand())),
                        int(base * 0.05 * rand()),
                        int(base * 0.04 * rand()),
                        15,
                        0,
                        0,
                        1 if lost else 0,
                    )
                )
    return out


def day_rows(topo: Topology, day0: int) -> tuple[list[tuple], list[tuple]]:
    """Generate one UTC day of interface and ICMP rollup rows.

    Args:
        topo (Topology): Inventory.
        day0 (int): Day start, Unix microseconds (UTC midnight).

    Returns:
        tuple[list[tuple], list[tuple]]: Interface rows and ICMP rows.

    Raises:
        None
    """
    rng = random.Random(topo.seed * 104_729 + day0 // DAY_US)
    levels = [traffic_level(day0 + b * BUCKET_US + BUCKET_US // 2) for b in range(BUCKETS)]
    return _iface_rows(rng, topo, day0, levels), _icmp_rows(rng, topo, day0)
