"""SPOOL-T06, SPOOL-T08: kill a real collector process mid-append; acknowledged records survive.

A child process appends batches in a loop and prints each receipt's last
sequence number, which it receives only after the covering fsync. The
parent kills it, either with SIGKILL from outside or with ``os._exit``
inside the fsync that would have covered the next group, exactly where a
crash hurts most. The parent then reopens the spool as the next service
start would and checks that every acknowledged record is readable, that
at most the un-fsynced tail is missing, and that the stop was recorded as
a telemetry-loss incident (STORE-007). A third test also throws away the
page cache's un-fsynced bytes, as a power cut would, by truncating the
segment to its last fsynced size and leaving a torn record behind.

Dependencies:
    subprocess, signal; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_crash.py
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    REPO,
    disk_dir,
    remove_disk_dir,
    run,
    spool_settings,
)
from vakr.ingest import LossKind, Spool, read_incidents
from vakr.ingest.reader import SpoolReader
from vakr.ingest.segments import list_segments
from vakr.ingest.settings import SpoolSettings

CRASH_EXIT = 17
_CHILD = """
import asyncio, os, sys
from pathlib import Path
from tests.unit.ingest.spool_testkit import (
    COLLECTOR, CountingOps, interface_samples, spool_settings)
from vakr.ingest import Spool

root, die_at = Path(sys.argv[1]), int(sys.argv[2])

class Ops(CountingOps):
    def fsync(self, fd):
        size = os.fstat(fd).st_size
        if die_at and len(self.fsyncs) >= die_at:
            os._exit(17)  # dies inside the fsync that would cover the next group
        super().fsync(fd)
        print("synced", size, flush=True)

async def main():
    spool = Spool(spool_settings(root, fsync_interval_s=0.02), COLLECTOR, ops=Ops())
    await spool.open()
    while True:
        receipt = await spool.append(interface_samples(5))
        print("ack", receipt.last_seq, flush=True)

asyncio.run(main())
"""


def _spawn(root: Path, die_at: int) -> subprocess.Popen[str]:
    """Start a child collector that appends until it is killed.

    Args:
        root: Spool root.
        die_at: Exit inside this many-th fsync, or 0 to run until killed.

    Returns:
        subprocess.Popen[str]: The child, with stdout piped.

    Raises:
        None
    """
    argv = [sys.executable, "-c", _CHILD, str(root), str(die_at)]
    return subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE, text=True)  # noqa: S603


def _acknowledged(lines: list[str]) -> int:
    """Return the highest sequence number the child was told is durable.

    Args:
        lines: The child's output lines.

    Returns:
        int: The sequence number, or 0.

    Raises:
        None
    """
    return max((int(line.split()[1]) for line in lines if line.startswith("ack")), default=0)


async def _recover(settings: SpoolSettings) -> tuple[list[int], Spool]:
    """Reopen the spool as the next start would and read every committed record.

    Args:
        settings: Spool settings.

    Returns:
        tuple[list[int], Spool]: Sequence numbers read, and the open spool.

    Raises:
        None
    """
    spool = Spool(settings, COLLECTOR)
    await spool.open()
    assert spool.incidents is not None
    reader = SpoolReader(spool, spool.incidents, lambda: 0)
    reader.start(None)
    seqs = [f.header.seq for f in reader.read(100_000).frames]
    reader.close()
    return seqs, spool


def test_spool_t06_sigkill_mid_append_keeps_every_acknowledged_record(tmp_path: Path) -> None:
    """SPOOL-T06, STORE-006: records acknowledged before SIGKILL all survive; the stop is reported.

    Args:
        tmp_path: pytest's directory; replaced by a disk directory on tmpfs hosts.

    Returns:
        None

    Raises:
        None
    """
    root = disk_dir(tmp_path)
    try:
        child = _spawn(root, die_at=0)
        assert child.stdout is not None
        lines = []
        while len([x for x in lines if x.startswith("ack")]) < 20:
            lines.append(child.stdout.readline())
        os.kill(child.pid, signal.SIGKILL)  # while it keeps appending
        lines += child.stdout.readlines()
        assert child.wait(timeout=30) == -signal.SIGKILL
        acked = _acknowledged(lines)

        async def check() -> None:
            """Recover and compare.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """
            seqs, spool = await _recover(spool_settings(root))
            assert seqs == list(range(1, len(seqs) + 1))
            assert len(seqs) >= acked >= 100
            assert spool.recovery is not None
            assert spool.recovery.unclean
            assert LossKind.UNCLEAN_STOP in {i.kind for i in read_incidents(spool.directory)}
            await spool.close()

        run(check())
    finally:
        remove_disk_dir(root, tmp_path)


def test_spool_t08_death_inside_fsync_loses_at_most_the_unsynced_group(tmp_path: Path) -> None:
    """SPOOL-T08, E.8: dying inside an fsync loses at most that group, and the loss is recorded.

    Args:
        tmp_path: pytest's directory; replaced by a disk directory on tmpfs hosts.

    Returns:
        None

    Raises:
        None
    """
    root = disk_dir(tmp_path)
    try:
        child = _spawn(root, die_at=12)
        out, _ = child.communicate(timeout=60)
        assert child.returncode == CRASH_EXIT
        acked = _acknowledged(out.splitlines())

        async def check() -> None:
            """Recover and compare.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """
            seqs, spool = await _recover(spool_settings(root))
            assert seqs[:acked] == list(range(1, acked + 1))  # nothing acknowledged is lost
            # Bytes written but not yet fsynced survive a process kill in the
            # page cache; recovery fsyncs them, so they are kept too.
            assert len(seqs) - acked <= spool.settings.fsync_records
            (incident,) = spool.recovery.incidents if spool.recovery else ()
            assert incident.kind is LossKind.UNCLEAN_STOP
            assert incident.first_seq == len(seqs) + 1
            await spool.close()

        run(check())
    finally:
        remove_disk_dir(root, tmp_path)


def test_spool_t02_power_cut_after_kill_truncates_the_torn_tail(tmp_path: Path) -> None:
    """SPOOL-T02, E.8: losing the page cache after a crash costs only un-fsynced records.

    The segment is cut back to the size it had at its last completed fsync
    and a half-written record is appended, which is what the disk may hold
    after a power cut. Every acknowledged record must still be readable.

    Args:
        tmp_path: pytest's directory; replaced by a disk directory on tmpfs hosts.

    Returns:
        None

    Raises:
        None
    """
    root = disk_dir(tmp_path)
    try:
        child = _spawn(root, die_at=12)
        out, _ = child.communicate(timeout=60)
        assert child.returncode == CRASH_EXIT
        lines = out.splitlines()
        acked = _acknowledged(lines)
        synced = [int(x.split()[1]) for x in lines if x.startswith("synced")]
        segment = list_segments(root / str(COLLECTOR))[-1].path
        with segment.open("r+b") as f:
            f.truncate(synced[-1])
            f.seek(0, os.SEEK_END)
            f.write(b"\x00\x00\x03\xa0garbage")  # a torn final record

        async def check() -> None:
            """Recover and compare.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """
            seqs, spool = await _recover(spool_settings(root))
            assert seqs == list(range(1, acked + 1))  # exactly the acknowledged records
            kinds = {i.kind for i in spool.recovery.incidents} if spool.recovery else set()
            assert kinds == {LossKind.TORN_TAIL, LossKind.UNCLEAN_STOP}
            await spool.close()

        run(check())
    finally:
        remove_disk_dir(root, tmp_path)
