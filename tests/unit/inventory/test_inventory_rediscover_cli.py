"""CLI: ``vakr inventory rediscover`` and ``vakr inventory operations`` (DISC-003, API-T06).

Runs the real commands against a relocated install (``VAKR_ROOT``) with a
SQLite metadata database. A thread stands in for the collector: it claims
the queued operation and records an outcome, as ``CollectorOperations``
does in ``vakr-collector``. Covered: the immediate answer, ``--wait`` to
success, failure, timeout, and "no collector reporting", refusals, the
list in text and JSON, and the audit actor.

Dependencies:
    pytest, threading; vakr.cli.main; the SQLite metadata adapter.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_rediscover_cli.py
"""

import json
import threading
import time
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.cli import inventory_ops
from vakr.cli.main import main
from vakr.domain.catalog import Collector
from vakr.domain.operations import OperationResult
from vakr.domain.paging import PageRequest
from vakr.operations import CollectorOperations, limits
from vakr.ports.clock import SystemClock
from vakr.ports.metadata import AuditFilter

RESULT = OperationResult(24, new=1, renumbered=0, missing=2, reviews_opened=1, duration_ms=1800)


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Collector:
    """A relocated install with ``core1`` (SNMP), ``quiet`` (no credential), and a collector.

    Args:
        tmp_path: Install root.
        monkeypatch: ``VAKR_ROOT`` and a fast ``--wait`` poll.

    Returns:
        Collector: The collector ``east``.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    monkeypatch.setattr(limits, "WAIT_POLL_S", 0.05)
    state = tmp_path / "var/lib/vakr"
    state.mkdir(parents=True)
    (tmp_path / "etc/vakr").mkdir(parents=True)
    metadata_migrations(state / METADATA_FILE).migrate()
    store = SqliteMetadataStore(state / METADATA_FILE)
    east = s.collector()
    with sqlite_uow_factory(store)() as uow:
        uow.collectors.add(east)
        uow.devices.add(s.device("core1", credential_ref="lab"))
        uow.devices.add(s.device("quiet", poll_address="192.0.2.9"))
        uow.commit()
    store.close()
    return east


def collector_stand_in(root: Path, east: Collector, error: str | None) -> threading.Thread:
    """Start a thread that claims the first queued operation and records an outcome.

    Args:
        root: The install root.
        east: The collector that claims.
        error: Record this failure; ``None`` records ``RESULT``.

    Returns:
        threading.Thread: The running thread.

    Raises:
        None
    """

    def work() -> None:
        """Wait for a queued operation, claim it, and finish it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        store = SqliteMetadataStore(root / "var/lib/vakr" / METADATA_FILE)
        runs = CollectorOperations(sqlite_uow_factory(store), SystemClock(), east.id)
        try:
            for _ in range(200):
                if queued := runs.pending():
                    claimed = runs.claim(queued[0])
                    # A lost claim (the CLI's own write committed in between) is retried,
                    # as the collector does at its next pickup; giving up left the wait
                    # running to its 300 s default on a loaded host.
                    if claimed is not None:
                        time.sleep(0.1)
                        runs.finish(claimed, None if error else RESULT, error)
                        return
                time.sleep(0.02)
        finally:
            store.close()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    """Run ``vakr`` and return its exit code and combined output.

    Args:
        capsys: Captures output.
        *argv: Arguments.

    Returns:
        tuple[int, str]: Exit code and stdout plus stderr.

    Raises:
        None
    """
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_disc003_rediscover_answers_at_once_and_wait_follows_to_the_result(
    node: Collector, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """DISC-003, DISC-010, API-T06: queued at once; --wait prints the result and exits 0.

    Args:
        node: The collector.
        tmp_path: The install root.
        capsys: Output capture.
        monkeypatch: Pretends a collector reports.

    Returns:
        None

    Raises:
        None
    """
    code, out = run(capsys, "inventory", "rediscover", "core1")
    assert code == 0
    assert "queued manual rediscovery of core1 (operation " in out
    assert "no collector has reported lately" in out  # no telemetry database at all
    code, out = run(capsys, "inventory", "rediscover", "core1")
    assert (code, "already queued; following" in out) == (0, True)
    monkeypatch.setattr(inventory_ops, "collectors_reporting", lambda _command: True)
    stand_in = collector_stand_in(tmp_path, node, None)
    code, out = run(capsys, "inventory", "rediscover", "core1", "--wait")
    stand_in.join(5)
    assert code == 0
    assert "core1: succeeded: 24 interfaces, 1 new, 0 renumbered, 2 missing" in out
    code, out = run(capsys, "inventory", "operations", "--json")
    rows = json.loads(out)["operations"]
    assert [(r["device_name"], r["state"]) for r in rows] == [("core1", "succeeded")]
    assert rows[0]["result"]["reviews_opened"] == 1
    assert rows[0]["requested_by"].startswith("cli:")
    store = SqliteMetadataStore(tmp_path / "var/lib/vakr" / METADATA_FILE)
    with sqlite_uow_factory(store)() as uow:
        audit = uow.audit.list_page(PageRequest(), AuditFilter(actor=rows[0]["requested_by"]))
    store.close()
    assert sum(1 for e in audit.items if e.action == "device.rediscover") == 3  # SEC-018


def test_wait_reports_failure_timeout_and_a_missing_collector(
    node: Collector, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """DISC-003: --wait exits 1 on failure, 3 without a collector or after --timeout.

    Args:
        node: The collector.
        tmp_path: The install root.
        capsys: Output capture.
        monkeypatch: Switches the collector check.

    Returns:
        None

    Raises:
        None
    """
    code, out = run(capsys, "inventory", "rediscover", "core1", "--wait")
    assert code == 3
    assert "nothing will pick this up until vakr-collector runs" in out
    monkeypatch.setattr(inventory_ops, "collectors_reporting", lambda _command: True)
    code, out = run(capsys, "inventory", "rediscover", "core1", "--wait", "--timeout", "1")
    assert code == 3
    assert "still queued after 1 s" in out
    stand_in = collector_stand_in(tmp_path, node, "the agent did not answer")
    code, out = run(capsys, "inventory", "rediscover", "core1", "--wait", "--timeout", "30")
    stand_in.join(5)
    assert code == 1
    assert "core1: failed: the agent did not answer" in out
    code, out = run(capsys, "inventory", "operations", "core1")
    assert "failed: the agent did not answer" in out.splitlines()[1]


@pytest.mark.parametrize(
    ("argv", "status", "says"),
    [
        (("rediscover", "quiet"), 1, "has no SNMP credential"),
        (("rediscover", "nosuch"), 1, "no device nosuch"),
        (("operations", "nosuch"), 1, "no device nosuch"),
        (("operations", "--limit", "0"), 1, "--limit must be 1 to 500"),
        (("operations",), 0, "no operations"),
    ],
)
def test_refusals_name_the_reason(
    node: Collector, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], status: int,
    says: str,
) -> None:  # fmt: skip
    """DISC-003: a device no collector would discover, or an unknown one, is refused with why.

    Args:
        node: The collector.
        capsys: Output capture.
        argv: Arguments after ``inventory``.
        status: Expected exit code.
        says: Part of the expected output.

    Returns:
        None

    Raises:
        None
    """
    assert node.name == "east"
    code, out = run(capsys, "inventory", *argv)
    assert code == status
    assert says in out


def test_a_timeout_out_of_bounds_is_a_usage_error(
    node: Collector, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.32: ``--timeout`` is 1 to 3600 seconds; anything else exits 2.

    Args:
        node: The collector.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert node.name == "east"
    with pytest.raises(SystemExit) as stopped:
        main(["inventory", "rediscover", "core1", "--timeout", "0"])
    assert stopped.value.code == 2
    assert "timeout must be 1 to 3600" in capsys.readouterr().err
