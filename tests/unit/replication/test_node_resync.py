"""A demoted primary's resync: converge exactly, back up first, report what was lost (E.39).

East was the primary; west copied it, was promoted during a partition, and
changed metadata. East kept accepting edits too. After ``vakr node demote``
on east, east's next pull from west, run as a ``Resync``, must leave east's
replicated metadata exactly west's, with east's partition edits listed in
the report and recoverable from the backup.

Dependencies:
    pytest; tests.unit.replication.repl_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_node_resync.py
"""

import dataclasses
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.contract import contract_auth_samples as auth_samples
from tests.contract import contract_samples as samples
from tests.unit.auth.auth_testkit import cheap_hasher
from tests.unit.replication.repl_testkit import FakePrimary, open_store, seed
from vakr.auth import Passwords
from vakr.auth.accounts import UserAdmin
from vakr.credentials.secret import Secret
from vakr.domain.users import Role
from vakr.ports.clock import ManualClock
from vakr.replication import role_record
from vakr.replication.metadata_pull import MetadataPuller
from vakr.replication.resync import Resync

FORK = datetime(2026, 10, 27, 10, tzinfo=UTC)
LATER = datetime(2026, 10, 27, 11, tzinfo=UTC)


class _Promoted(FakePrimary):
    """West after its promotion: a primary whose state carries the fork point."""

    def get_json(self, path: str, query: Any = None) -> Any:
        """Answer, adding the promotion to the state.

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
            doc["promotion"] = {"at": LATER.isoformat(), "fork_at": FORK.isoformat()}
        return doc


def _digest(factory: Any) -> str:
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


@pytest.fixture
def partitioned(tmp_path: Path):
    """East (old primary) and west (promoted), each edited during the partition.

    Args:
        tmp_path: Where the databases live.

    Returns:
        tuple: East's factory, west's factory, the seeded IDs, and the state directory.

    Raises:
        None
    """
    east, ef = open_store(tmp_path / "east.db")
    west, wf = open_store(tmp_path / "west.db")
    ids = seed(ef, ManualClock(FORK))
    MetadataPuller(FakePrimary(ef), wf, tmp_path / "w.json", ManualClock(FORK)).pull_once()
    with wf() as uow:  # west, promoted, renames the device
        dev = uow.devices.get(ids["device"])
        uow.devices.update(dataclasses.replace(dev, name="core1-west"), writer=dev.managed_by)
        uow.commit()
    with ef() as uow:  # east, cut off, still a primary, adds a device and edits the tag
        uow.devices.add(samples.device("edge9"))
        tag = uow.tags.get(ids["tag"])
        uow.tags.update(dataclasses.replace(tag, name="wan-east"), writer=tag.managed_by)
        uow.commit()
    admin = UserAdmin(ef, ManualClock(LATER), Passwords(cheap_hasher()))
    carol = admin.create("carol", Role.OPERATOR, Secret("another long test password"),
                         actor="cli:alice")  # fmt: skip
    with ef() as uow:
        uow.sessions.add(auth_samples.session(carol.id))
        uow.commit()
    state = tmp_path / "state"
    marker = {"at": LATER.isoformat(), "primary_url": "https://west.test"}
    role_record.write_json(state / role_record.RESYNC_FILE, marker)
    yield ef, wf, ids, state
    east.close()
    west.close()


def test_e39_a_demoted_primary_converges_and_reports_its_partition_edits(partitioned) -> None:
    """E.39, MPOS-003: after the resync east equals west; the report lists rows and edits.

    Args:
        partitioned: The two stores.

    Returns:
        None

    Raises:
        None
    """
    ef, wf, ids, state = partitioned
    backups: list[str] = []

    def backup() -> Path:
        """Stand in for the online backup.

        Args:
            None

        Returns:
            Path: Where the copy would be.

        Raises:
            None
        """
        backups.append("taken")
        return state / "backups" / "metadata-copy.db"

    assert Resync.pending(state)
    resync = Resync(state, ef, backup, lambda: LATER)
    puller = MetadataPuller(_Promoted(wf), ef, state / "pull.json", ManualClock(LATER), resync)
    assert puller.pull_once() == "applied"
    assert _digest(ef) == _digest(wf)
    assert backups == ["taken"]
    assert not Resync.pending(state)
    report = role_record.read_json(state / role_record.REPORT_FILE)
    assert report is not None
    found = {(d["table"], d["label"], d["change"]) for d in report["discarded"]}
    assert ("device", "edge9", "removed") in found
    assert ("tag", "wan-east", "replaced") in found
    assert ("device", "core1", "replaced") in found
    assert ("local_user", "carol", "removed") in found
    assert report["discarded_total"] == len(report["discarded"])
    assert report["fork_at"] == FORK.isoformat()
    assert report["backup"].endswith("metadata-copy.db")
    # Newest first; alice's account, made at the fork point itself, is inside the margin.
    assert [e["action"] for e in report["edits"]] == ["user.create", "user.create"]
    assert report["edits"][0]["actor"] == "cli:alice"
    assert "carol" in report["edits"][0]["summary"]
    assert "password" not in str(report).lower().replace("password_hash", "")
    with ef() as uow:
        assert uow.devices.get(ids["device"]).name == "core1-west"
        assert uow.devices.get_by_name("edge9") is None


def test_e39_guard_triggers_return_after_the_resync(partitioned) -> None:
    """E.39: the never-deleted guards are back after a replacing apply.

    Args:
        partitioned: The two stores.

    Returns:
        None

    Raises:
        None
    """
    ef, wf, _, state = partitioned
    resync = Resync(state, ef, lambda: None, lambda: LATER)
    MetadataPuller(_Promoted(wf), ef, state / "pull.json", ManualClock(LATER), resync).pull_once()
    # The connection's own context manager ends the transaction but leaves it open.
    with closing(sqlite3.connect(state.parent / "east.db")) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"device_never_deleted", "identity_review_closed_is_final"} <= names
        with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
            db.execute("DELETE FROM device")
    again = MetadataPuller(_Promoted(wf), ef, state / "pull.json", ManualClock(LATER))
    assert again.pull_once() == "unchanged"


def test_e39_a_resync_with_nothing_diverged_still_reports_and_clears(tmp_path: Path) -> None:
    """E.39: equal digests replace nothing; the report is written and the marker removed.

    Args:
        tmp_path: Where the databases live.

    Returns:
        None

    Raises:
        None
    """
    east, ef = open_store(tmp_path / "east.db")
    west, wf = open_store(tmp_path / "west.db")
    seed(wf, ManualClock(FORK))
    MetadataPuller(FakePrimary(wf), ef, tmp_path / "p.json", ManualClock(FORK)).pull_once()
    role_record.write_json(tmp_path / role_record.RESYNC_FILE, {"primary_url": "https://w"})
    resync = Resync(tmp_path, ef, lambda: None, lambda: LATER)
    puller = MetadataPuller(_Promoted(wf), ef, tmp_path / "p.json", ManualClock(LATER), resync)
    assert puller.pull_once() == "unchanged"
    report = role_record.read_json(tmp_path / role_record.REPORT_FILE)
    assert report is not None
    assert (report["discarded_total"], report["backup"]) == (0, None)
    assert not Resync.pending(tmp_path)
    east.close()
    west.close()
