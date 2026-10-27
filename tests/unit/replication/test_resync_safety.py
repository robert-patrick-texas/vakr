"""A demoted primary's resync keeps its report and takes one backup, whatever fails (E.39).

M6 review findings 2 and 3:

2. The replacing apply committed before the report was written. When the
   write failed, the marker stayed, and the next pull (digests now equal)
   overwrote the report with an empty one: the discarded rows and the
   backup's location were lost.
3. Every attempt of a pending resync wrote a new full backup, every 60 s,
   without bound.

Dependencies:
    pytest; tests.unit.replication.repl_testkit and test_node_resync.

Example:
    $ uv run pytest tests/unit/replication/test_resync_safety.py
"""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from tests.unit.replication.repl_testkit import FakePrimary, open_store, seed
from tests.unit.replication.test_node_resync import LATER, _Promoted
from tests.unit.replication.test_node_resync import partitioned as partitioned  # the fixture
from vakr.domain.paging import PageRequest
from vakr.ports.clock import ManualClock
from vakr.replication import role_record, status
from vakr.replication.metadata_pull import MetadataPuller, PullError
from vakr.replication.resync import Resync


class _Backups:
    """A backup stand-in that writes one small real file per call."""

    def __init__(self, state: Path) -> None:
        """Bind it.

        Args:
            state: The state directory; copies go to ``backups/``.

        Returns:
            None

        Raises:
            None
        """
        self.state, self.calls = state, []

    def __call__(self) -> Path:
        """Write one copy.

        Args:
            None

        Returns:
            Path: The file.

        Raises:
            None
        """
        target = self.state / "backups" / f"metadata-{len(self.calls)}.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"copy")
        self.calls.append(target)
        return target


def _fail_once(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make the first write of one role record fail, as a full disk does.

    Args:
        monkeypatch: Replaces ``role_record.write_json``.
        name: The record's file name.

    Returns:
        None

    Raises:
        None
    """
    real, failures = role_record.write_json, [OSError(28, "No space left on device")]

    def flaky(path: Path, doc: Any) -> None:
        """Write, failing once for ``name``.

        Args:
            path: The record.
            doc: Its document.

        Returns:
            None

        Raises:
            OSError: Once.
        """
        if path.name == name and failures:
            raise failures.pop()
        real(path, doc)

    monkeypatch.setattr(role_record, "write_json", flaky)


def _attempt(wf: Any, ef: Any, state: Path, backups: Any) -> str | None:
    """Run one pull of the demoted node, as the collector does each minute.

    Args:
        wf: The new primary's units of work.
        ef: The demoted node's units of work.
        state: Its state directory.
        backups: The backup stand-in.

    Returns:
        str | None: The pull's result, or ``None`` when it failed.

    Raises:
        None
    """
    resync = Resync(state, ef, backups, lambda: LATER) if Resync.pending(state) else None
    puller = MetadataPuller(_Promoted(wf), ef, state / status.FILE, ManualClock(LATER), resync)
    try:
        return puller.pull_once()
    except PullError:
        return None


def test_e39_a_failed_report_write_never_loses_the_discarded_rows(
    partitioned: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E.39 (finding 2): the report fails once; nothing is discarded unreported or forgotten.

    Args:
        partitioned: East and west, each edited during the partition.
        monkeypatch: Makes the first report write fail.

    Returns:
        None

    Raises:
        None
    """
    ef, wf, ids, state = partitioned
    _fail_once(monkeypatch, Path(role_record.REPORT_FILE).name)
    backups = _Backups(state)
    assert _attempt(wf, ef, state, backups) is None
    with ef() as uow:  # rolled back: east still holds its own edits
        assert uow.devices.get_by_name("edge9") is not None
    assert Resync.pending(state)
    assert _attempt(wf, ef, state, backups) == "applied"
    report = role_record.read_json(state / role_record.REPORT_FILE)
    assert report is not None
    assert report["state"] == "done"
    assert report["discarded_total"] >= 4
    assert report["backup"] == str(backups.calls[0])
    assert len(backups.calls) == 1
    assert not Resync.pending(state)
    assert ids


def test_e39_a_report_already_written_survives_a_failed_finish(
    partitioned: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E.39 (finding 2): the commit happened, then the marker stayed; the next pass keeps the rows.

    Args:
        partitioned: East and west, each edited during the partition.
        monkeypatch: Makes removing the marker fail once.

    Returns:
        None

    Raises:
        None
    """
    ef, wf, _, state = partitioned
    real_finish, failures = Resync.finish, [OSError(13, "Permission denied")]

    def flaky_finish(self: Resync) -> dict[str, Any]:
        """Fail once after the commit, as an unwritable directory does.

        Args:
            self: The resync.

        Returns:
            dict: The report.

        Raises:
            OSError: Once.
        """
        if failures:
            raise failures.pop()
        return real_finish(self)

    monkeypatch.setattr(Resync, "finish", flaky_finish)
    backups = _Backups(state)
    assert _attempt(wf, ef, state, backups) is None
    first = role_record.read_json(state / role_record.REPORT_FILE)
    assert first is not None
    assert first["state"] == "applying"
    assert _attempt(wf, ef, state, backups) == "unchanged"
    report = role_record.read_json(state / role_record.REPORT_FILE)
    assert report is not None
    assert report["state"] == "done"
    assert report["discarded_total"] == first["discarded_total"] >= 4
    assert report["discarded"] == first["discarded"]
    assert report["backup"] == str(backups.calls[0])
    assert not Resync.pending(state)


class _Torn(FakePrimary):
    """A new primary whose metadata changes during every pull."""

    def get_json(self, path: str, query: Any = None) -> Any:
        """Answer, renaming a tag between the state and the pages.

        Args:
            path: The endpoint.
            query: Its parameters.

        Returns:
            Any: The answer.

        Raises:
            None
        """
        if path == "metadata/state":
            self.before_pages = self._edit
        return super().get_json(path, query)

    def _edit(self) -> None:
        """Rename a tag, so the pages no longer hash to the advertised digest.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self.factory() as uow:
            tag = uow.tags.list_page(PageRequest()).items[0]
            uow.tags.update(dataclasses.replace(tag, name=f"{tag.name}x"), writer=tag.managed_by)
            uow.commit()


def test_e39_a_resync_takes_one_backup_however_many_attempts_fail(tmp_path: Path) -> None:
    """E.39 (finding 3): torn pulls retried every minute reuse the one backup.

    Args:
        tmp_path: Where the databases live.

    Returns:
        None

    Raises:
        None
    """
    east, ef = open_store(tmp_path / "east.db")
    west, wf = open_store(tmp_path / "west.db")
    seed(wf, ManualClock(LATER))
    role_record.write_json(tmp_path / role_record.RESYNC_FILE, {"at": LATER.isoformat()})
    backups = _Backups(tmp_path)
    for _ in range(4):  # a fresh Resync each minute, as the collector builds it
        resync = Resync(tmp_path, ef, backups, lambda: LATER)
        with pytest.raises(PullError):
            MetadataPuller(_Torn(wf), ef, tmp_path / "p.json", ManualClock(LATER),
                           resync).pull_once()  # fmt: skip
    assert len(backups.calls) == 1
    marker = role_record.read_json(tmp_path / role_record.RESYNC_FILE)
    assert marker is not None
    assert marker["backup"] == str(backups.calls[0])
    east.close()
    west.close()


def test_e39_a_backup_whose_path_cannot_be_kept_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E.39 (finding 3): when the marker cannot record the backup, the copy goes again.

    Args:
        tmp_path: The state directory.
        monkeypatch: Makes the marker write fail once.

    Returns:
        None

    Raises:
        None
    """
    role_record.write_json(tmp_path / role_record.RESYNC_FILE, {"at": LATER.isoformat()})
    _fail_once(monkeypatch, Path(role_record.RESYNC_FILE).name)
    backups = _Backups(tmp_path)
    resync = Resync(tmp_path, lambda: None, backups, lambda: LATER)
    with pytest.raises(OSError, match="No space"):
        resync.before_apply()
    assert not backups.calls[0].exists()
    resync.before_apply()
    resync.before_apply()
    assert len(backups.calls) == 2
    assert backups.calls[1].exists()


def test_mpos002_an_unwritable_pull_record_is_a_pull_failure_not_a_crash(
    tmp_path: Path,
) -> None:
    """MPOS-002: when the metadata pull record cannot be written, pull_once raises PullError only.

    Args:
        tmp_path: Where the databases live.

    Returns:
        None

    Raises:
        None
    """
    east, ef = open_store(tmp_path / "east.db")
    west, wf = open_store(tmp_path / "west.db")
    seed(wf, ManualClock(LATER))
    record = tmp_path / status.FILE
    record.mkdir(parents=True)  # a directory: every record write fails
    with pytest.raises(PullError):
        MetadataPuller(FakePrimary(wf), ef, record, ManualClock(LATER)).pull_once()
    east.close()
    west.close()
