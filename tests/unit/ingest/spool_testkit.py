"""Helpers for the spool tests: observations, settings, observable file operations, flaky writers.

The spool's durability depends on ``fsync`` calls and on how writes fail,
so ``CountingOps`` counts every data and directory fsync and can make writes
fail with ``ENOSPC``. ``FlakyWriter`` stands in for a telemetry database
that is down, or that commits a batch and then loses the reply (STORE-009).
``disk_dir`` finds a directory on a real disk: on hosts where ``/tmp`` is
tmpfs, ``fsync`` there proves nothing. The module name is unique on purpose:
test helpers are never imported from a ``conftest``.

Dependencies:
    vakr.ingest, vakr.adapters.memory: The code under test and its storage double.
    tests.contract.contract_samples: Valid observations.

Example:
    >>> len(interface_samples(3))
    3
"""

import asyncio
import errno
import os
import shutil
import uuid
from collections.abc import Coroutine, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.contract import contract_samples as samples
from vakr.adapters.memory import MemoryTelemetryStore
from vakr.adapters.memory.telemetry_store import MemoryObservationWriter
from vakr.domain.errors import StorageError
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.observations import Observation
from vakr.ingest.fileops import FileOps
from vakr.ingest.settings import MIB, SpoolSettings
from vakr.ports.telemetry import WriteResult

COLLECTOR = CollectorId(uuid.UUID("01929b4a-0000-7000-8000-00000000c0c0"))
DEVICE = DeviceId(uuid.UUID("01929b4a-0000-7000-8000-00000000d0d0"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[3]


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run one async scenario to completion.

    Args:
        coro: The scenario.

    Returns:
        T: Its result.

    Raises:
        Exception: Whatever the scenario raises.
    """
    return asyncio.run(coro)


def interface_samples(count: int, *, start: datetime = T0) -> list[Observation]:
    """Build interface observations one second apart on distinct interfaces.

    Args:
        count: How many.
        start: Timestamp of the first.

    Returns:
        list[Observation]: Valid observations with fresh UUIDs.

    Raises:
        None
    """
    return [
        samples.sample(DEVICE, new_id(InterfaceId), COLLECTOR, start + timedelta(seconds=i))
        for i in range(count)
    ]


def mixed_samples(at: datetime = T0) -> list[Observation]:
    """Build one observation of each poll-related type, as one device poll yields.

    Args:
        at: Timestamp.

    Returns:
        list[Observation]: ICMP, SNMP device, interface, and poll records.

    Raises:
        None
    """
    return [
        samples.icmp(DEVICE, COLLECTOR, at),
        samples.snmp(DEVICE, COLLECTOR, at),
        samples.sample(DEVICE, new_id(InterfaceId), COLLECTOR, at),
        samples.poll(DEVICE, COLLECTOR, at),
    ]


def spool_settings(root: Path, **changes: Any) -> SpoolSettings:
    """Return packaged defaults under ``root`` with fast test intervals.

    Args:
        root: Spool root.
        **changes: Fields to override.

    Returns:
        SpoolSettings: Defaults, a 50 ms fsync interval, then ``changes``.

    Raises:
        ValidationError: If the result is invalid.
    """
    base = SpoolSettings.defaults(root).with_values(fsync_interval_s=0.05, ingest_interval_s=1.0)
    return base.with_values(**changes) if changes else base


def small_settings(root: Path, **changes: Any) -> SpoolSettings:
    """Settings with tiny segments and limit, for rotation and capacity tests.

    Args:
        root: Spool root.
        **changes: Fields to override.

    Returns:
        SpoolSettings: 16 KiB segments within a 64 KiB limit, then ``changes``.

    Raises:
        ValidationError: If the result is invalid.
    """
    values: dict[str, Any] = {"segment_bytes": 16 * 1024, "max_bytes": 64 * 1024} | changes
    return spool_settings(root, **values)


class CountingOps(FileOps):
    """``FileOps`` that counts fsyncs and can fail writes like a full disk.

    Attributes:
        fsyncs: File fsyncs, in call order (descriptors).
        dir_fsyncs: Directory fsyncs (paths).
        pass_writes: Writes to let through before ``fail_writes`` applies.
        fail_writes: Writes still to fail; each fails after writing ``partial`` bytes.
        partial: Bytes a failing write writes before failing.
        write_errno: The errno of a failing write.
        fail_fsyncs: Fsyncs still to fail with ``EIO``.
    """

    def __init__(self) -> None:
        """Start with nothing counted and nothing failing.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.fsyncs: list[int] = []
        self.dir_fsyncs: list[Path] = []
        self.pass_writes = 0
        self.fail_writes = 0
        self.partial = 0
        self.write_errno = errno.ENOSPC
        self.fail_fsyncs = 0

    def write(self, fd: int, data: bytes | memoryview) -> int:
        """Write, or fail as configured.

        Args:
            fd: File descriptor.
            data: Bytes.

        Returns:
            int: Bytes written.

        Raises:
            OSError: When a failure is configured.
        """
        if self.pass_writes:
            self.pass_writes -= 1
        elif self.fail_writes:
            self.fail_writes -= 1
            if self.partial:
                os.write(fd, bytes(data[: self.partial]))
            raise OSError(self.write_errno, os.strerror(self.write_errno))
        return super().write(fd, data)

    def fsync(self, fd: int) -> None:
        """Count, then fsync or fail.

        Args:
            fd: File descriptor.

        Returns:
            None

        Raises:
            OSError: When a failure is configured.
        """
        if self.fail_fsyncs:
            self.fail_fsyncs -= 1
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        self.fsyncs.append(fd)
        super().fsync(fd)

    def fsync_dir(self, path: Path) -> None:
        """Count, then fsync the directory.

        Args:
            path: The directory.

        Returns:
            None

        Raises:
            OSError: If it cannot be synced.
        """
        self.dir_fsyncs.append(path)
        super().fsync_dir(path)


class FlakyWriter:
    """An ``ObservationWriter`` over the memory adapter that can fail like a real database.

    Attributes:
        store: The memory store written to.
        failures: Calls still to fail before writing anything.
        commit_then_fail: Calls still to commit and then raise (an uncertain commit).
        calls: Batches received, as (batch ID, size).
    """

    def __init__(self, store: MemoryTelemetryStore | None = None) -> None:
        """Wrap a memory store.

        Args:
            store: The store, or a new one.

        Returns:
            None

        Raises:
            None
        """
        self.store = store or MemoryTelemetryStore()
        self._inner = MemoryObservationWriter(self.store)
        self.failures = 0
        self.commit_then_fail = 0
        self.calls: list[tuple[BatchId, int]] = []

    def write_batch(self, batch_id: BatchId, observations: Sequence[Observation]) -> WriteResult:
        """Write, or fail as configured.

        Args:
            batch_id: The batch.
            observations: Its observations.

        Returns:
            WriteResult: The memory adapter's result.

        Raises:
            StorageError: When a failure is configured.
        """
        self.calls.append((batch_id, len(observations)))
        if self.failures:
            self.failures -= 1
            raise StorageError("telemetry.db is locked", retryable=True)
        result = self._inner.write_batch(batch_id, observations)
        if self.commit_then_fail:
            self.commit_then_fail -= 1
            raise StorageError("connection lost after COMMIT", retryable=True)
        return result


def disk_dir(tmp_path: Path) -> Path:
    """Return an empty directory on a disk-backed filesystem.

    ``tmp_path`` is used when it is on a real disk. When it is on tmpfs,
    where ``fsync`` returns at once, a directory under the repository's
    git-ignored ``.claude/tmp`` is used instead; ``remove_disk_dir`` deletes it.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        Path: The directory.

    Raises:
        OSError: If it cannot be created.
    """
    if _fstype(tmp_path) not in {"tmpfs", "ramfs"}:
        return tmp_path
    path = REPO / ".claude" / "tmp" / f"spool-test-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def remove_disk_dir(path: Path, tmp_path: Path) -> None:
    """Delete a directory made by ``disk_dir`` (``tmp_path`` is left to pytest).

    Args:
        path: The directory.
        tmp_path: pytest's temporary directory.

    Returns:
        None

    Raises:
        None
    """
    if path != tmp_path:
        shutil.rmtree(path, ignore_errors=True)


def _fstype(path: Path) -> str:
    """Return the filesystem type of the mount that holds ``path``.

    Args:
        path: Any existing path.

    Returns:
        str: For example ``ext4`` or ``tmpfs``; ``""`` if unknown.

    Raises:
        None
    """
    best, kind, target = "", "", str(path.resolve())
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        fields = line.split()
        mount, fstype = fields[4], fields[fields.index("-") + 1]
        inside = target == mount or target.startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best):
            best, kind = mount, fstype
    return kind


def spool_files(root: Path) -> list[str]:
    """List file names in a collector spool directory, for assertions.

    Args:
        root: The spool root.

    Returns:
        list[str]: Sorted names of files directly in ``root/<COLLECTOR>``.

    Raises:
        None
    """
    directory = root / str(COLLECTOR)
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def mib(count: int) -> int:
    """Return ``count`` MiB in bytes.

    Args:
        count: MiB.

    Returns:
        int: Bytes.

    Raises:
        None
    """
    return count * MIB
