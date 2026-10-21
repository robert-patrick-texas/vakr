"""End-to-end telemetry: history with known gaps and losses, then a live collector (WEB-T04/T05).

Times are relative to ``anchor``, the seed's "now" aligned down to the
minute, and polls are one per 60 s (the E.9 default):

- ``core1``: 24 h of polls. Probe losses at fixed offsets: 1 of 5 lost at
  ``anchor - 60 min`` (20 %), 2 of 5 at ``- 50 min`` (40 %), all 5 at
  ``- 40 min`` (100 %; SNMP skipped), for the smoke graph (ICMP-009).
- ``core1/Gi0/0/1`` (and ``Gi0/0/2``): 24 h of interface samples, with no
  samples at all from ``- 209 min`` to ``- 181 min`` (a 30-minute gap), a
  ``BASELINE_GAP`` sample at ``- 180 min`` and a ``BASELINE_DISCONTINUITY``
  sample at ``- 120 min`` (no rates: UI-010), inbound errors from ``- 35``
  to ``- 30 min``, and oper status down from ``- 300`` to ``- 291 min``.
- Other interfaces: 6 h (``core1``) or 1 h of samples; other devices: 1 h
  of polls, except ``branch9``, whose last poll is ``- 15 min`` (stale).
- Collector ``east`` reports healthy; ``west`` last reported 60 min ago,
  so the API judges it unavailable (WEB-T07).

``LiveCollector.tick`` then writes one fresh poll per minute for every
device the plan keeps polling, so freshness (E.3) never decays during a
long browser run.

Dependencies:
    vakr.adapters.sqlite.telemetry: The store and batch writer.
    tests.e2e.observations: The observation builders.

Safety:
    Writes only to the relocated install's ``telemetry.db``.

Example:
    >>> LOSS_OFFSETS[40]
    'down'
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from tests.contract import contract_samples as samples
from tests.e2e.fleet import POLLED, STALE
from tests.e2e.metadata import Seeded
from tests.e2e.observations import BOOT_UPTIME_S, device_rows, probe_rtts, sample_row
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.domain.enums import CollectorState
from vakr.domain.ids import BatchId, PollId, new_id
from vakr.domain.observations import Observation
from vakr.domain.quality import Quality

MINUTE = timedelta(minutes=1)
GAP_MINUTES = range(181, 210)
"""Offsets (minutes before the anchor) with no samples on the 24-hour interfaces."""
INVALID_AT = {180: Quality.BASELINE_GAP, 120: Quality.BASELINE_DISCONTINUITY}
ERROR_MINUTES = range(30, 36)
OPER_DOWN_MINUTES = range(291, 301)
LOSS_OFFSETS = {60: "one", 50: "loss", 40: "down"}
"""``core1`` polls with lost probes: one lost, two lost, all lost."""
STALE_AGE = 15
BATCH = 20_000


def _history_minutes(spec_name: str, behavior: str) -> range:
    """Return the poll offsets (minutes before the anchor) to seed for a device.

    Args:
        spec_name: Device name.
        behavior: Its planned behaviour.

    Returns:
        range: Offsets, oldest first is not required.

    Raises:
        None
    """
    if spec_name == "core1":
        return range(0, 24 * 60)
    if behavior == STALE:
        return range(STALE_AGE, STALE_AGE + 60)
    return range(0, 60) if behavior in POLLED else range(0)


def _rtts_for(name: str, behavior: str, offset: int, minute: int) -> tuple[int | None, ...]:
    """Return the probe group of one historical poll.

    Args:
        name: Device name.
        behavior: Planned behaviour.
        offset: Minutes before the anchor.
        minute: Minutes since the epoch.

    Returns:
        tuple: Five RTTs.

    Raises:
        None
    """
    special = LOSS_OFFSETS.get(offset) if name == "core1" else None
    if special == "one":
        rtts = list(probe_rtts(minute, "ok"))
        rtts[2] = None
        return tuple(rtts)
    if special is not None:
        return probe_rtts(minute, special)
    return probe_rtts(minute, "ok" if behavior == STALE else behavior)


def _samples(
    seeded: Seeded, name: str, at: datetime, offset: int, pid: PollId
) -> Iterator[Observation]:
    """Yield the interface samples of one historical poll of a device.

    Args:
        seeded: The stored fleet.
        name: Device name.
        at: Poll time.
        offset: Minutes before the anchor.
        pid: Poll ID.

    Returns:
        Iterator[Observation]: Samples of the interfaces whose history reaches this far.

    Raises:
        None
    """
    east = seeded.collectors["east"]
    for salt, (key, iface) in enumerate(seeded.interfaces.items()):
        spec = seeded.iface_specs[key]
        if not key.startswith(f"{name}/") or offset >= spec.history_h * 60:
            continue
        long = spec.history_h >= 24
        if long and offset in GAP_MINUTES:
            continue
        yield sample_row(iface, east, at, pid, speed_mbps=spec.speed_mbps, salt=salt,
                         invalid=INVALID_AT.get(offset) if long else None,
                         errors=3 if long and offset in ERROR_MINUTES else spec.errors,
                         oper_down=long and offset in OPER_DOWN_MINUTES)  # fmt: skip


def history(seeded: Seeded, anchor: datetime) -> Iterator[Observation]:
    """Yield every historical observation of the plan.

    Args:
        seeded: The stored fleet.
        anchor: The seed's "now", aligned to the minute (UTC).

    Returns:
        Iterator[Observation]: Polls, probes, SNMP replies, samples, and health.

    Raises:
        None
    """
    east, west = seeded.collectors["east"], seeded.collectors["west"]
    boot = anchor - timedelta(seconds=BOOT_UPTIME_S)
    for name, spec in seeded.specs.items():
        device = seeded.devices[name]
        for offset in _history_minutes(name, spec.behavior):
            at = anchor - offset * MINUTE
            rtts = _rtts_for(name, spec.behavior, offset, int(at.timestamp() // 60))
            pid, rows = device_rows(device, east, at, rtts, boot)
            yield from rows
            yield from _samples(seeded, name, at, offset, pid)
    yield samples.health(east.id, anchor, state=CollectorState.HEALTHY, spool_used_pct=1.5)
    yield samples.health(west.id, anchor - 60 * MINUTE, state=CollectorState.HEALTHY)


def write_all(backend: SqliteTelemetry, rows: Iterator[Observation]) -> int:
    """Write observations in bounded batches, as ingestion does.

    Args:
        backend: The telemetry backend.
        rows: Observations.

    Returns:
        int: How many were written.

    Raises:
        None
    """
    total, batch = 0, []
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH:
            backend.writer.write_batch(new_id(BatchId), batch)
            total, batch = total + len(batch), []
    if batch:
        backend.writer.write_batch(new_id(BatchId), batch)
        total += len(batch)
    return total


class LiveCollector:
    """Keeps the polled devices fresh while the browser tests run."""

    def __init__(self, state_dir: Path, seeded: Seeded, anchor: datetime) -> None:
        """Open the telemetry store for writing.

        Args:
            state_dir: The install's state directory.
            seeded: The stored fleet.
            anchor: Time of the newest seeded poll.

        Returns:
            None

        Raises:
            None
        """
        self.store = SqliteTelemetryStore(state_dir / TELEMETRY_FILE)
        self.backend = SqliteTelemetry(self.store)
        self.seeded = seeded
        self.last_poll = anchor
        self.boot = anchor - timedelta(seconds=BOOT_UPTIME_S)

    def seed(self, anchor: datetime) -> int:
        """Write the whole history.

        Args:
            anchor: The seed's "now".

        Returns:
            int: Observations written.

        Raises:
            None
        """
        return write_all(self.backend, history(self.seeded, anchor))

    def tick(self, now: datetime) -> int:
        """Write the next poll of every kept device when a 60 s interval has passed, and health.

        Args:
            now: The current time (UTC).

        Returns:
            int: Observations written.

        Raises:
            None
        """
        east = self.seeded.collectors["east"]
        rows: list[Observation] = [samples.health(east.id, now, state=CollectorState.HEALTHY,
                                                  spool_used_pct=1.5)]  # fmt: skip
        if now - self.last_poll >= MINUTE:
            at = self.last_poll + MINUTE * int((now - self.last_poll) / MINUTE)
            for name, spec in self.seeded.specs.items():
                if spec.behavior not in POLLED:
                    continue
                rtts = probe_rtts(int(at.timestamp() // 60), spec.behavior)
                pid, polled = device_rows(self.seeded.devices[name], east, at, rtts, self.boot)
                rows += polled
                rows += _samples(self.seeded, name, at, 0, pid)
            self.last_poll = at
        return write_all(self.backend, iter(rows))

    def close(self) -> None:
        """Close the store.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.store.close()
