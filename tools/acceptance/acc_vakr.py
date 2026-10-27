# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Read the application's comparison export from extracted bundles.

``vakr acceptance export`` (through the field kit's ``acceptance``
profile) leaves these members under ``acceptance/``: ``export.json`` (the
host, the collector or position, the window), ``devices.csv`` and
``interfaces.csv`` (what the short keys stand for), ``rates/<device>.csv``
(one row per interface sample: end time, elapsed seconds, in/out bits per
second, the ``ifIndex`` it was read from, counter width, quality flags)
and ``icmp/<device>.csv`` (one row per ICMP cycle with every probe's RTT
in microseconds, ``-1`` for no reply). Bundles from consecutive days
overlap; rows are merged by interface or device identity and time, so
each sample counts once.

Dependencies:
    csv: Standard library only.

Safety:
    Reads files under one bundle directory; an empty rate cell is a
    missing value (``None``), never zero.

Example:
    >>> sample_of({"end": "120.000", "elapsed_s": "60.000", "in_bps": "8", "out_bps": "",
    ...            "if_index": "2", "counter_bits": "64", "flags": ""}).start
    60.0
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Sample:
    """One interface sample.

    Attributes:
        start: Interval start (end minus elapsed), or ``end`` without a baseline.
        end: Sample time, epoch seconds.
        in_bps: Inbound bits per second, or ``None``.
        out_bps: Outbound bits per second, or ``None``.
        if_index: ``ifIndex`` the counters were read from.
        counter_bits: 64, 32, or 0 when unknown.
        flags: Quality flag names.
    """

    start: float
    end: float
    in_bps: float | None
    out_bps: float | None
    if_index: int
    counter_bits: int
    flags: frozenset[str]


@dataclass(frozen=True, slots=True)
class Cycle:
    """One ICMP cycle.

    Attributes:
        start: Cycle start, epoch seconds.
        rtts_ms: Probe RTTs in milliseconds, ``None`` for no reply.
    """

    start: float
    rtts_ms: tuple[float | None, ...]


@dataclass
class Device:
    """One device across bundles.

    Attributes:
        device_id: Persistent identity.
        name: Name as exported (pseudonymized in pseudonymized bundles).
        aliases: Every alias, lower-cased.
        cycles: ICMP cycles by start time.
    """

    device_id: str
    name: str
    aliases: set[str] = field(default_factory=set)
    cycles: dict[float, Cycle] = field(default_factory=dict)


@dataclass
class Interface:
    """One interface across bundles.

    Attributes:
        interface_id: Persistent identity (never ``ifIndex``).
        device_id: Its device.
        if_index: ``ifIndex`` now, if known.
        if_name: ``ifName``.
        if_descr: ``ifDescr``.
        mac: MAC address text.
        addresses: Interface addresses without prefix length.
        samples: Samples by end time.
    """

    interface_id: str
    device_id: str
    if_index: int | None
    if_name: str
    if_descr: str
    mac: str
    addresses: set[str]
    samples: dict[float, Sample] = field(default_factory=dict)


@dataclass
class Export:
    """Everything the application exported, merged across bundles.

    Attributes:
        hosts: ``(host, collector name, position)`` of each export.
        devices: Devices by identity.
        interfaces: Interfaces by identity.
        truncated: True when any export hit its row budget.
    """

    hosts: set[tuple[str, str, str]] = field(default_factory=set)
    devices: dict[str, Device] = field(default_factory=dict)
    interfaces: dict[str, Interface] = field(default_factory=dict)
    truncated: bool = False


def read_table(path: Path) -> list[dict[str, str]]:
    """Read a CSV member with a header.

    Args:
        path: The file.

    Returns:
        list[dict[str, str]]: Rows; empty when the file is missing.

    Raises:
        None
    """
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _float(text: str) -> float | None:
    """Parse an optional number.

    Args:
        text: Cell text.

    Returns:
        float | None: The number, or ``None`` for an empty cell.

    Raises:
        None
    """
    return float(text) if text else None


def sample_of(row: dict[str, str]) -> Sample:
    """Build a sample from a ``rates`` row.

    Args:
        row: The row.

    Returns:
        Sample: The sample.

    Raises:
        ValueError: If a number is malformed.
    """
    end = float(row["end"])
    elapsed = _float(row.get("elapsed_s", ""))
    flags = frozenset(f for f in row.get("flags", "").split("|") if f)
    bits = int(row["counter_bits"]) if row.get("counter_bits") else 0
    return Sample(end - (elapsed or 0.0), end, _float(row["in_bps"]), _float(row["out_bps"]),
                  int(row["if_index"]), bits, flags)  # fmt: skip


def _cycle(row: dict[str, str]) -> Cycle:
    """Build a cycle from an ``icmp`` row.

    Args:
        row: The row.

    Returns:
        Cycle: The cycle, RTTs in milliseconds.

    Raises:
        ValueError: If a number is malformed.
    """
    rtts = tuple(None if int(r) < 0 else int(r) / 1000 for r in row["rtts_us"].split())
    return Cycle(float(row["start"]), rtts)


def read_export(root: Path, export: Export) -> None:
    """Merge one bundle's ``acceptance/`` members into ``export``.

    Args:
        root: The bundle's ``acceptance`` directory.
        export: The merged export.

    Returns:
        None

    Raises:
        ValueError: If a member is malformed.
    """
    about = json.loads((root / "export.json").read_text(encoding="utf-8"))
    collector = about.get("collector", {})
    export.hosts.add((about.get("host", ""), collector.get("name", ""),
                      collector.get("position", "")))  # fmt: skip
    export.truncated |= bool(about.get("truncated"))
    keys: dict[str, str] = {}
    for row in read_table(root / "devices.csv"):
        device = export.devices.setdefault(
            row["device_id"], Device(row["device_id"], row["device"])
        )
        device.aliases |= {a.lower() for a in row["aliases"].split()}
        keys[row["key"]] = row["device_id"]
    ifaces: dict[str, Interface] = {}
    for row in read_table(root / "interfaces.csv"):
        index = int(row["if_index"]) if row["if_index"] else None
        addresses = {a.split("/")[0].lower() for a in row["addresses"].split()}
        ifaces[row["key"]] = export.interfaces.setdefault(row["interface_id"], Interface(
            row["interface_id"], keys.get(row["device_key"], ""), index, row["if_name"],
            row["if_descr"], row["mac"], addresses))  # fmt: skip
    for path in sorted((root / "rates").glob("*.csv")):
        for row in read_table(path):
            if row["key"] in ifaces:
                sample = sample_of(row)
                ifaces[row["key"]].samples[sample.end] = sample
    for path in sorted((root / "icmp").glob("*.csv")):
        for row in read_table(path):
            device_id = keys.get(row["key"])
            if device_id in export.devices:
                cycle = _cycle(row)
                export.devices[device_id].cycles[cycle.start] = cycle
