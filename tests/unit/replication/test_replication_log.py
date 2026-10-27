"""Replication log upkeep, schema 3, health, config, and pacing (MPOS-004 to MPOS-006, E.13).

Retention purges log rows with their observations, in bounded batches; a
schema-2 database migrates to 3 with its data intact; the observation half
of the ``replication`` check ranks lag, gaps, and failures; ``node.peer_url``
is validated like ``primary_url``; pacing yields to a backlogged local
spool; a primary records a partner collector once.

Dependencies:
    pytest; tests.unit.replication.obs_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_replication_log.py
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.contract import contract_samples as s
from tests.unit.replication import obs_testkit as k
from vakr.adapters.memory import memory_uow_factory
from vakr.adapters.sqlite.migrations import SqliteMigrationManager
from vakr.adapters.sqlite.migrations.model import SchemaFamily
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetryStore, telemetry_family
from vakr.adapters.sqlite.telemetry import replication_log as log
from vakr.app.collector import observation_loop as loop
from vakr.config.loader import EffectiveConfig
from vakr.config.node import node_problems, node_settings
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, new_id
from vakr.health.model import Status
from vakr.health.observation_check import judge
from vakr.replication import observation_status as rec


def _count(path: Path) -> int:
    """Count log rows.

    Args:
        path: The database.

    Returns:
        int: Rows in ``repl_log``.

    Raises:
        None
    """
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT count(*) FROM repl_log").fetchone()[0]


def test_mpos005_purge_removes_expired_log_rows_in_bounded_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MPOS-005, E.13: rows older than the horizon go, ``PURGE_ROWS`` a pass at most, oldest first.

    Args:
        tmp_path: Directory for the store.
        monkeypatch: Shrinks the batch.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    store = k.store_at(tmp_path)
    k.backend(store).writer.write_batch(new_id(BatchId), k.minutes(east, 4))
    monkeypatch.setattr(log, "PURGE_ROWS", 3)
    horizon = int((k.T0 + k.MINUTE * 2).timestamp() * 1_000_000)
    with closing(sqlite3.connect(store.path)) as conn, conn:
        assert log.purge_log(conn, None) == 0
        assert [log.purge_log(conn, horizon) for _ in range(5)] == [3, 3, 3, 1, 0]
        assert conn.execute("SELECT min(seq) FROM repl_log").fetchone()[0] == 11
    assert _count(store.path) == 10
    store.close()


def test_mig002_a_schema_2_database_migrates_to_3_with_its_data(tmp_path: Path) -> None:
    """MIG-002: rows written at schema 2 stay; the log starts empty and logs what comes next.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    base = telemetry_family()
    two = SchemaFamily(base.name, base.application_id, base.migrations[:2], base.compat_from)
    SqliteMigrationManager(path, two).migrate()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO dict_collector (collector_uuid) VALUES (?)", (new_id(CollectorId).bytes,)
        )
    applied = SqliteMigrationManager(path, base).migrate()
    assert [a.name for a in applied] == ["replication", "interface_status"]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM dict_collector").fetchone()[0] == 1
    store = SqliteTelemetryStore(path)
    k.backend(store).writer.write_batch(new_id(BatchId), k.minutes(new_id(CollectorId), 1))
    assert _count(path) == 5
    store.close()


def test_mpos006_the_observation_check_ranks_lag_gaps_and_failures() -> None:
    """MPOS-006, E.13: OK fresh; WARNING at 5 min, on gaps, or a failing pull; CRITICAL at 30 min.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fresh = rec.ObservationPullStatus(sources={"x": rec.SourceStatus(position="east")})
    assert judge(fresh, 40.0, 0).status is Status.OK
    assert "observations from east 40 s behind" in judge(fresh, 40.0, 0).summary
    assert judge(fresh, 301.0, 0).status is Status.WARNING
    assert judge(fresh, 1800.0, 0).status is Status.CRITICAL
    gapped = rec.ObservationPullStatus(sources={"x": rec.SourceStatus(gaps=3)})
    assert judge(gapped, 10.0, 0).status is Status.WARNING
    failing = rec.ObservationPullStatus(last_error="partner refused", sources={})
    assert judge(failing, 10.0, 60).status is Status.WARNING
    assert judge(None, None, 0).status is Status.WARNING


def test_peer_url_is_https_and_for_the_primary_only() -> None:
    """MPOS-006: ``node.peer_url`` must be https; a peer pulls from its primary instead.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    primary = EffectiveConfig({"node": {"role": "primary", "peer_url": "https://west.example/"}})
    assert node_problems(primary) == []
    assert node_settings(primary).partner_url == "https://west.example"
    plain = EffectiveConfig({"node": {"role": "primary", "peer_url": "http://west.example"}})
    assert any("node.peer_url must be" in p for p in node_problems(plain))
    peer = EffectiveConfig(
        {
            "node": {
                "role": "peer",
                "position": "west",
                "peer_url": "https://x",
                "primary_url": "https://east.example",
            }
        }
    )
    assert any("node.peer_url is for the primary" in p for p in node_problems(peer))
    assert node_settings(peer).partner_url == "https://east.example"
    assert node_settings(EffectiveConfig({})).partner_url == ""


def test_e13_pacing_yields_to_a_backlogged_local_spool() -> None:
    """E.13: full budget while local ingestion keeps up; half its progress while it lags.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    status = SimpleNamespace(acked_seq=100, inserted=1_000)
    service = SimpleNamespace(
        ingester=SimpleNamespace(status=status),
        parts=SimpleNamespace(spool=SimpleNamespace(durable_seq=150)),
    )
    pacer = loop._Pacer()
    assert pacer.budget(service) == loop.PASS_BUDGET
    service.parts.spool.durable_seq = 100 + loop.BACKLOG_RECORDS + 1
    status.inserted = 7_000
    assert pacer.budget(service) == 3_000  # half of the 6,000 local ingestion stored
    assert pacer.budget(service) == 0  # no local progress: backfill waits


def test_a_primary_records_a_partner_collector_once() -> None:
    """MPOS-007: the partner's collector gets a metadata record, so views name its position.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory, west = memory_uow_factory(), new_id(CollectorId)
    source = {"id": west, "name": "west-1", "position": "west"}
    loop.register_partner(factory, source)
    loop.register_partner(factory, source)
    with factory() as uow:
        found = uow.collectors.get(west)
    assert found is not None
    assert (found.name, found.position) == ("west-1", "west")
    taken = memory_uow_factory()
    with taken() as uow:
        uow.collectors.add(s.collector("west-1"))
        uow.commit()
    with pytest.raises(ValidationError, match="is taken here"):
        loop.register_partner(taken, source)
