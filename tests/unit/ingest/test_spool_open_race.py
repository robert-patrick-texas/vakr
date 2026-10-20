"""An ingester started right after ``Spool.open`` never quarantines the live segment (E.24).

The writer thread creates the first segment (``O_EXCL``, then the header)
after ``Spool.open`` starts it. A reader that listed the directory in that
window found a header-less newest segment, judged it damaged, and renamed
the file the writer was still appending to, so every record went to
quarantine instead of storage. Found at the M3 merge under CPU load; this
test makes the window wide and deterministic.

Dependencies:
    pytest; the spool test kit and the memory telemetry adapter.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_open_race.py
"""

import os
import threading
import time
from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import COLLECTOR, FlakyWriter, interface_samples, run
from vakr.ingest import Ingester, Spool, SpoolSettings
from vakr.ingest import segment_writer as segment_writer_module
from vakr.ingest.fileops import FileOps
from vakr.ports.telemetry import TelemetryCapabilities


def test_store006_ingester_started_at_once_stores_every_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STORE-006, SPOOL-T05: the first segment exists with its header before ``open`` returns.

    Args:
        tmp_path: Spool directory root.
        monkeypatch: Slows segment creation down.

    Returns:
        None

    Raises:
        None
    """
    original = segment_writer_module.create_segment
    created = threading.Event()

    def slow_create(directory: Path, first_seq: int, created_us: int, ops: FileOps) -> int:
        """Leave an empty segment file visible for a while, as a busy host can.

        Args:
            directory: The spool directory.
            first_seq: First sequence number.
            created_us: Creation time.
            ops: File operations.

        Returns:
            int: The descriptor from the real ``create_segment``.

        Raises:
            OSError: As the real function does.
        """
        path = directory / f"seg-{first_seq:020d}.spool"
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640))
        created.set()
        time.sleep(0.3)
        path.unlink()
        return original(directory, first_seq, created_us, ops)

    monkeypatch.setattr(segment_writer_module, "create_segment", slow_create)

    async def scenario() -> FlakyWriter:
        """Open, start ingesting at once, append, and stop.

        Args:
            None

        Returns:
            FlakyWriter: The writer that received the batches.

        Raises:
            None
        """
        spool = Spool(SpoolSettings.defaults(tmp_path / "spool"), COLLECTOR)
        await spool.open()
        assert created.is_set()
        writer = FlakyWriter()
        ingester = Ingester(spool, writer, TelemetryCapabilities(False, 50_000))
        await ingester.start()
        await spool.append(interface_samples(30))
        await spool.close()
        await ingester.stop()
        return writer

    writer = run(scenario())
    assert len(writer.store.observations) == 30
    assert not list((tmp_path / "spool").rglob("*.damaged"))
    assert not (tmp_path / "spool" / "quarantine").exists()
