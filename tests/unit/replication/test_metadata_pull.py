"""Metadata pull: exact copies, torn snapshots refused, isolation kept (MPOS-002/003, LIFE-003).

Real SQLite stores on both sides; ``FakePrimary`` serves the replication
endpoints through a JSON round trip.

Dependencies:
    pytest; tests.unit.replication.repl_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_metadata_pull.py
"""

import dataclasses
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.contract import contract_samples as samples
from tests.unit.replication.repl_testkit import FakePrimary, open_store, puller, seed
from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.errors import ValidationError
from vakr.domain.timestamps import to_epoch_us
from vakr.health.model import Status
from vakr.health.replication_check import judge
from vakr.lifecycle.egress.errors import EgressError
from vakr.ports.clock import ManualClock
from vakr.ports.metadata.replica import ReplicaPage
from vakr.replication import status
from vakr.replication.metadata_pull import PullError

NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture
def pair(tmp_path: Path):
    """A seeded primary and an empty peer.

    Args:
        tmp_path: Where the databases live.

    Returns:
        tuple: Primary factory, peer factory, seeded IDs, and the pull record path.

    Raises:
        None
    """
    primary, pf = open_store(tmp_path / "east.db")
    peer, qf = open_store(tmp_path / "west.db")
    ids = seed(pf, ManualClock(NOW))
    yield pf, qf, ids, tmp_path / "pull.json"
    primary.close()
    peer.close()


def _record(path: Path) -> status.PullStatus:
    """Read a pull record that must exist.

    Args:
        path: The record file.

    Returns:
        status.PullStatus: The record.

    Raises:
        AssertionError: If there is none.
    """
    found = status.read_status(path)
    assert found is not None
    return found


class _Shifted(FakePrimary):
    """A partner whose state reports a schema moved by ``shift``."""

    shift = 0

    def get_json(self, path: str, query: Mapping[str, str | int] | None = None) -> Any:
        """Answer, with the schema moved on the state.

        Args:
            path: The endpoint.
            query: Its parameters.

        Returns:
            Any: The answer.

        Raises:
            None
        """
        doc = super().get_json(path, query)
        if path == "metadata/state":
            doc["schema"] += self.shift
        return doc


def _digest(factory) -> str:
    """Return a store's replicated digest.

    Args:
        factory: Its units of work.

    Returns:
        str: The digest.

    Raises:
        None
    """
    with factory() as uow:
        return uow.replica.state().digest


def test_mpos003_a_peer_copies_the_primary_exactly(pair) -> None:
    """MPOS-003: UUIDs, versions, accounts, memberships arrive unchanged; then a no-op.

    Args:
        pair: The stores.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, ids, record = pair
    fake = FakePrimary(pf)
    assert puller(fake, qf, record).pull_once() == "applied"
    assert _digest(qf) == _digest(pf)
    with pf() as a, qf() as b:
        assert b.devices.get(ids["device"]) == a.devices.get(ids["device"])
        assert b.interfaces.get(ids["iface"]) == a.interfaces.get(ids["iface"])
        assert b.users.get_by_username("alice") == a.users.get_by_username("alice")
        assert b.dashboards.get(ids["dashboard"]) == a.dashboards.get(ids["dashboard"])
    fake.calls.clear()
    assert puller(fake, qf, record).pull_once() == "unchanged"
    assert fake.calls == ["metadata/state"]  # no pages when nothing changed
    assert _record(record).pulls == {"applied": 1, "unchanged": 1, "failed": 0}


def test_mpos003_changes_and_removals_follow_the_primary(pair) -> None:
    """MPOS-003: a rename, a dropped tag, and a deleted dashboard reach the peer.

    Args:
        pair: The stores.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, ids, record = pair
    puller(FakePrimary(pf), qf, record).pull_once()
    with pf() as uow:
        dev = uow.devices.get(ids["device"])
        uow.devices.update(dataclasses.replace(dev, name="core1-renamed", tags=frozenset()),
                           writer=dev.managed_by)  # fmt: skip
        uow.dashboards.delete(ids["dashboard"])
        uow.commit()
    assert puller(FakePrimary(pf), qf, record).pull_once() == "applied"
    with qf() as uow:
        moved = uow.devices.get(ids["device"])
        assert (moved.name, moved.tags, moved.version) == ("core1-renamed", frozenset(), 2)
        assert uow.dashboards.get(ids["dashboard"]) is None
    assert _digest(qf) == _digest(pf)


def test_mpos003_a_snapshot_torn_by_an_edit_is_never_applied(pair) -> None:
    """MPOS-003: the rows must hash to the digest the state advertised, or nothing changes.

    Args:
        pair: The stores.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, _, record = pair
    fake = FakePrimary(pf)

    def edit() -> None:
        """Change the primary between the state answer and the pages.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with pf() as uow:
            uow.tags.add(samples.tag("late"))
            uow.commit()

    fake.before_pages = edit
    before = _digest(qf)
    with pytest.raises(PullError, match="does not match the primary's digest"):
        puller(fake, qf, record).pull_once()
    assert _digest(qf) == before
    assert "digest" in (_record(record).last_error or "")
    assert puller(fake, qf, record).pull_once() == "applied"  # the next pull is whole


def test_mpos002_an_unreachable_primary_keeps_last_known_metadata(pair) -> None:
    """MPOS-002, MPOS-006: a failed pull changes nothing and is reported as node health.

    Args:
        pair: The stores.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, ids, record = pair
    fake = FakePrimary(pf)
    puller(fake, qf, record).pull_once()
    fake.fail = EgressError("connect", "https://east.example: connection refused")
    with pytest.raises(PullError, match="connection refused"):
        puller(fake, qf, record).pull_once()
    with qf() as uow:
        assert uow.devices.get(ids["device"]) is not None
    found = _record(record)
    assert found.last_error
    assert found.last_success_at
    assert judge(found, 60).status is Status.OK
    assert judge(found, 301).status is Status.WARNING
    assert judge(found, 1801).status is Status.CRITICAL
    assert judge(dataclasses.replace(found, last_success_at=None), None).status is Status.CRITICAL
    assert judge(None, None).status is Status.WARNING


@pytest.mark.parametrize(("role", "schema", "says"), [
    ("peer", 0, "names another peer"),
    ("primary", 1, "metadata schema"),
])  # fmt: skip
def test_mpos003_wrong_partner_or_schema_is_refused(
    pair, role: str, schema: int, says: str
) -> None:
    """MPOS-003: only a primary at the same metadata schema is copied.

    Args:
        pair: The stores.
        role: What the partner says it is.
        schema: Added to its schema version.
        says: Part of the refusal.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, _, record = pair
    fake = _Shifted(pf, role=role)
    fake.shift = schema
    with pytest.raises(PullError, match=says):
        puller(fake, qf, record).pull_once()


def test_pages_are_bounded_complete_and_checked(pair) -> None:
    """E.13: a one-row page walk returns every row once; bad kinds, limits, and cursors are refused.

    Args:
        pair: The stores.

    Returns:
        None

    Raises:
        None
    """
    pf, _, _, _ = pair
    with pf() as uow:
        r = uow.replica
        full = r.page("tag", None, 1000).rows
        walked, cursor = [], None
        while True:
            p = r.page("tag", cursor, 1)
            walked += p.rows
            if (cursor := p.next_cursor) is None:
                break
        assert walked == list(full)
        assert len(full) >= 1
        cases = (("session", None, 10), ("tag", None, 0), ("tag", None, 1001), ("tag", "x", 10))
        for kind, cursor, limit in cases:
            with pytest.raises(ValidationError):
                r.page(kind, cursor, limit)
        snapshot = {t: (r.page(t, None, 1000),) for t in r.tables()}
        state = r.state()
        bad_cols = dict(snapshot, tag=(ReplicaPage("tag", ("id",), (), None),))
        with pytest.raises(ValidationError, match="columns differ"):
            r.apply(bad_cols, state.digest)
        with pytest.raises(ValidationError, match="no device pages"):
            r.apply({k: v for k, v in snapshot.items() if k != "device"}, state.digest)


def test_the_peer_keeps_its_own_collector_record(pair) -> None:
    """ADR-024: the peer's own collector row is local; the primary's arrives beside it.

    Args:
        pair: The stores.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, ids, record = pair
    west = samples.collector("west")
    with qf() as uow:
        uow.collectors.add(west)
        uow.commit()
    puller(FakePrimary(pf), qf, record).pull_once()
    with qf() as uow:
        assert uow.collectors.get(west.id) is not None
        assert uow.collectors.get(ids["collector"]) is not None


def test_life003_the_stale_clock_start_replicates_and_is_in_the_digest(
    pair, tmp_path: Path
) -> None:
    """LIFE-003, MPOS-003: the peer takes ``inactive_since`` from the primary; the digest covers it.

    A peer never writes lifecycle (ADR-024, E.6 "Peers"), so the start of
    the stale clock must arrive with the row. A change of that column alone
    must change the digest, or the peer would keep an older clock.

    Args:
        pair: The stores.
        tmp_path: Where the primary's file (``east.db``) lives.

    Returns:
        None

    Raises:
        None
    """
    pf, qf, ids, record = pair
    went_down = NOW - timedelta(days=3)
    with pf() as uow:
        iface = uow.interfaces.get(ids["iface"])
        uow.interfaces.set_lifecycle(iface.id, InterfaceLifecycle.INACTIVE,
                                     expected_version=iface.version, at=NOW,
                                     inactive_since=went_down)  # fmt: skip
        uow.commit()
    assert puller(FakePrimary(pf), qf, record).pull_once() == "applied"
    with pf() as a, qf() as b:
        assert b.interfaces.get(ids["iface"]) == a.interfaces.get(ids["iface"])
        assert b.interfaces.get(ids["iface"]).inactive_since == went_down
    before = _digest(pf)
    earlier = went_down - timedelta(days=1)
    with closing(sqlite3.connect(tmp_path / "east.db")) as conn:  # that column and nothing else
        conn.execute("UPDATE interface SET inactive_since = ?", (to_epoch_us(earlier),))
        conn.commit()
    assert _digest(pf) != before
    assert puller(FakePrimary(pf), qf, record).pull_once() == "applied"
    with qf() as uow:
        assert uow.interfaces.get(ids["iface"]).inactive_since == earlier
    assert _digest(qf) == _digest(pf)


def test_the_record_ages_and_survives_garbage(tmp_path: Path) -> None:
    """MPOS-006: the pull record is small, atomic, and an unreadable one counts as none.

    Args:
        tmp_path: Where it lives.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "replication" / "metadata-pull.json"
    status.record(path, "applied", NOW, digest="a" * 64, position="east")
    found = _record(path)
    assert found.success_age_s(NOW + timedelta(seconds=90)) == 90
    assert (path.stat().st_mode & 0o777, found.primary_position) == (0o640, "east")
    path.write_text("{not json")
    assert status.read_status(path) is None
