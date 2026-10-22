"""What one scrape of ``vakr-api`` collects, merges, and caches (OBS-001, OBS-005, E.33).

``MetricsService`` lives in the API context. A scrape has two halves:

1. ``live`` runs on the event loop and reads what the loop itself mutates:
   request counts and durations, event-loop lag, open connections. Reading
   them there means no other thread iterates a dictionary the loop writes.
2. ``render`` runs in a worker thread and does the blocking rest: the
   process's ``/proc`` readings, the collector's snapshot file (cached
   ``SNAPSHOT_CACHE_S``), and the inventory and database figures (cached
   ``STATE_CACHE_S``, refreshed by one thread at a time, so any number of
   scrapers costs at most one database pass per period).

Families with the same name from both processes (``process="api"`` and
``process="collector"``) are merged into one, as the format requires.

Dependencies:
    vakr.metrics: Exposition, catalog, process, requests, snapshot.
    vakr.scheduler.lag: The event-loop lag monitor.

Safety:
    Holds counts and timings only.

Example:
    >>> service = MetricsService()
    >>> "vakr_build_info" in service.render(service.live(), now_s=0.0)
    True
"""

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vakr.metrics.catalog import build
from vakr.metrics.exposition import Family, render
from vakr.metrics.process import process_stats
from vakr.metrics.requests import RequestStats
from vakr.metrics.snapshot import SnapshotRead, read_snapshot
from vakr.scheduler.lag import LoopLagMonitor
from vakr.version import get_version

STATE_CACHE_S = 15.0
"""Inventory and database figures are read at most this often (E.33)."""
SNAPSHOT_CACHE_S = 2.0
"""The collector's snapshot is read at most this often."""
LAG_PERIOD_S = 60.0
_US = 1e6

type Readers = Callable[[], list[Family]]
type Listeners = Callable[[], list[tuple[str, int, int]]]
"""Each listener's name, open connections, and connection limit."""


def process_families(process: str) -> list[Family]:
    """Return the process families of the calling process.

    Args:
        process: ``api`` or ``collector``.

    Returns:
        list[Family]: Start time, CPU, memory, and file descriptors.

    Raises:
        None
    """
    stats = process_stats()
    key = (process,)
    return [
        build("vakr_process_start_time_seconds", [(key, stats.start_s)]),
        build("vakr_process_cpu_seconds_total", [(key, stats.cpu_s)]),
        build("vakr_process_resident_memory_bytes", [(key, stats.rss_bytes)]),
        build("vakr_process_open_fds", [(key, stats.open_fds)]),
    ]


def merge(families: Iterable[Family]) -> list[Family]:
    """Join families of one name, keeping first-seen order.

    Args:
        families: Families, possibly repeating a name.

    Returns:
        list[Family]: One family per name.

    Raises:
        None
    """
    out: dict[str, Family] = {}
    for f in families:
        seen = out.get(f.name)
        out[f.name] = f if seen is None else Family(f.name, f.kind, f.help,
                                                    (*seen.samples, *f.samples))  # fmt: skip
    return list(out.values())


@dataclass(slots=True)
class MetricsService:
    """The API's metrics: its own, and the readers of everything else.

    Attributes:
        snapshot_path: The collector's snapshot file; ``None`` on a node without one.
        readers: Inventory and database readers (blocking), cached together.
        requests: Request counts and durations, fed by the middleware.
        lag: The API's event-loop lag monitor, once the server runs.
        listeners: Reports open connections and limits, once the server runs.
        version: The release in ``vakr_build_info``.
    """

    snapshot_path: Path | None = None
    readers: tuple[Readers, ...] = ()
    requests: RequestStats = field(default_factory=RequestStats)
    lag: LoopLagMonitor | None = None
    listeners: Listeners | None = None
    version: str = field(default_factory=get_version)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _state: tuple[float, list[Family]] = (float("-inf"), [])
    _snapshot: tuple[float, SnapshotRead] = (float("-inf"), SnapshotRead("missing"))
    _lag_prev_us: int | None = None
    _lag_rolled: float = field(default_factory=time.monotonic)

    def live(self) -> list[Family]:
        """Read what the event loop owns (call on the loop).

        Args:
            None

        Returns:
            list[Family]: Requests, event-loop lag, and connections.

        Raises:
            None
        """
        families = self.requests.families()
        if self.lag is not None:
            if time.monotonic() - self._lag_rolled >= LAG_PERIOD_S:
                self._lag_prev_us, self._lag_rolled = self.lag.take_period_max(), time.monotonic()
            peaks = [v for v in (self._lag_prev_us, self.lag.peek_period_max()) if v is not None]
            lag = max(peaks) / _US if peaks else None
            families.append(build("vakr_event_loop_lag_seconds", [(("api",), lag)]))
        if self.listeners is not None:
            rows = self.listeners()
            families += [
                build("vakr_api_connections", (((n,), c) for n, c, _ in rows)),
                build("vakr_api_connection_limit", (((n,), m) for n, _, m in rows)),
            ]
        return families

    def _state_families(self) -> list[Family]:
        """Return the inventory and database families, read at most once per ``STATE_CACHE_S``.

        Args:
            None

        Returns:
            list[Family]: The readers' families.

        Raises:
            None
        """
        with self._lock:  # one refresh at a time: concurrent scrapes share it
            if time.monotonic() >= self._state[0]:
                found = [f for read in self.readers for f in read()]
                self._state = (time.monotonic() + STATE_CACHE_S, found)
            return self._state[1]

    def _collector(self, now_s: float) -> list[Family]:
        """Return the collector's families, or why there are none.

        Args:
            now_s: Current Unix seconds.

        Returns:
            list[Family]: ``up``, its age when readable, and the snapshot's families.

        Raises:
            None
        """
        with self._lock:
            if time.monotonic() >= self._snapshot[0]:
                fresh = read_snapshot(self.snapshot_path, now_s)
                self._snapshot = (time.monotonic() + SNAPSHOT_CACHE_S, fresh)
            read = self._snapshot[1]
        up = build("vakr_collector_metrics_up", [((read.reason,), 1 if read.reason == "ok" else 0)])
        age = build("vakr_collector_metrics_age_seconds", [((), read.age_s)])
        return [up, age, *read.families]

    def render(self, live: list[Family], now_s: float | None = None) -> str:
        """Collect the blocking rest and write the answer (call in a worker thread).

        Args:
            live: From ``live``, read on the event loop.
            now_s: Current Unix seconds; the system clock by default.

        Returns:
            str: The exposition text.

        Raises:
            None
        """
        now_s = time.time() if now_s is None else now_s
        families = [
            build("vakr_build_info", [((self.version,), 1)]),
            *process_families("api"),
            *live,
            *self._collector(now_s),
            *self._state_families(),
        ]
        return render(merge(families))
