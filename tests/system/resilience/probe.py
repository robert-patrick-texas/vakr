"""Read what the resilience node knows, inside its container (MVP-ACC-06, E.36).

The harness runs this with the package's own interpreter,
``/opt/vakr/venv/bin/python /acc06/probe.py WHAT``, so it needs nothing the
package does not ship. Each command prints tab-separated lines:

- ``started``: when the current ``vakr-collector.service`` run became
  ready (``READY=1``), in epoch microseconds, from systemd's monotonic
  ``ActiveEnterTimestampMonotonic`` and this process's two clocks;
- ``status``: per device, ``name bucket label positions device_id`` from
  ``vakr status --json``;
- ``polls START_US END_US DEVICE_ID...``: each device asked about
  (``device id``), every stored poll record of those devices scheduled in
  the window (``poll device scheduled_us execution
  started_us completed_us poll_id``), every ICMP observation
  (``icmp poll_id state``), and every collector-health report
  (``health ts_us state``), read through the telemetry port
  (``ObservationQuery.read_raw``);
- ``incidents``: every telemetry-loss incident of every collector spool,
  one JSON object per line (``vakr.ingest.read_incidents``);
- ``spool``: per collector spool, ``durable_seq acked_seq storage_available
  oldest_unacked_age_s taken_us`` from the ingester's ``status.json``;
- ``next-slot DEVICE_ID...``: the next slot of any of those devices at least
  3 s from now, in epoch microseconds, from their stored polls;
- ``api PATH``: ``GET PATH`` on vakr-api's socket, as root (admin): the
  status on the first line, the body after it.

Dependencies:
    json, subprocess, time: Standard library.
    vakr.adapters.sqlite.telemetry: The store and its query port.
    vakr.ingest: The incident reader and the ingester's status file.
    vakr.client.transport: ``get`` over ``/run/vakr/api.sock``.

Safety:
    Read-only: ``read_raw`` and ``read_incidents`` change nothing, and the
    harness runs the telemetry and spool reads as ``vakr``, so no file
    becomes root's. Nothing printed carries a credential.

Example:
    $ runuser -u vakr -- /opt/vakr/venv/bin/python /acc06/probe.py incidents
"""

import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vakr.adapters.sqlite.telemetry import SqliteTelemetry, SqliteTelemetryStore
from vakr.client.transport import get
from vakr.domain.enums import ObservationType
from vakr.domain.ids import DeviceId
from vakr.domain.paging import MAX_PAGE_LIMIT, Page, PageRequest
from vakr.domain.series import TimeWindow
from vakr.ingest import read_incidents
from vakr.ingest.status import read_status

TELEMETRY = Path("/var/lib/vakr/telemetry.db")
SPOOL = Path("/var/lib/vakr/spool")
SOCKET = "/run/vakr/api.sock"
INTERVAL_US = 30_000_000
"""Every simulated device's interval (``simdata.py --interval 30s``)."""


def _us(moment: datetime | None) -> str:
    """Render an optional time as epoch microseconds, ``-`` when absent.

    Args:
        moment: A UTC time, or ``None``.

    Returns:
        str: The microseconds, or ``-``.

    Raises:
        None
    """
    return "-" if moment is None else str(round(moment.timestamp() * 1_000_000))


def print_started() -> int:
    """Print when the running collector became ready, in epoch microseconds.

    Args:
        None

    Returns:
        int: 0, or 1 when the unit has not become active.

    Raises:
        None
    """
    argv = ["systemctl", "show", "-P", "ActiveEnterTimestampMonotonic", "vakr-collector.service"]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    mono = int(done.stdout.strip() or 0)
    if mono <= 0:
        return 1
    # systemd's *Monotonic timestamps are CLOCK_MONOTONIC, the clock time.monotonic_ns reads.
    now_us, now_mono = time.time_ns() // 1000, time.monotonic_ns() // 1000
    print(now_us - (now_mono - mono))
    return 0


def print_status() -> int:
    """Print one line per device: name, bucket, label, positions, and device ID.

    Args:
        None

    Returns:
        int: 0, or 1 when the status could not be read.

    Raises:
        None
    """
    argv = ["/usr/bin/vakr", "status", "--json"]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    try:
        rows = json.loads(done.stdout)["devices"]
    except ValueError, KeyError:
        print(f"probe: vakr status exited {done.returncode}", file=sys.stderr)
        return 1
    for row in rows:
        status = row["status"]
        positions = ";".join(f"{p['position']}={p['status']['label']}" for p in row["positions"])
        print(f"{row['name']}\t{status['bucket']}\t{status['label']}\t{positions}\t"
              f"{row['device_id']}")  # fmt: skip
    return 0


def every(fetch: Callable[[PageRequest], Page[Any]]) -> Iterator[Any]:
    """Yield every item of a paged read.

    Args:
        fetch: Returns the page for a request.

    Returns:
        Iterator[Any]: The items, oldest first.

    Raises:
        StorageError: If the store cannot be read.
    """
    cursor: str | None = None
    while True:
        page = fetch(PageRequest(limit=MAX_PAGE_LIMIT, cursor=cursor))
        yield from page.items
        if (cursor := page.next_cursor) is None:
            return


def print_polls(start_us: int, end_us: int, devices: Sequence[str]) -> int:
    """Print the poll records, ICMP results, and health reports stored in a window.

    Args:
        start_us: Window start, epoch microseconds.
        end_us: Window end (exclusive), epoch microseconds.
        devices: Device UUIDs.

    Returns:
        int: 0.

    Raises:
        StorageError: If the store cannot be read.
    """
    window = TimeWindow(datetime.fromtimestamp(start_us / 1e6, UTC),
                        datetime.fromtimestamp(end_us / 1e6, UTC))  # fmt: skip
    store = SqliteTelemetryStore(TELEMETRY)
    try:
        query = SqliteTelemetry(store).query
        collectors: set[uuid.UUID] = set()
        for text in devices:
            print(f"device\t{text}")
            device = DeviceId(uuid.UUID(text))
            for kind in (ObservationType.DEVICE_POLL, ObservationType.ICMP):
                for o in every(lambda p, k=kind, d=device: query.read_raw(k, d, window, p)):
                    collectors.add(o.collector_id)
                    print(_poll_line(o, text) if kind is ObservationType.DEVICE_POLL
                          else f"icmp\t{o.poll_id}\t{o.state.value}")  # fmt: skip
        for collector in sorted(collectors):
            health = every(lambda p, c=collector: query.read_raw(
                ObservationType.COLLECTOR_HEALTH, c, window, p))  # fmt: skip
            for o in health:
                print(f"health\t{_us(o.timestamp)}\t{o.state.value}")
    finally:
        store.close()
    return 0


def _poll_line(poll: Any, device: str) -> str:
    """Render one poll record as a ``poll`` line.

    Args:
        poll: A ``DevicePoll``.
        device: Its device UUID, as text.

    Returns:
        str: The tab-separated line.

    Raises:
        None
    """
    return (f"poll\t{device}\t{_us(poll.scheduled_at)}\t{poll.execution.value}\t"
            f"{_us(poll.started_at)}\t{_us(poll.completed_at)}\t{poll.poll_id}")  # fmt: skip


def print_incidents() -> int:
    """Print every telemetry-loss incident of every collector spool as JSON lines.

    Args:
        None

    Returns:
        int: 0.

    Raises:
        None
    """
    for directory in sorted(d for d in SPOOL.iterdir() if d.is_dir()):
        for incident in read_incidents(directory):
            print(json.dumps(incident.to_dict(), sort_keys=True))
    return 0


def print_spool() -> int:
    """Print each collector spool's ingestion position from its ``status.json``.

    Args:
        None

    Returns:
        int: 0, or 1 when no spool has written a status yet.

    Raises:
        None
    """
    found = 0
    for directory in sorted(d for d in SPOOL.iterdir() if d.is_dir()):
        if (h := read_status(directory)) is not None:
            found += 1
            print(f"{h.durable_seq}\t{h.acked_seq}\t{h.storage_available}\t"
                  f"{h.oldest_unacked_age_s}\t{h.taken_us}")  # fmt: skip
    return 0 if found else 1


def print_next_slot(devices: Sequence[str]) -> int:
    """Print the next slot of any listed device that is at least 3 s away.

    Args:
        devices: Device UUIDs.

    Returns:
        int: 0, or 1 when none of them has a stored poll in the last 5 minutes.

    Raises:
        StorageError: If the store cannot be read.
    """
    now = time.time_ns() // 1000
    window = TimeWindow(datetime.fromtimestamp(now / 1e6 - 300, UTC),
                        datetime.fromtimestamp(now / 1e6 + 1, UTC))  # fmt: skip
    store = SqliteTelemetryStore(TELEMETRY)
    try:
        query = SqliteTelemetry(store).query
        slots: list[str] = []
        for text in devices:
            device = DeviceId(uuid.UUID(text))
            found = every(lambda p, d=device: query.read_raw(ObservationType.DEVICE_POLL, d,
                                                              window, p))  # fmt: skip
            slots.extend(_us(o.scheduled_at) for o in found)
    finally:
        store.close()
    if not slots:
        return 1
    soon = now + 3_000_000
    print(min(int(s) + -((int(s) - soon) // INTERVAL_US) * INTERVAL_US for s in slots))
    return 0


def print_api(path: str) -> int:
    """Print the status and body of one ``GET`` on vakr-api's socket.

    Args:
        path: Path and query, such as ``/api/v1/overview``.

    Returns:
        int: 0 for a 2xx answer, else 1.

    Raises:
        SourceUnavailableError: If the socket cannot be reached.
    """
    answer = get(SOCKET, path)
    print(answer.status)
    print(answer.body.decode("utf-8", "replace"))
    return 0 if 200 <= answer.status < 300 else 1


def main(argv: Sequence[str]) -> int:
    """Run one probe command.

    Args:
        argv: ``started``, ``status``, ``polls START END DEVICE...``, ``incidents``,
            ``spool``, ``next-slot DEVICE...``, or ``api PATH``.

    Returns:
        int: The command's status; 2 for a usage error.

    Raises:
        None
    """
    simple: dict[str, Callable[[], int]] = {
        "started": print_started, "status": print_status, "incidents": print_incidents,
        "spool": print_spool,
    }  # fmt: skip
    match list(argv):
        case [name] if name in simple:
            return simple[name]()
        case ["polls", start, end, *devices]:
            return print_polls(int(start), int(end), devices)
        case ["next-slot", *devices]:
            return print_next_slot(devices)
        case ["api", path]:
            return print_api(path)
    print("usage: probe.py started | status | polls START END DEVICE... | incidents | spool | "
          "next-slot DEVICE... | api PATH", file=sys.stderr)  # fmt: skip
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
