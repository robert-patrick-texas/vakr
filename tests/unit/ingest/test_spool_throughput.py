"""Throughput: 2x the MVP rate with p99 append-to-durable latency within E.8's 1 s (STORE-004).

The MVP workload (27.2) produces about 1,800 observations a minute; this test
sustains 3,600 a minute (60 a second, in poll-sized bursts) for six seconds
with the packaged fsync settings, real fsyncs on a disk-backed directory,
and ingestion running into the memory telemetry adapter at the same time.
It also checks that the event loop never stalls, because the collector's
polls share it.

Dependencies:
    pytest, asyncio; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_throughput.py -s
"""

import asyncio
import time
from pathlib import Path

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    FlakyWriter,
    disk_dir,
    interface_samples,
    remove_disk_dir,
    run,
)
from vakr.ingest import Ingester, Spool, SpoolSettings
from vakr.logic.stats import p95
from vakr.ports.telemetry import TelemetryCapabilities

RATE_PER_S = 60  # 3,600 observations a minute: twice the MVP workload
BURST = 15  # observations per append, about one device poll
SECONDS = 6.0


def _p99(values: list[float]) -> float:
    """Nearest-rank 99th percentile.

    Args:
        values: Samples.

    Returns:
        float: The value at rank ceil(0.99 n).

    Raises:
        None
    """
    ordered = sorted(values)
    return ordered[max(0, -(-99 * len(ordered) // 100) - 1)]


async def _ticker(lags: list[float], stop: asyncio.Event) -> None:
    """Measure event-loop lag with a 10 ms ticker.

    Args:
        lags: Lags are appended here, in seconds.
        stop: Set to end the measurement.

    Returns:
        None

    Raises:
        None
    """
    while not stop.is_set():
        started = time.monotonic()
        await asyncio.sleep(0.01)
        lags.append(time.monotonic() - started - 0.01)


def test_store004_twice_mvp_rate_meets_the_e8_latency(tmp_path: Path) -> None:
    """STORE-004, E.8: at 2x the MVP rate, p99 append-to-durable latency is at most 1 s.

    Args:
        tmp_path: pytest's directory; replaced by a disk directory on tmpfs hosts.

    Returns:
        None

    Raises:
        None
    """
    root = disk_dir(tmp_path)

    async def scenario() -> None:
        """Append bursts on schedule while ingesting, and record latencies.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(SpoolSettings.defaults(root), COLLECTOR)
        await spool.open()
        writer = FlakyWriter()
        ingester = Ingester(spool, writer, TelemetryCapabilities(False, 50_000))
        await ingester.start()
        lags: list[float] = []
        stop = asyncio.Event()
        ticker = asyncio.create_task(_ticker(lags, stop))
        bursts, period = int(SECONDS * RATE_PER_S / BURST), BURST / RATE_PER_S
        started = time.monotonic()
        appends = []
        for index in range(bursts):
            await asyncio.sleep(max(0.0, started + index * period - time.monotonic()))
            appends.append(asyncio.create_task(spool.append(interface_samples(BURST))))
        receipts = await asyncio.gather(*appends)
        stop.set()
        await ticker
        await ingester.stop()
        await spool.close()
        latencies = [r.latency_s for r in receipts for _ in range(r.count)]  # per observation
        assert sum(r.count for r in receipts) == bursts * BURST == 360
        assert _p99(latencies) <= 1.0, sorted({round(x, 3) for x in latencies})[-5:]
        assert p95(lags) < 0.05, p95(lags)
        assert max(lags) < 0.25, max(lags)
        assert len(writer.store.observations) == 360

    try:
        run(scenario())
    finally:
        remove_disk_dir(root, tmp_path)
