"""Inject the resilience scenarios' storage faults, inside the node (MVP-ACC-06, E.36).

The harness runs this with the package's own interpreter:

- ``lock DB``: make the telemetry store unavailable to writers. It opens
  the SQLite file and holds its write lock (``BEGIN IMMEDIATE``) until
  SIGTERM, as a long backup or a stuck maintenance job would. Every write
  the collector tries then fails after the adapter's 5 s busy timeout
  (E.20) with a ``StorageError``; readers keep working (WAL).
  The harness starts it as a transient unit running as ``vakr``
  (``systemd-run --unit acc06-dblock``) and stops it to end the outage.
- ``powercut SPOOL_DIR``: leave a frozen collector's spool as a power cut
  leaves it when the page cache is lost, which killing a container cannot
  do (E.36 decision 2). The harness stops the collector with SIGSTOP
  first. The newest segment loses its records appended within
  ``spool.fsync_interval`` (1 s, E.8) of its last one that ingestion has
  not acknowledged, the ones no fsync may have covered yet, and gets half
  a frame appended: a torn final write. It prints ``dropped N`` and one
  ``poll DEVICE SCHEDULED_US`` line per poll record removed.

Dependencies:
    json, os, signal, sqlite3: Standard library.
    vakr.ingest: The segment scanner and the consumer offset.

Safety:
    ``lock`` changes no data: its transaction writes nothing and is rolled
    back. ``powercut`` destroys spool records on purpose, on the test node.

Example:
    $ systemd-run --unit acc06-dblock -p User=vakr /opt/vakr/venv/bin/python
        /acc06/faults.py lock /var/lib/vakr/telemetry.db
"""

import json
import os
import signal
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from vakr.ingest.scan import scan_segment
from vakr.ingest.state import load_offset

FSYNC_US = 1_000_000
"""``spool.fsync_interval`` (E.8): records younger than this may not be durable yet."""
TAIL_FRAMES = 5_000


def hold_lock(path: str) -> int:
    """Hold the database's write lock until SIGTERM or SIGINT.

    Args:
        path: The SQLite database file.

    Returns:
        int: 0 after the lock was released.

    Raises:
        sqlite3.Error: If the lock cannot be taken within 60 s.
    """
    db = sqlite3.connect(path, timeout=60.0, isolation_level=None)
    db.execute("BEGIN IMMEDIATE")
    print(f"faults: holding the write lock of {path}", flush=True)

    def release(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001 - handler shape
        """Leave the wait loop.

        Args:
            signum: The signal.
            frame: Unused.

        Returns:
            None

        Raises:
            SystemExit: Always, to end the wait.
        """
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, release)
    signal.signal(signal.SIGINT, release)
    try:
        while True:
            signal.pause()
    finally:
        db.execute("ROLLBACK")
        db.close()
        print("faults: write lock released", flush=True)


def power_cut(directory: Path) -> int:
    """Drop the unacknowledged records of the last fsync interval and tear the last write.

    Args:
        directory: A collector's spool directory, its writer frozen.

    Returns:
        int: 0, or 1 when the spool has no segment with records.

    Raises:
        OSError: If the segment cannot be read or changed.
    """
    segments = sorted(directory.glob("seg-*.spool"))
    found = scan_segment(segments[-1], keep_tail=TAIL_FRAMES) if segments else None
    if found is None or not found.tail:
        return 1
    offset = load_offset(directory)
    acked = 0 if offset is None else offset.seq
    newest = found.tail[-1].header.append_us
    dropped = [f for f in found.tail
               if f.header.seq > acked and f.header.append_us > newest - FSYNC_US]  # fmt: skip
    print(f"dropped {len(dropped)}")
    if not dropped:
        return 0
    first = dropped[0]
    torn = segments[-1].read_bytes()[first.offset : first.end]
    os.truncate(segments[-1], first.offset)
    with segments[-1].open("ab") as segment:
        segment.write(torn[: len(torn) // 2])
        os.fsync(segment.fileno())
    for frame in dropped:
        envelope = json.loads(frame.payload)
        if envelope.get("observation_type") == "device_poll":
            print(f"poll\t{envelope['payload']['device_id']}\t{envelope['timestamp_us']}")
    return 0


def main(argv: Sequence[str]) -> int:
    """Run one fault.

    Args:
        argv: ``lock DB`` or ``powercut SPOOL_DIR``.

    Returns:
        int: The fault's status; 2 for a usage error.

    Raises:
        sqlite3.Error: As ``hold_lock``.
        OSError: As ``power_cut``.
    """
    match list(argv):
        case ["lock", path]:
            return hold_lock(path)
        case ["powercut", directory]:
            return power_cut(Path(directory))
    print("usage: faults.py lock DB | powercut SPOOL_DIR", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
