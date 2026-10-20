# A seeded, reproducible PRNG is the point: the workload must be identical on
# every re-run. Nothing generated here is a secret.
# ruff: noqa: S311
"""Deterministic one-minute telemetry batches shaped like real IOS XE polling.

Each call to ``Workload.minute`` returns every observation one node stores
for that minute: 3,200 interface rows, 200 ICMP rows, and 200 SNMP device
rows, in arrival (timestamp) order. Traffic follows a diurnal and weekly
cycle with noise and bursts; a few polls fail each day (rows with ``NULL``
counters and rates flagged ``NOT_ATTEMPTED``), long gaps and reloads
invalidate the next baseline, CoPP-style probe loss stores ``-1`` RTTs, and
observation IDs are 16-byte UUIDv7 values. Columns follow blueprint
10.1-10.3; the order matches ``sqlc_schema.COLUMNS``.

Dependencies:
    random: One ``Random`` stream per minute and one per UTC day, so any
        minute or day can be regenerated from the seed alone.
    sqlc_topology: The seeded inventory and UUID helpers.
    vakr.domain: Quality flags and state enums (stored as integer codes).

Safety:
    Missing data is never zero: failed polls store ``NULL`` values plus a
    quality flag, never a zero rate (blueprint 4.3).

Example:
    >>> from tests.performance.sqlite_compact.sqlc_topology import build_topology
    >>> wl = Workload(build_topology(21), start_us=1_788_220_800_000_000)
    >>> b = wl.minute(0)
    >>> len(b.iface), len(b.icmp), len(b.snmp)
    (3200, 200, 200)
"""

import math
import random
from dataclasses import dataclass, field
from operator import itemgetter

from tests.performance.sqlite_compact.sqlc_icmp import icmp_row, snmp_row
from tests.performance.sqlite_compact.sqlc_topology import (
    DAY_US,
    MINUTE_US,
    Topology,
    uuid7_bytes,
    vocab_codes,
)
from vakr.domain.enums import BaselineState, CounterFamily
from vakr.domain.quality import Quality

BASELINE = vocab_codes(BaselineState)
FAMILY_HC = vocab_codes(CounterFamily)[CounterFamily.HC64]
MAX_GAP_US = 5 * MINUTE_US  # Appendix E.4: 5 x interval for HC counters
_TS = itemgetter(2)


@dataclass(frozen=True, slots=True)
class MinuteBatch:
    """All rows for one minute, each list sorted by timestamp. Immutable.

    Attributes:
        minute (int): Minute index since the workload start.
        iface (list[tuple]): Interface observation rows.
        icmp (list[tuple]): ICMP observation rows.
        snmp (list[tuple]): SNMP device observation rows.
    """

    minute: int
    iface: list[tuple]
    icmp: list[tuple]
    snmp: list[tuple]


@dataclass(slots=True)
class _SeriesState:
    """Mutable counter state per interface series (index = series number).

    Attributes:
        prev_ts (list[int | None]): Timestamp of the last good sample.
        counters (list[list[int]]): in/out octets, packets, errors per series.
        boot_seen (dict[tuple[int, int], int]): Boot time each collector last saw.
    """

    prev_ts: list[int | None]
    counters: list[list[int]]
    boot_seen: dict[tuple[int, int], int] = field(default_factory=dict)


def traffic_level(ts_us: int) -> float:
    """Return the diurnal and weekly traffic multiplier at a UTC time.

    Traffic bottoms out at 04:00 and peaks at 16:00 local time (UTC-6), and
    weekends carry 55% of weekday traffic.

    Args:
        ts_us (int): Unix time in microseconds.

    Returns:
        float: Multiplier between about 0.16 and 1.0.

    Raises:
        None
    """
    hour = (ts_us / 3_600_000_000 - 6.0) % 24.0
    level = 0.3 + 0.7 * (0.5 - 0.5 * math.cos(2 * math.pi * (hour - 4.0) / 24.0))
    weekday = ((ts_us - 6 * 3_600_000_000) // DAY_US + 3) % 7  # 1970-01-01 was a Thursday
    return level * (0.55 if weekday >= 5 else 1.0)


def day_events(
    seed: int, day: int, n_devices: int, n_coll: int
) -> dict[tuple[int, int], tuple[range, bool]]:
    """Choose one UTC day's failed-poll windows: device outages, path cuts, reloads.

    About 2% of devices have an outage seen by every position, 3% reload
    (four unanswered minutes, then a reset uptime), and 3% lose the path
    from one position only.

    Args:
        seed (int): Workload seed.
        day (int): Days since the Unix epoch.
        n_devices (int): Device count; keys are 1..n.
        n_coll (int): Collector count; keys are 1..n.

    Returns:
        dict[tuple[int, int], tuple[range, bool]]: ``(device_key,
        collector_key)`` to the failing minutes of the day and whether the
        window ends in a reload.

    Raises:
        None
    """
    rng = random.Random(seed * 7919 + day)
    windows: dict[tuple[int, int], tuple[range, bool]] = {}
    for dev in range(1, n_devices + 1):
        roll, start = rng.random(), rng.randrange(0, 1400)
        if roll < 0.05:  # device-wide, so every position sees it
            reload = roll >= 0.02
            length = 4 if reload else rng.randint(5, 25)
            for c in range(1, n_coll + 1):
                windows[dev, c] = (range(start, start + length), reload)
        elif roll < 0.08:
            windows[dev, rng.randint(1, n_coll)] = (range(start, start + rng.randint(2, 12)), False)
    return windows


class Workload:
    """Generates consecutive minutes for a topology; carries counter state.

    Calling ``minute(m)`` for increasing ``m`` keeps counters and baselines
    continuous. Starting at any ``m`` is allowed: the first sample of each
    series is flagged ``BASELINE_FIRST_SAMPLE`` like a fresh collector.
    """

    def __init__(self, topo: Topology, start_us: int) -> None:
        """Prepare per-series state for a run starting at a UTC-day boundary.

        Args:
            topo (Topology): Seeded inventory.
            start_us (int): Start of minute 0, in Unix microseconds.

        Returns:
            None

        Raises:
            ValueError: If ``start_us`` is not aligned to a UTC day.
        """
        if start_us % DAY_US:
            raise ValueError("start_us must be a UTC midnight")
        self.topo, self.start_us = topo, start_us
        self.n_coll = len(topo.collectors)
        n = len(topo.interfaces) * self.n_coll
        self._state = _SeriesState([None] * n, [[0] * 6 for _ in range(n)])
        boot_rng = random.Random(topo.seed + 1)
        self._boot = {d.key: start_us - boot_rng.randrange(5, 300) * DAY_US for d in topo.devices}
        self._events: dict[int, dict[tuple[int, int], tuple[range, bool]]] = {}

    def _failed(self, dev_key: int, coll_key: int, ts_us: int) -> bool:
        """Report whether a poll fails, applying a reload when a window ends.

        Args:
            dev_key (int): Device key.
            coll_key (int): Collector key.
            ts_us (int): Minute start of the poll.

        Returns:
            bool: True when the poll falls in a failure window.

        Raises:
            None
        """
        day, minute = divmod(ts_us // MINUTE_US, 1440)
        if day not in self._events:
            self._events = {
                day: day_events(self.topo.seed, day, len(self.topo.devices), self.n_coll)
            }
        window, reload = self._events[day].get((dev_key, coll_key), (range(0), False))
        if reload and minute == window.stop:
            self._boot[dev_key] = max(self._boot[dev_key], ts_us)  # reload finished
        return minute in window

    def minute(self, m: int) -> MinuteBatch:
        """Generate every observation for minute ``m``.

        Args:
            m (int): Minute index since ``start_us``.

        Returns:
            MinuteBatch: Interface, ICMP, and SNMP rows sorted by timestamp.

        Raises:
            None
        """
        t0 = self.start_us + m * MINUTE_US
        rng = random.Random(self.topo.seed * 1_000_003 + m)
        level = traffic_level(t0)
        iface: list[tuple] = []
        icmp: list[tuple] = []
        snmp: list[tuple] = []
        for dev in self.topo.devices:
            for c in range(self.n_coll):
                ck = c + 1
                ts = t0 + dev.phase_us[c] + rng.randrange(0, 80_000)
                poll_uuid = uuid7_bytes(ts // 1000, rng.getrandbits(74))
                down = self._failed(dev.key, ck, t0)
                icmp.append(icmp_row(rng, dev, ck, ts, poll_uuid, down))
                boot = self._boot[dev.key]
                reset = self._state.boot_seen.setdefault((dev.key, ck), boot) != boot
                self._state.boot_seen[dev.key, ck] = boot
                snmp.append(snmp_row(rng, dev, ck, ts + 90_000, poll_uuid, down, boot, reset))
                self._iface_rows(
                    iface, rng, (dev.key, ck), ts + 150_000, poll_uuid, (down, reset, level)
                )
        for rows in (iface, icmp, snmp):
            rows.sort(key=_TS)
        return MinuteBatch(m, iface, icmp, snmp)

    def _iface_rows(
        self,
        out: list[tuple],
        rng: random.Random,
        keys: tuple[int, int],
        ts: int,
        poll_uuid: bytes,
        ctx: tuple[bool, bool, float],
    ) -> None:
        """Append one poll's interface rows (blueprint 10.3 column order).

        Args:
            out (list[tuple]): Destination list.
            rng (random.Random): The minute's generator.
            keys (tuple[int, int]): ``(device_key, collector_key)``.
            ts (int): Stage 2 completion time shared by the poll's rows.
            poll_uuid (bytes): The device poll's UUID.
            ctx (tuple[bool, bool, float]): Poll failed, uptime reset seen,
                and the traffic multiplier.

        Returns:
            None

        Raises:
            None
        """
        dev_key, ck = keys
        down, reset, level = ctx
        rand, bits, st = rng.random, rng.getrandbits, self._state
        for ik in self.topo.devices[dev_key - 1].iface_keys:
            itf = self.topo.interfaces[ik - 1]
            s = (ik - 1) * self.n_coll + ck - 1
            obs = uuid7_bytes(ts // 1000, bits(74))
            if down:
                out.append(
                    (
                        ik,
                        ck,
                        ts,
                        obs,
                        poll_uuid,
                        dev_key,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        BASELINE[BaselineState.NO_BASELINE],
                        int(Quality.NOT_ATTEMPTED),
                    )
                )
                continue
            cnt, prev = st.counters[s], st.prev_ts[s]
            elapsed = ts - prev if prev is not None else MINUTE_US
            cap = itf.capacity_bps * 0.97
            burst = 2.0 if rand() < 0.01 else 1.0
            true_in = min(
                cap, itf.capacity_bps * itf.base_util * level * burst * (0.88 + 0.24 * rand())
            )
            true_out = min(cap, true_in * itf.out_ratio * (0.85 + 0.3 * rand()))
            p_err = 0.08 if itf.error_prone else 0.0004
            e_in = 1 + int(rand() ** 3 * 400) if rand() < p_err else 0
            e_out = 1 + int(rand() * 3) if rand() < p_err / 4 else 0
            if reset:
                cnt[:] = [bits(20), bits(20), bits(12), bits(12), 0, 0]
            d = (
                int(true_in * elapsed / 8e6),
                int(true_out * elapsed / 8e6),
                int(true_in * elapsed / 8e6 / itf.pkt_bytes),
                int(true_out * elapsed / 8e6 / itf.pkt_bytes),
                e_in,
                e_out,
            )
            for i in range(6):
                cnt[i] += d[i]
            st.prev_ts[s] = ts
            if prev is None or reset or elapsed > MAX_GAP_US:
                flag = (
                    Quality.BASELINE_FIRST_SAMPLE
                    if prev is None
                    else Quality.BASELINE_UPTIME_RESET
                    if reset
                    else Quality.BASELINE_GAP
                )
                state = BaselineState.NO_BASELINE if prev is None else BaselineState.INVALIDATED
                out.append(
                    (
                        ik,
                        ck,
                        ts,
                        obs,
                        poll_uuid,
                        dev_key,
                        itf.ifindex,
                        1,
                        1,
                        *cnt,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        FAMILY_HC,
                        0,
                        BASELINE[state],
                        int(flag),
                    )
                )
                continue
            sec = elapsed / 1e6
            q = int(Quality.RETRIED) if rand() < 0.001 else 0
            out.append(
                (
                    ik,
                    ck,
                    ts,
                    obs,
                    poll_uuid,
                    dev_key,
                    itf.ifindex,
                    1,
                    1,
                    *cnt,
                    d[0] * 8 / sec,
                    d[1] * 8 / sec,
                    d[2] / sec,
                    d[3] / sec,
                    e_in,
                    e_out,
                    e_in / sec,
                    e_out / sec,
                    elapsed,
                    FAMILY_HC,
                    0,
                    BASELINE[BaselineState.VALID],
                    q,
                )
            )
