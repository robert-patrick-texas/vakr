"""MPOS-004 for interface status records: they reach the partner with their poll (E.35).

``repl_log`` admits families 1 to 5 only, so a poll's status records are
served attached to its SNMP device observation's log entry and stored by
the partner with the page, idempotently. A page from a partner that
predates them has no attachments, and a stray or foreign attachment is
refused like any other bad observation.

Dependencies:
    pytest; tests.unit.replication.obs_testkit; vakr.replication.

Example:
    $ uv run pytest tests/unit/replication/test_status_replication.py
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.replication import obs_testkit as k
from vakr.adapters.sqlite.telemetry.replication_feed import SqliteObservationFeed
from vakr.domain.enums import IfOperStatus, ObservationType
from vakr.domain.envelope import envelope_to_dict, seal
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.observations import SnmpDeviceObservation
from vakr.domain.paging import PageRequest
from vakr.domain.series import TimeWindow
from vakr.ports.clock import ManualClock
from vakr.replication import observation_status as rec
from vakr.replication.attachments import verified_attachments
from vakr.replication.observation_pull import ObservationPuller


def _rows(east: CollectorId) -> tuple[list, list]:
    """Build two minutes of every family, plus status records of the first minute's poll.

    Args:
        east: The collector.

    Returns:
        tuple[list, list]: Every observation, and the status records.

    Raises:
        None
    """
    rows = k.minutes(east, 2)
    snmp = next(o for o in rows if isinstance(o, SnmpDeviceObservation))
    iface = new_id(InterfaceId)
    status = [
        s.status(snmp.device_id, iface, east, snmp.timestamp, poll_id=snmp.poll_id),
        s.status(snmp.device_id, new_id(InterfaceId), east, snmp.timestamp, poll_id=snmp.poll_id,
                 oper_status=IfOperStatus.UP, baseline=False),
    ]  # fmt: skip
    return [*status, *rows], status


def test_mpos004_status_records_replicate_with_their_poll(tmp_path: Path) -> None:
    """MPOS-004, E.35: west stores east's status records once; none enter the log.

    Args:
        tmp_path: Directory for both stores.

    Returns:
        None

    Raises:
        None
    """
    east, west = new_id(CollectorId), new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "east"), k.store_at(tmp_path / "west")
    rows, status = _rows(east)
    k.backend(src).writer.write_batch(new_id(BatchId), rows)
    with closing(sqlite3.connect(src.path)) as conn:
        assert conn.execute("SELECT max(family) FROM repl_log").fetchone() == (5,)
    page = SqliteObservationFeed(src).feed(east, 0, 100)
    assert sorted(str(a.observation.observation_id) for a in page.attached) == sorted(
        str(o.observation_id) for o in status)  # fmt: skip
    puller = ObservationPuller(k.FakePartner(k.source_for(src, east)), k.backend(dst).writer,
                               tmp_path / "west" / rec.FILE, ManualClock(k.T0), west)  # fmt: skip
    assert puller.pull_once(50_000) == 12
    assert puller.pull_once(50_000) == 0  # a replay stores nothing twice
    window = TimeWindow(k.T0, k.T0 + 10 * s.MINUTE)
    for record in status:
        stored = k.backend(dst).query.read_raw(
            ObservationType.INTERFACE_STATUS, record.interface_id, window, PageRequest(5)
        )
        assert list(stored.items) == [record]
    src.close()
    dst.close()


def test_mpos004_an_older_partner_page_has_no_attachments() -> None:
    """MPOS-004: a page without ``attachments`` (a partner before schema 4) has none to store.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert verified_attachments({"next_after": 3, "observations": []}, new_id(CollectorId), 0) == []


def test_mpos004_stray_or_foreign_attachments_are_refused() -> None:
    """MPOS-004: an attachment outside the page, foreign, or not a status record is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    good = s.status(s.device().id, new_id(InterfaceId), east, k.T0)
    sealed = envelope_to_dict(seal(good, new_id(BatchId)))
    page = {"next_after": 5, "attachments": [{"seq": 4, "envelope": sealed}]}
    assert verified_attachments(page, east, 3) == [good]
    with pytest.raises(ValidationError):
        verified_attachments(page, east, 4)  # seq not inside the page
    with pytest.raises(ValidationError):
        verified_attachments(page, new_id(CollectorId), 3)
    icmp = envelope_to_dict(seal(s.icmp(s.device().id, east, k.T0), new_id(BatchId)))
    with pytest.raises(ValidationError):
        verified_attachments({"next_after": 5, "attachments": [{"seq": 4, "envelope": icmp}]},
                             east, 3)  # fmt: skip
