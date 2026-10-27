"""A state file that cannot be written is a recorded, survivable pull failure (MPOS-006, E.39).

M6 review finding 1: ``note_partner`` raised OSError past ``pull_once``
and ``observation_pull_loop``, which ended observation replication until
the collector restarted and recorded nothing.

Dependencies:
    pytest; tests.unit.replication.obs_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_partner_record_failure.py
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.replication import obs_testkit as k
from vakr.app.collector import observation_loop
from vakr.config.loader import EffectiveConfig
from vakr.domain.ids import CollectorId, new_id
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock
from vakr.replication import observation_status as rec
from vakr.replication import role_record
from vakr.replication.observation_pull import ObservationPuller, ObservationPullError

WEST = "https://vakr-west.example.net"


def test_mpos006_an_unwritable_partner_record_is_a_recorded_pull_failure(tmp_path: Path) -> None:
    """MPOS-006: an OSError writing partner.json fails the pull, and the failure is recorded.

    Args:
        tmp_path: Directory for the stores and records.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "src"), k.store_at(tmp_path / "dst")
    record = tmp_path / rec.FILE
    (tmp_path / role_record.PARTNER_FILE).mkdir(parents=True)  # a directory: the write fails
    puller = ObservationPuller(k.FakePartner(k.source_for(src, east)), k.backend(dst).writer,
                               record, ManualClock(k.T0), new_id(CollectorId))  # fmt: skip
    with pytest.raises(ObservationPullError):
        puller.pull_once(100)
    found = rec.read_status(record)
    assert found is not None
    assert found.pulls["failed"] == 1
    assert found.last_error
    src.close()
    dst.close()


def test_mpos006_a_failure_record_that_cannot_be_written_is_still_reported(
    tmp_path: Path,
) -> None:
    """MPOS-006: when the pull record itself cannot be written, the message says so.

    Args:
        tmp_path: The record's directory.

    Returns:
        None

    Raises:
        None
    """
    record = tmp_path / rec.FILE
    record.mkdir(parents=True)  # a directory: the record write fails too
    text = rec.record_failure(record, rec.blank(), "tcp: cannot connect")
    assert text.startswith("tcp: cannot connect")
    assert "cannot be written either" in text


def test_mpos006_the_observation_loop_survives_an_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MPOS-006: the loop logs an OSError from one pass and keeps pulling.

    Args:
        tmp_path: The install root.
        monkeypatch: Replaces one pass and the interval.

    Returns:
        None

    Raises:
        None
    """
    config = EffectiveConfig({"node": {"role": "primary", "position": "east", "peer_url": WEST}})
    parts = SimpleNamespace(paths=InstallPaths.under(tmp_path), config=config)
    service = SimpleNamespace(parts=parts, loops_stop=asyncio.Event())
    calls: list[int] = []

    def one_pass(_service: Any, _pacer: Any) -> int:
        """Fail like a full disk once, then stop the loop.

        Args:
            _service: The service.
            _pacer: The pacing state.

        Returns:
            int: Nothing stored.

        Raises:
            OSError: On the first pass.
        """
        calls.append(1)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        service.loops_stop.set()
        return 0

    monkeypatch.setattr(observation_loop, "_pull", one_pass)
    monkeypatch.setattr(observation_loop, "PULL_INTERVAL_S", 0.01)
    asyncio.run(asyncio.wait_for(observation_loop.observation_pull_loop(service), 5))
    assert len(calls) == 2
