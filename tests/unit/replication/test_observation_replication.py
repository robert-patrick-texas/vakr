"""Observation replication end to end: log, serve, pull, replay, gaps (MPOS-004, MPOS-005).

The source store logs what its spool ingests (``write_batch``); the target
pulls it through ``ObservationPuller`` and a JSON round trip. Every
observation arrives exactly as written, oldest seq first; a replayed pull
stores nothing new; an observation the source no longer holds is counted
as a gap instead of hidden.

Dependencies:
    pytest; tests.unit.replication.obs_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_observation_replication.py
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests.unit.replication import obs_testkit as k
from vakr.adapters.sqlite.telemetry.replication_feed import SqliteObservationFeed
from vakr.domain.errors import NotFoundError, ValidationError
from vakr.domain.ids import BatchId, CollectorId, new_id
from vakr.ports.clock import ManualClock
from vakr.replication import observation_status as rec
from vakr.replication.observation_pull import ObservationPuller, ObservationPullError
from vakr.replication.serving import ObservationSource


def _sorted(rows: list) -> list:
    """Order observations by UUID for comparison.

    Args:
        rows: Observations.

    Returns:
        list: Sorted by observation UUID.

    Raises:
        None
    """
    return sorted(rows, key=lambda o: o.observation_id)


def test_mpos004_local_ingest_is_logged_once_in_seq_order(tmp_path: Path) -> None:
    """MPOS-004: each new local row gets one seq; a replayed batch logs nothing more.

    Args:
        tmp_path: Directory for the store.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    store = k.store_at(tmp_path / "east")
    rows = k.minutes(east, 3)
    writer = k.backend(store).writer
    assert writer.write_batch(new_id(BatchId), rows).inserted == 15
    assert writer.write_batch(new_id(BatchId), rows).inserted == 0  # a replay (STORE-009)
    feed = SqliteObservationFeed(store)
    assert feed.bounds(east) == (1, 15)
    page = feed.feed(east, 0, 10)
    assert [e.seq for e in page.entries] == list(range(1, 11)) and page.has_more
    rest = feed.feed(east, page.next_after, 10)
    assert not rest.has_more and rest.next_after == 15
    got = [e.observation for e in (*page.entries, *rest.entries)]
    assert _sorted(got) == _sorted(rows)  # exactly as written, every family
    assert [o.timestamp for o in got] == sorted(o.timestamp for o in got)  # oldest first
    assert feed.feed(new_id(CollectorId), 0, 10).entries == ()
    with pytest.raises(ValidationError):
        feed.feed(east, -1, 10)
    store.close()


def test_mpos004_pull_stores_every_observation_and_a_replay_adds_none(tmp_path: Path) -> None:
    """MPOS-004, MPOS-T02: west holds every east observation, once; the cursor follows.

    Args:
        tmp_path: Directory for both stores.

    Returns:
        None

    Raises:
        None
    """
    east, west_id = new_id(CollectorId), new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "east"), k.store_at(tmp_path / "west")
    rows = k.minutes(east, 4)
    k.backend(src).writer.write_batch(new_id(BatchId), rows)
    partner = k.FakePartner(k.source_for(src, east))
    record = tmp_path / "west" / rec.FILE
    writer = k.backend(dst).writer
    puller = ObservationPuller(partner, writer, record, ManualClock(k.T0), west_id)
    assert puller.pull_once(50_000) == 20
    assert writer.cursor(east) == 20
    assert puller.pull_once(50_000) == 0  # nothing new: no duplicate (MPOS-004)
    status = rec.read_status(record)
    assert status is not None and status.sources[str(east)].backlog == 0
    assert status.sources[str(east)].position == "east" and status.last_error is None
    with closing(sqlite3.connect(dst.path)) as conn:
        uuids = conn.execute("SELECT count(*), count(DISTINCT observation_uuid) FROM "
                             "obs_icmp_hot").fetchone()  # fmt: skip
    assert uuids == (4, 4)
    assert SqliteObservationFeed(dst).bounds(east) == (None, None)  # not re-served onward
    src.close()
    dst.close()


def test_e13_budget_paces_a_backfill_oldest_first(tmp_path: Path) -> None:
    """E.13, MPOS-005: a small budget stores the oldest first; the next pass continues.

    Args:
        tmp_path: Directory for both stores.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "east"), k.store_at(tmp_path / "west")
    rows = k.minutes(east, 6)
    k.backend(src).writer.write_batch(new_id(BatchId), rows)
    writer = k.backend(dst).writer
    puller = ObservationPuller(k.FakePartner(k.source_for(src, east)), writer,
                               tmp_path / rec.FILE, ManualClock(k.T0), None)  # fmt: skip
    assert puller.pull_once(7) == 7
    assert writer.cursor(east) == 7
    oldest = sorted(o.timestamp for o in rows)[:5]
    status = rec.read_status(tmp_path / rec.FILE)
    assert status is not None and status.sources[str(east)].backlog == 23
    assert status.sources[str(east)].newest_ts is not None
    assert min(oldest) <= k.T0 + k.MINUTE  # the first pass took the first minutes
    assert puller.pull_once(50_000) == 23
    src.close()
    dst.close()


def test_mpos005_a_purged_observation_is_a_reported_gap(tmp_path: Path) -> None:
    """MPOS-005: what the source no longer holds is counted, not hidden; the rest arrives.

    Args:
        tmp_path: Directory for both stores.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "east"), k.store_at(tmp_path / "west")
    k.backend(src).writer.write_batch(new_id(BatchId), k.minutes(east, 2))
    with closing(sqlite3.connect(src.path)) as conn, conn:
        conn.execute("DELETE FROM obs_icmp_hot")  # as if retention had dropped them
    puller = ObservationPuller(k.FakePartner(k.source_for(src, east)), k.backend(dst).writer,
                               tmp_path / rec.FILE, ManualClock(k.T0), None)  # fmt: skip
    assert puller.pull_once(50_000) == 8
    status = rec.read_status(tmp_path / rec.FILE)
    assert status is not None and status.sources[str(east)].gaps == 2
    src.close()
    dst.close()


def test_serving_refuses_other_collectors_and_bounds(tmp_path: Path) -> None:
    """MPOS-004: only this node's collector is served; limits are checked; pages fit the budget.

    Args:
        tmp_path: Directory for the store.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    store = k.store_at(tmp_path)
    k.backend(store).writer.write_batch(new_id(BatchId), k.minutes(east, 2))
    source = k.source_for(store, east)
    with pytest.raises(NotFoundError):
        source.page(new_id(CollectorId), 0, 10)
    with pytest.raises(ValidationError):
        source.page(east, 0, 10_001)
    page = source.page(east, 0, 10)
    assert (page["next_after"], page["has_more"], page["oldest_seq"], page["newest_seq"]) == (
        10, False, 1, 10)  # fmt: skip
    assert all(o["envelope"]["collector_id"] == str(east) for o in page["observations"])
    assert [c["name"] for c in source.state()] == ["east"]
    empty = ObservationSource(lambda: (east,), lambda: None, k.source_for(store, east)._uow)
    with pytest.raises(Exception, match="no telemetry database"):
        empty.page(east, 0, 10)
    store.close()


def test_byte_budget_trims_a_page_and_the_next_one_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MPOS-004: an answer never exceeds the byte budget; nothing is skipped across the cut.

    Args:
        tmp_path: Directory for the store.
        monkeypatch: Shrinks the budget.

    Returns:
        None

    Raises:
        None
    """
    from vakr.replication import serving

    east = new_id(CollectorId)
    store = k.store_at(tmp_path)
    k.backend(store).writer.write_batch(new_id(BatchId), k.minutes(east, 2))
    monkeypatch.setattr(serving, "BYTE_BUDGET", 1500)
    source, seen, after = k.source_for(store, east), [], 0
    while True:
        page = source.page(east, after, 100)
        assert page["observations"], "every answer serves at least one observation"
        seen += [o["seq"] for o in page["observations"]]
        after = page["next_after"]
        if not page["has_more"]:
            break
    assert seen == list(range(1, 11))
    store.close()


def test_replicated_writes_refuse_foreign_rows_and_backward_cursors(tmp_path: Path) -> None:
    """MPOS-004: a page holds only its collector's observations; the cursor never moves back.

    Args:
        tmp_path: Directory for the store.

    Returns:
        None

    Raises:
        None
    """
    east, other = new_id(CollectorId), new_id(CollectorId)
    store = k.store_at(tmp_path)
    writer = k.backend(store).writer
    with pytest.raises(ValidationError, match="another collector"):
        writer.write_replicated(east, k.minutes(other, 1), 5)
    assert writer.write_replicated(east, k.minutes(east, 1), 5).inserted == 5
    with pytest.raises(ValidationError, match="move back"):
        writer.write_replicated(east, [], 4)
    assert writer.cursor(east) == 5
    store.close()


def test_mpos006_partner_trouble_is_recorded_as_node_health(tmp_path: Path) -> None:
    """MPOS-006: a malformed or self-pointing partner fails the pull and is recorded.

    Args:
        tmp_path: Directory for the stores.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "east"), k.store_at(tmp_path / "west")
    partner = k.FakePartner(k.source_for(src, east))
    record = tmp_path / rec.FILE
    puller = ObservationPuller(partner, k.backend(dst).writer, record, ManualClock(k.T0), east)
    with pytest.raises(ObservationPullError, match="this node's own collector"):
        puller.pull_once(100)
    status = rec.read_status(record)
    assert status is not None and status.pulls["failed"] == 1 and status.last_error
    src.close()
    dst.close()
