"""The rate-baseline checkpoint file: atomic writes, bounded reads (6.2, RATE-004).

Every minute the collector writes its baselines to
``/var/lib/vakr/collector/<collector UUID>/rate-baselines.json``: a
temporary file is written and fsynced, renamed over the old one, and the
directory is fsynced, so a crash or power cut leaves either the previous
checkpoint or the new one, never half of one. At startup the file is read
back; a missing, oversized, damaged, or foreign file is reported and
ignored, which costs one rate interval per interface and nothing else
(``BASELINE_FIRST_SAMPLE``), never a wrong rate.

Dependencies:
    json, os, pathlib: The file and its atomic replacement.
    vakr.app.collector.baselines: The store and its encoding.

Safety:
    Blocking file I/O: call from a worker thread (``asyncio.to_thread``).
    The file holds counters and timestamps only, mode 0640.

Example:
    >>> from uuid import UUID
    >>> checkpoint_path(Path("/var/lib/vakr"), CollectorId(UUID(int=7))).name
    'rate-baselines.json'
"""

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from vakr.app.collector.baseline_codec import decode_store
from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.timings import MAX_CHECKPOINT_BYTES
from vakr.domain.ids import CollectorId

CHECKPOINT_FILE = "rate-baselines.json"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_LOG = logging.getLogger(__name__)


def boot_id(path: Path = BOOT_ID) -> str:
    """Return this kernel boot's ID, which scopes monotonic time.

    Args:
        path: The kernel's boot-ID file.

    Returns:
        str: The ID, or ``""`` when unknown, which makes restored baselines
        fall back to wall-clock elapsed time.

    Raises:
        None
    """
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def checkpoint_path(state_dir: Path, collector_id: CollectorId) -> Path:
    """Return where one collector's checkpoint lives.

    Args:
        state_dir: ``/var/lib/vakr``.
        collector_id: The collector.

    Returns:
        Path: ``<state>/collector/<collector UUID>/rate-baselines.json``.

    Raises:
        None
    """
    return state_dir / "collector" / str(collector_id) / CHECKPOINT_FILE


def save_checkpoint(data: Mapping[str, Any], path: Path) -> int:
    """Write the checkpoint atomically: temporary file, fsync, rename, directory fsync.

    The caller encodes the store on the event loop (``encode_store``),
    so the snapshot is consistent while this runs in a worker thread.

    Args:
        data: The encoded store.
        path: The checkpoint file.

    Returns:
        int: Bytes written.

    Raises:
        OSError: If the directory or file cannot be written; the previous
            checkpoint is then left in place.
    """
    encoded = json.dumps(data, separators=(",", ":")).encode()
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o640)
    try:
        view = memoryview(encoded)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(path)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return len(encoded)


def load_baselines(path: Path, collector_id: CollectorId) -> BaselineStore:
    """Read the checkpoint, or start empty when it cannot be trusted.

    Args:
        path: The checkpoint file.
        collector_id: This collector.

    Returns:
        BaselineStore: The restored baselines, or an empty store.

    Raises:
        None
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return BaselineStore()
    except OSError as exc:
        _LOG.warning(
            "cannot read the rate-baseline checkpoint (%s); rates restart with the next two polls",
            type(exc).__name__,
            extra={"event_id": "collector.checkpoint_unreadable"},
        )
        return BaselineStore()
    if size > MAX_CHECKPOINT_BYTES:
        _LOG.warning(
            "ignoring a %d-byte rate-baseline checkpoint (limit %d)",
            size,
            MAX_CHECKPOINT_BYTES,
            extra={"event_id": "collector.checkpoint_too_big"},
        )
        return BaselineStore()
    try:
        return decode_store(json.loads(path.read_bytes()), collector_id)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        _LOG.warning(
            "ignoring a damaged rate-baseline checkpoint (%s); rates restart with "
            "the next two polls",
            type(exc).__name__,
            extra={"event_id": "collector.checkpoint_damaged"},
        )
        return BaselineStore()
