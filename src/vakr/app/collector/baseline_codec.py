"""The rate-baseline checkpoint's encoding: compact, versioned, validated on read (RATE-004).

A checkpoint is one JSON object: a format name and version, the owning
collector's UUID (a checkpoint of another collector is refused, MPOL-003),
the write time, each device's last ``sysUpTime`` reading, and each
interface's last accepted sample. Entries are validated as they are read,
exactly as a fresh sample would be, so a damaged file can only be refused,
never turned into a wrong rate.

Dependencies:
    vakr.app.collector.baselines: The store.
    vakr.logic.counters: Counter names and ``CounterSample``.

Safety:
    Counters and timestamps only.

Example:
    >>> FORMAT, VERSION
    ('vakr-rate-baselines', 1)
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from vakr.app.collector.baselines import Baseline, BaselineStore
from vakr.collectors.snmp import UptimeReading
from vakr.domain.enums import CounterFamily
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.timestamps import from_epoch_us, to_epoch_us
from vakr.logic.counters import COUNTERS, CounterSample

FORMAT = "vakr-rate-baselines"
VERSION = 1


def encode_store(store: BaselineStore, collector_id: CollectorId, at: datetime) -> dict[str, Any]:
    """Encode the store for the checkpoint file; call it on the event loop.

    Args:
        store: The baselines.
        collector_id: The owning collector, checked on load.
        at: When the checkpoint is written (UTC).

    Returns:
        dict[str, Any]: JSON-ready data.

    Raises:
        None
    """
    uptime = {str(d): [r.ticks, to_epoch_us(r.at)] for d, r in store.uptimes().items()}
    return {
        "format": FORMAT,
        "version": VERSION,
        "collector_id": str(collector_id),
        "written_at_us": to_epoch_us(at),
        "uptime": uptime,
        "interfaces": {str(i): _encode(b) for i, b in store.baselines().items()},
    }


def decode_store(data: Mapping[str, Any], collector_id: CollectorId) -> BaselineStore:
    """Decode a checkpoint written by ``encode_store``.

    Args:
        data: The decoded JSON.
        collector_id: This collector; another collector's baselines are refused.

    Returns:
        BaselineStore: The restored store.

    Raises:
        ValueError: If the data is not a checkpoint of this collector, or
            any entry is malformed (``KeyError`` and ``TypeError`` included).
    """
    if data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ValueError("not a version-1 rate-baseline checkpoint")
    if data.get("collector_id") != str(collector_id):
        raise ValueError("the checkpoint belongs to another collector (MPOL-003)")
    store = BaselineStore()
    for key, (ticks, at_us) in dict(data["uptime"]).items():
        store.set_uptime(DeviceId(UUID(key)), UptimeReading(int(ticks), from_epoch_us(at_us)))
    for key, entry in dict(data["interfaces"]).items():
        store.put(InterfaceId(UUID(key)), _decode(entry))
    return store


def _encode(baseline: Baseline) -> dict[str, Any]:
    """Encode one baseline compactly.

    Args:
        baseline: The baseline.

    Returns:
        dict[str, Any]: Device, ifIndex, times, boot, family, counters, discontinuity.

    Raises:
        None
    """
    s = baseline.sample
    return {
        "d": str(baseline.device_id),
        "i": baseline.if_index,
        "t": to_epoch_us(baseline.sampled_at),
        "m": baseline.sampled_monotonic,
        "b": baseline.boot_id,
        "f": s.family.value,
        "c": [s.value(n) for n in COUNTERS],
        "x": s.discontinuity_ticks,
    }


def _decode(entry: Mapping[str, Any]) -> Baseline:
    """Decode one baseline, validating it as a fresh sample would be.

    Args:
        entry: The encoded baseline.

    Returns:
        Baseline: The baseline.

    Raises:
        ValueError: If a counter or field is invalid (``KeyError``, ``TypeError`` too).
    """
    counters = list(entry["c"])
    if len(counters) != len(COUNTERS) or any(
        c is not None and type(c) is not int for c in counters
    ):
        raise ValueError("a baseline needs six integer-or-null counters")
    values = dict(zip(COUNTERS, counters, strict=True))
    disc = entry["x"]
    sample = CounterSample(
        CounterFamily(entry["f"]), **values, discontinuity_ticks=None if disc is None else int(disc)
    )
    return Baseline(
        DeviceId(UUID(entry["d"])),
        int(entry["i"]),
        sample,
        from_epoch_us(int(entry["t"])),
        float(entry["m"]),
        str(entry["b"]),
    )
