"""The collector's metrics snapshot file: written atomically, read strictly (OBS-001, E.33).

The collector has no HTTP listener. Every ``WRITE_INTERVAL_S`` it writes
its families to ``/run/vakr/collector-metrics.json`` (tmpfs, mode 0640,
``vakr:vakr``): a temporary file in the same directory, then a rename, so a
reader never sees half a file. ``vakr-api`` reads it at each scrape.

A snapshot is used only when every catalogued family in it is valid:
known label names and fixed values, finite numbers, non-negative
counters. Otherwise the API answers no collector series at all, with
``vakr_collector_metrics_up{reason=...} 0``, because a partly trusted
file could put a wrong figure where an absent one belongs (invariant 1):

- ``missing``: no file (the collector is not running, or never wrote);
- ``stale``: older than ``STALE_AFTER_S`` (the collector hangs or stopped
  without removing it);
- ``too_large``: bigger than ``MAX_SNAPSHOT_BYTES``;
- ``unreadable``: the file cannot be read;
- ``corrupt``: not JSON, the wrong shape, a refused label or value, or a
  time more than a minute in the future.

Families the catalog does not know are skipped, so a collector one release
ahead of the API (during an update) still shows what both know.

Dependencies:
    json, os, pathlib: The file.
    vakr.metrics.catalog, exposition: The checks and the model.

Safety:
    The file carries counts, sizes, and timings with fixed labels only. It
    is written without following symbolic links.

Example:
    >>> read_snapshot(None, 0.0).reason
    'missing'
"""

import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from vakr.metrics.catalog import CATALOG, COLLECTOR_NAMES, check
from vakr.metrics.exposition import Family, Sample

SNAPSHOT_FILE = "collector-metrics.json"
WRITE_INTERVAL_S = 15.0
"""How often the collector writes its snapshot (E.33)."""
STALE_AFTER_S = 3 * WRITE_INTERVAL_S
"""A snapshot older than this is the collector not reporting, never current figures."""
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SNAPSHOT_SAMPLES = 2000
_FUTURE_S = 60.0
_FORMAT = 1
_PROCESS_FAMILIES = frozenset(
    {"vakr_process_start_time_seconds", "vakr_process_cpu_seconds_total",
     "vakr_process_resident_memory_bytes", "vakr_process_open_fds", "vakr_event_loop_lag_seconds"}
)  # fmt: skip
SNAPSHOT_NAMES = COLLECTOR_NAMES | _PROCESS_FAMILIES


class SnapshotTooLargeError(ValueError):
    """The encoded snapshot is over ``MAX_SNAPSHOT_BYTES``; it is not written."""


@dataclass(frozen=True, slots=True)
class SnapshotRead:
    """What one read of the snapshot found.

    Attributes:
        reason: ``ok`` or why the collector's figures are left out.
        age_s: The snapshot's age, when it could be read.
        families: The collector's families; empty unless ``reason`` is ``ok``.
    """

    reason: str
    age_s: float | None = None
    families: tuple[Family, ...] = ()


def encode_snapshot(families: Iterable[Family], written_at: float) -> bytes:
    """Encode families for the file.

    Args:
        families: The collector's families.
        written_at: Unix seconds.

    Returns:
        bytes: Compact JSON.

    Raises:
        SnapshotTooLargeError: If it is over ``MAX_SNAPSHOT_BYTES``.
    """
    doc = {"format": _FORMAT, "written_at": written_at, "families": [
        {"name": f.name, "samples": [[[list(p) for p in s.labels], s.value, s.suffix]
                                     for s in f.samples]}
        for f in families if f.samples
    ]}  # fmt: skip
    data = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLargeError(f"the snapshot is {len(data)} bytes")
    return data


def write_snapshot(path: Path, data: bytes) -> None:
    """Replace the snapshot atomically, mode 0640 (blocking; a worker thread).

    Args:
        path: The snapshot file.
        data: From ``encode_snapshot``.

    Returns:
        None

    Raises:
        OSError: If the directory refuses the write.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)  # left by a crash of this very process ID
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        os.fchmod(fd, 0o640)  # the umask must not decide who may read it
        os.write(fd, data)
    finally:
        os.close(fd)
    tmp.replace(path)


def _finite(value: object) -> bool:
    """Tell whether a JSON value is a finite number (and not a boolean).

    Args:
        value: The value.

    Returns:
        bool: True for a finite int or float.

    Raises:
        None
    """
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)


def _sample(raw: object) -> Sample | None:
    """Decode one sample, or ``None`` when its shape is wrong.

    Args:
        raw: ``[[[name, value], ...], value, suffix]``.

    Returns:
        Sample | None: The sample.

    Raises:
        None
    """
    if not (isinstance(raw, list) and len(raw) == 3 and isinstance(raw[0], list)):
        return None
    labels, value, suffix = raw
    pairs = tuple(
        (p[0], p[1])
        for p in labels
        if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p)
    )
    if len(pairs) != len(labels) or not _finite(value) or not isinstance(suffix, str):
        return None
    try:
        return Sample(pairs, float(value), suffix)
    except ValueError:
        return None


def _family(raw: object) -> Family | bool | None:
    """Decode one family: the family, ``True`` to skip an unknown one, ``None`` if invalid.

    Args:
        raw: ``{"name": ..., "samples": [...]}``.

    Returns:
        Family | None | bool: As described.

    Raises:
        None
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        return None
    spec = CATALOG.get(raw["name"])
    if spec is None or spec.name not in SNAPSHOT_NAMES:
        return True
    if not isinstance(raw.get("samples"), list):
        return None
    samples: list[Sample] = []
    for item in raw["samples"]:
        s = _sample(item)
        if s is None or not check(spec, s):
            return None
        if spec.name in _PROCESS_FAMILIES and dict(s.labels).get("process") != "collector":
            return None
        samples.append(s)
    return Family(spec.name, spec.kind, spec.help, tuple(samples))


def _families(doc: object) -> tuple[Family, ...] | None:
    """Decode every family, or ``None`` when anything known is invalid.

    Args:
        doc: The parsed file.

    Returns:
        tuple[Family, ...] | None: The families.

    Raises:
        None
    """
    raw = doc.get("families") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        return None
    out: list[Family] = []
    for item in raw:
        found = _family(item)
        if found is None:
            return None
        if isinstance(found, Family):
            out.append(found)
    if sum(len(f.samples) for f in out) > MAX_SNAPSHOT_SAMPLES:
        return None
    return tuple(out)


def _load(path: Path | None) -> dict[str, object] | SnapshotRead:
    """Read the file's JSON object within its size bound.

    Args:
        path: The snapshot file, or ``None``.

    Returns:
        dict | SnapshotRead: The parsed object, or why there is none.

    Raises:
        None
    """
    if path is None:
        return SnapshotRead("missing")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SNAPSHOT_BYTES + 1)
    except FileNotFoundError:
        return SnapshotRead("missing")
    except OSError:
        return SnapshotRead("unreadable")
    if len(data) > MAX_SNAPSHOT_BYTES:
        return SnapshotRead("too_large")
    try:
        doc = json.loads(data)
    except ValueError, RecursionError:
        return SnapshotRead("corrupt")
    return doc if isinstance(doc, dict) else SnapshotRead("corrupt")


def read_snapshot(path: Path | None, now_s: float) -> SnapshotRead:
    """Read and check the snapshot (blocking).

    Args:
        path: The snapshot file; ``None`` when this node has none configured.
        now_s: Current Unix seconds.

    Returns:
        SnapshotRead: The families, or why there are none.

    Raises:
        None
    """
    loaded = _load(path)
    if isinstance(loaded, SnapshotRead):
        return loaded
    written = loaded.get("written_at")
    age = now_s - written if isinstance(written, int | float) and _finite(written) else None
    if loaded.get("format") != _FORMAT or age is None or age < -_FUTURE_S:
        return SnapshotRead("corrupt")  # a time far ahead means the clocks disagree, not "fresh"
    age = max(age, 0.0)
    doc = loaded
    if age > STALE_AFTER_S:
        return SnapshotRead("stale", age)
    families = _families(doc)
    if families is None:
        return SnapshotRead("corrupt", age)
    return SnapshotRead("ok", age, families)
