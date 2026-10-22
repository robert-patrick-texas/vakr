"""Read what one node knows, inside its container (MPOS-T01, MPOS-T02, MPOS-T04).

The harness runs this with the package's own interpreter,
``/opt/vakr/venv/bin/python /mpos/probe.py WHAT``, so it needs nothing the
package does not ship. Each command prints tab-separated lines for the
shell to compare:

- ``status``: per device, ``name bucket label positions``, where
  ``positions`` is ``position=label`` pairs joined by ``;``
  (``vakr status --json``);
- ``latency NAME``: per position in the device's last hour of latency
  history, ``position collector median_ms points`` (the API over the
  socket). Medians are of the probe medians, so each position's own
  series is summarised alone (MPOS-008);
- ``rows``: per observation family and collector, ``family collector rows
  distinct``, over every partition and the staging tables of
  ``telemetry.db`` in one read transaction (MPOS-T02's counts and
  duplicate UUIDs).

Dependencies:
    json, sqlite3, statistics, subprocess, uuid: Standard library.
    vakr.client.transport: ``get`` over ``/run/vakr/api.sock``.

Safety:
    Read-only: the telemetry database is opened with ``mode=ro``, and the
    API calls are GETs. Nothing printed carries a credential.

Example:
    $ /opt/vakr/venv/bin/python /mpos/probe.py status
"""

import json
import sqlite3
import statistics
import subprocess
import sys
import uuid
from collections.abc import Sequence
from typing import Any

from vakr.client.transport import get

SOCKET = "/run/vakr/api.sock"
TELEMETRY = "/var/lib/vakr/telemetry.db"
FAMILIES = ("obs_icmp", "obs_poll", "obs_snmp", "obs_iface")


def status_rows() -> list[dict[str, Any]]:
    """Return the device rows of ``vakr status --json``.

    Args:
        None

    Returns:
        list[dict[str, Any]]: One object per device, as JSON parsed it.

    Raises:
        ValueError: If the output is not the status document.
    """
    argv = ["/usr/bin/vakr", "status", "--json"]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    return json.loads(done.stdout)["devices"]


def print_status() -> int:
    """Print one line per device: name, bucket, label, and each position's label.

    Args:
        None

    Returns:
        int: 0.

    Raises:
        ValueError: As ``status_rows``.
    """
    for row in status_rows():
        status = row["status"]
        positions = ";".join(f"{p['position']}={p['status']['label']}" for p in row["positions"])
        print(f"{row['name']}\t{status['bucket']}\t{status['label']}\t{positions}")
    return 0


def print_latency(name: str) -> int:
    """Print each position's median round-trip time for one device over the last hour.

    Args:
        name: Device display name.

    Returns:
        int: 0, or 1 when the device or its history cannot be read.

    Raises:
        None
    """
    ids = [r["device_id"] for r in status_rows() if r["name"] == name]
    if not ids:
        print(f"probe: no device {name}", file=sys.stderr)
        return 1
    answer = get(SOCKET, f"/api/v1/devices/{ids[0]}/history/reachability?range=1h")
    if answer.status != 200:
        print(f"probe: history answered HTTP {answer.status}", file=sys.stderr)
        return 1
    for position in json.loads(answer.body)["positions"]:
        medians = [p["median_us"] for p in position["probes"] if p["median_us"] is not None]
        ms = f"{statistics.median(medians) / 1000:.1f}" if medians else "none"
        print(f"{position['position']}\t{position['collector_id']}\t{ms}\t{len(medians)}")
    return 0


def family_counts(db: sqlite3.Connection, family: str) -> list[tuple[int, int, int]]:
    """Count rows and distinct observation UUIDs per collector in one family.

    Args:
        db: The read-only connection, inside a transaction.
        family: Such as ``obs_poll``.

    Returns:
        list[tuple[int, int, int]]: Collector key, rows, and distinct UUIDs.

    Raises:
        sqlite3.Error: If a table cannot be read.
    """
    found = db.execute("SELECT table_name FROM partition_catalog WHERE family = ?", (family,))
    # Table names come from the database's own catalog and FAMILIES, never from input.
    tables = [t for (t,) in found] + [f"{family}_hot"]
    union = " UNION ALL ".join(f"SELECT collector_key, observation_uuid FROM {t}" for t in tables)  # noqa: S608
    sql = "SELECT collector_key, count(*), count(DISTINCT observation_uuid) FROM ("
    return db.execute(sql + union + ") GROUP BY collector_key").fetchall()


def print_rows() -> int:
    """Print row and distinct-UUID counts per family and collector.

    Args:
        None

    Returns:
        int: 0.

    Raises:
        sqlite3.Error: If the database cannot be read.
    """
    db = sqlite3.connect(f"file:{TELEMETRY}?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        keys = dict(db.execute("SELECT collector_key, collector_uuid FROM dict_collector"))
        for family in FAMILIES:
            for key, rows, distinct in family_counts(db, family):
                print(f"{family}\t{uuid.UUID(bytes=keys[key])}\t{rows}\t{distinct}")
    finally:
        db.close()
    return 0


def main(argv: Sequence[str]) -> int:
    """Run one probe command.

    Args:
        argv: ``status``, ``latency NAME``, or ``rows``.

    Returns:
        int: The command's status; 2 for a usage error.

    Raises:
        None
    """
    match list(argv):
        case ["status"]:
            return print_status()
        case ["latency", name]:
            return print_latency(name)
        case ["rows"]:
            return print_rows()
    print("usage: probe.py status | latency NAME | rows", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
