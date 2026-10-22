"""The collector's metrics snapshot: written at start, then every 15 s (OBS-001, E.33).

The collector has no listener; ``vakr-api`` reads this file at each scrape.
The families are read on the event loop, where the counters live; the
process readings, the encoding, and the atomic write happen in a worker
thread. A failed write is logged (the first time as a warning, later at
debug level) and tried again next time: metrics trouble never stops
collection. A clean stop removes the file, so the API reports ``missing``
at once instead of ``stale`` after 45 s.

Dependencies:
    vakr.app.collector.metrics_families: The families.
    vakr.metrics.snapshot, service: The file and the process families.

Safety:
    Writes only ``<run dir>/collector-metrics.json``, mode 0640.

Example:
    >>> WRITE_INTERVAL_S
    15.0
"""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from vakr.app.collector.metrics_families import (
    fault_family,
    latency_families,
    scheduler_families,
    spool_families,
)
from vakr.metrics.catalog import build
from vakr.metrics.exposition import Family
from vakr.metrics.service import process_families
from vakr.metrics.snapshot import (
    SNAPSHOT_FILE,
    WRITE_INTERVAL_S,
    SnapshotTooLargeError,
    encode_snapshot,
    write_snapshot,
)

if TYPE_CHECKING:
    from vakr.app.collector.maintenance import TelemetryUpkeep
    from vakr.app.collector.service import CollectorService

_LOG = logging.getLogger(__name__)


def upkeep_families(upkeep: TelemetryUpkeep) -> list[Family]:
    """Read the telemetry upkeep passes: counts, last success, last duration (OBS-007).

    Args:
        upkeep: The upkeep timer.

    Returns:
        list[Family]: The upkeep families.

    Raises:
        None
    """
    kinds = sorted(upkeep.passes)
    last = {k: t.timestamp() for k, t in upkeep.last_success.items()}
    return [
        build("vakr_collector_upkeep_passes_total", (((k,), upkeep.passes[k]) for k in kinds)),
        build("vakr_collector_upkeep_failures_total", (((k,), upkeep.failures[k]) for k in kinds)),
        build("vakr_collector_upkeep_last_success_timestamp_seconds",
              (((k,), last.get(k)) for k in kinds)),
        build("vakr_collector_upkeep_last_duration_seconds",
              (((k,), upkeep.last_duration_s.get(k)) for k in kinds)),
    ]  # fmt: skip


def collector_families(service: CollectorService) -> list[Family]:
    """Read every collector family from memory (call on the event loop).

    Args:
        service: The running service.

    Returns:
        list[Family]: The families, without the process readings.

    Raises:
        None
    """
    snap = service.scheduler.snapshot()  # a peek: the health period stays open
    configured = float(service.replanner.defaults.interval_s)
    interfaces = len(service.plans.interface_ids())
    return [
        *scheduler_families(snap, configured),
        build("vakr_collector_interfaces_sampled", [((), interfaces)]),
        *latency_families(service.scheduler.stats),
        *spool_families(snap, service.ingester.health(), service.ingester.status),
        fault_family(dict(service.tally.lifetime)),
        *upkeep_families(service.upkeep),
    ]


def _write(path: Path, families: list[Family], written_at: float) -> None:
    """Add the process readings, encode, and write (blocking; a worker thread).

    Args:
        path: The snapshot file.
        families: From ``collector_families``.
        written_at: Unix seconds.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written.
        SnapshotTooLargeError: If the snapshot is over its bound.
    """
    write_snapshot(path, encode_snapshot([*process_families("collector"), *families], written_at))


async def write_metrics(service: CollectorService, path: Path, *, warned: bool) -> bool:
    """Write one snapshot; failures are logged, never raised.

    Args:
        service: The running service.
        path: The snapshot file.
        warned: True once a failure was logged as a warning.

    Returns:
        bool: The new ``warned``.

    Raises:
        None
    """
    families = collector_families(service)
    try:
        await asyncio.to_thread(_write, path, families, service.clock.now_utc().timestamp())
    except (OSError, SnapshotTooLargeError) as exc:
        level = logging.DEBUG if warned else logging.WARNING
        _LOG.log(level, "cannot write the metrics snapshot %s (%s); vakr-api reports the "
                 "collector's metrics as missing", path, type(exc).__name__,
                 extra={"event_id": "collector.metrics_failed"})  # fmt: skip
        return True
    return warned


async def metrics_loop(service: CollectorService) -> None:
    """Write the snapshot at once and every ``WRITE_INTERVAL_S`` until the loops stop.

    Args:
        service: The running service.

    Returns:
        None

    Raises:
        None
    """
    path = service.parts.paths.run_dir / SNAPSHOT_FILE
    stop, warned = service.loops_stop, False
    while not stop.is_set():
        try:
            warned = await write_metrics(service, path, warned=warned)
        except Exception as exc:  # noqa: BLE001 - a metrics bug must never stop collection
            _LOG.error("the metrics snapshot failed (%s); it runs again next interval",
                       type(exc).__name__,
                       extra={"event_id": "collector.timer_failed"})  # fmt: skip
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), WRITE_INTERVAL_S)
    with contextlib.suppress(OSError):
        await asyncio.to_thread(path.unlink, missing_ok=True)
