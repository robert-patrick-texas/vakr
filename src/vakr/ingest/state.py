"""Small durable state files: the writer's run marker and the consumer offset (E.8, STORE-010).

Two facts must survive a crash. The *run marker* (``writer.state``) says
whether the last writer stopped cleanly; if it did not, observations
completed in its final fsync interval may be gone, and recovery records a
loss incident (STORE-007). The *consumer offset* (``consumer.offset``) is
the last sequence number whose whole batch the telemetry database accepted;
replay resumes after it, and records before it may be deleted.

Both are tiny JSON files replaced atomically: write a temporary file, fsync
it, rename it over the old one, fsync the directory. A reader therefore
sees either the old or the new content, never a mix.

Dependencies:
    json, os: Encoding and the atomic replace.
    vakr.ingest.segments: ``FileOps`` for fsync.

Safety:
    The files hold sequence numbers, UUIDs, and times only.

Example:
    >>> ConsumerOffset(41, None, 0).seq
    41
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from vakr.ingest.fileops import FileOps, write_all

RUN_STATE_FILE = "writer.state"
OFFSET_FILE = "consumer.offset"
STATUS_FILE = "status.json"
_FORMAT = 1
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunState:
    """The writer's run marker. Immutable.

    Attributes:
        clean: ``True`` once the writer closed cleanly; ``False`` while running.
        last_seq: Last sequence number written before a clean close, else ``None``.
        updated_us: When the marker was written (epoch microseconds, UTC).
    """

    clean: bool
    last_seq: int | None
    updated_us: int


@dataclass(frozen=True, slots=True)
class ConsumerOffset:
    """How far ingestion has durably acknowledged (whole batches, E.8). Immutable.

    Attributes:
        seq: Last acknowledged sequence number; ``0`` before any.
        batch_id: Batch UUID of that record, or ``None``.
        acked_us: When it was acknowledged (epoch microseconds, UTC).
    """

    seq: int
    batch_id: UUID | None
    acked_us: int


def write_json_atomic(path: Path, data: dict[str, Any], ops: FileOps, *, durable: bool) -> None:
    """Replace a small JSON file atomically.

    Args:
        path: Destination.
        data: JSON-compatible content.
        ops: File operations.
        durable: Fsync the file and directory; advisory files skip it.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written or renamed.
    """
    temp = path.with_name(f".{path.name}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        write_all(fd, json.dumps(data, sort_keys=True).encode(), ops)
        if durable:
            ops.fsync(fd)
    finally:
        os.close(fd)
    temp.replace(path)
    if durable:
        ops.fsync_dir(path.parent)


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a small JSON object, treating any damage as absence.

    Args:
        path: The file.

    Returns:
        dict[str, Any] | None: The object, or ``None`` if missing or unreadable.

    Raises:
        None
    """
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        _LOG.warning("ignoring unreadable spool state file %s (%s)", path, type(exc).__name__,
                     extra={"event_id": "spool.state_unreadable"})  # fmt: skip
        return None
    return data if isinstance(data, dict) else None


def load_run_state(directory: Path) -> RunState | None:
    """Read the run marker.

    Args:
        directory: The collector's spool directory.

    Returns:
        RunState | None: The marker, or ``None`` when absent. An unreadable
        marker reads as an unclean stop, the safe assumption.

    Raises:
        None
    """
    path = directory / RUN_STATE_FILE
    data = read_json(path)
    if data is None:
        return RunState(False, None, 0) if path.exists() else None
    last = data.get("last_seq")
    return RunState(
        clean=data.get("clean") is True,
        last_seq=last if isinstance(last, int) else None,
        updated_us=int(data.get("updated_us", 0)),
    )


def save_run_state(directory: Path, state: RunState, ops: FileOps) -> None:
    """Write the run marker durably.

    Args:
        directory: The collector's spool directory.
        state: The marker.
        ops: File operations.

    Returns:
        None

    Raises:
        OSError: If it cannot be written.
    """
    data = {"format": _FORMAT, "clean": state.clean, "last_seq": state.last_seq,
            "updated_us": state.updated_us}  # fmt: skip
    write_json_atomic(directory / RUN_STATE_FILE, data, ops, durable=True)


def load_offset(directory: Path) -> ConsumerOffset | None:
    """Read the consumer offset.

    Args:
        directory: The collector's spool directory.

    Returns:
        ConsumerOffset | None: The offset, or ``None`` when absent or
        unreadable; replay then starts at the oldest record, which is safe
        because writes are idempotent (STORE-009).

    Raises:
        None
    """
    data = read_json(directory / OFFSET_FILE)
    if data is None or not isinstance(data.get("seq"), int) or data["seq"] < 0:
        return None
    batch = data.get("batch_id")
    try:
        batch_id = UUID(batch) if isinstance(batch, str) else None
    except ValueError:
        batch_id = None
    return ConsumerOffset(data["seq"], batch_id, int(data.get("acked_us", 0)))


def save_offset(directory: Path, offset: ConsumerOffset, ops: FileOps) -> None:
    """Write the consumer offset durably; only then may covered segments be deleted.

    Args:
        directory: The collector's spool directory.
        offset: The new offset.
        ops: File operations.

    Returns:
        None

    Raises:
        OSError: If it cannot be written.
    """
    batch = None if offset.batch_id is None else str(offset.batch_id)
    data = {"format": _FORMAT, "seq": offset.seq, "batch_id": batch, "acked_us": offset.acked_us}
    write_json_atomic(directory / OFFSET_FILE, data, ops, durable=True)
