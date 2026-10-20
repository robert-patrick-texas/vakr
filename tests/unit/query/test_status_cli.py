"""``vakr status``: text and ``--json`` output, Nagios exit codes, and the "no data yet" path.

Every test relocates the installation with ``VAKR_ROOT`` and uses real,
migrated SQLite databases, written through the adapters' ports exactly as
the collector writes them. The command reads them with the system clock,
so observations are stamped relative to the real current time.

Dependencies:
    pytest; vakr.adapters.sqlite; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/query/test_status_cli.py
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as samples
from tests.unit.query.query_testkit import RTTS, icmp_summary
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, metadata_migrations
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
    telemetry_migrations,
)
from vakr.cli import cmd_status
from vakr.cli.main import main
from vakr.domain.enums import IcmpState, UptimeContinuity
from vakr.domain.ids import BatchId, PollId, new_id
from vakr.domain.inventory import Device
from vakr.domain.observations import Observation
from vakr.domain.quality import Quality
from vakr.query.text import utc_iso

CONTINUOUS = UptimeContinuity.CONTINUOUS


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the installation and create its state directory.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: The state directory.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    state_dir = tmp_path / "var/lib/vakr"
    state_dir.mkdir(parents=True)
    return state_dir


def seed(state_dir: Path, *, telemetry: bool = True, down: bool = False) -> Device:
    """Create migrated databases with a collector and two devices, ``core1`` and ``sw2``.

    Args:
        state_dir: The state directory.
        telemetry: Also create the telemetry database and a poll of each device.
        down: Make ``sw2`` unreachable.

    Returns:
        Device: ``core1``.

    Raises:
        None
    """
    meta_path = state_dir / METADATA_FILE
    metadata_migrations(meta_path).migrate()
    meta = SqliteMetadataStore(meta_path)
    collector = samples.collector("east")
    core, sw2 = samples.device("core1"), samples.device("sw2", poll_address="192.0.2.2")
    with meta.unit_of_work() as uow:
        uow.collectors.add(collector)
        uow.devices.add(core)
        uow.devices.add(sw2)
        uow.interfaces.add(samples.interface(core.id, "Gi1/0/1", if_high_speed=1000))
        uow.commit()
    meta.close()
    if not telemetry:
        return core
    tele_path = state_dir / TELEMETRY_FILE
    telemetry_migrations(tele_path).migrate()
    store = SqliteTelemetryStore(tele_path)
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=20)
    obs: list[Observation] = []
    for device, unreachable in ((core, False), (sw2, down)):
        pid = new_id(PollId)
        state = IcmpState.UNREACHABLE if unreachable else IcmpState.REACHABLE
        rtts = RTTS[state]
        ping = {"state": state, "rtts_us": rtts, "quality": Quality.NONE, **icmp_summary(rtts)}
        obs.append(samples.poll(device.id, collector.id, at, poll_id=pid))
        obs.append(samples.icmp(device.id, collector.id, at, poll_id=pid, **ping))
        if not unreachable:
            uptime = {"sys_uptime_ticks": 360_000_000, "uptime_continuity": CONTINUOUS}
            obs.append(samples.snmp(device.id, collector.id, at, poll_id=pid, **uptime))
    sqlite_telemetry_factory(store)().writer.write_batch(new_id(BatchId), obs)
    store.close()
    return core


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Run ``vakr status`` and capture its output.

    Args:
        capsys: Output capture.
        *args: Arguments after ``status``.

    Returns:
        tuple[int, str, str]: Exit status, stdout, stderr.

    Raises:
        None
    """
    code = main(["status", *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_no_data_yet_exits_unknown_and_creates_nothing(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nagios 3 and "no data yet"; the databases are never created by a read.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    code, out, err = run(capsys)
    assert (code, out) == (3, "")
    assert "no data yet" in err
    assert not (state / METADATA_FILE).exists()
    assert not (state / TELEMETRY_FILE).exists()
    code, out, _ = run(capsys, "--json")
    doc = json.loads(out)
    assert (code, doc["schema"], doc["exit_code"]) == (3, "vakr.status/1", 3)
    assert "no data yet" in doc["error"]


def test_all_ok_exits_zero(state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Nagios 0 when nothing needs attention; the table shows icon and label.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    code, out, _ = run(capsys)
    assert code == 0
    assert "0 attention, 2 active, 0 offline" in out
    assert "✔ OK" in out
    assert "STATE" in out
    assert "showing 1 to 2 of 2" in out


def test_a_device_down_exits_one_and_json_matches(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nagios 1 for Attention; the JSON schema carries values, reasons, and UTC times.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(state, down=True)
    code, out, _ = run(capsys, "--json")
    doc = json.loads(out)
    assert code == 1
    assert doc["exit_code"] == 1
    assert doc["schema"] == "vakr.status/1"
    assert doc["generated_at"].endswith("Z")
    assert doc["counts"] == {"attention": 1, "active": 1, "offline": 0}
    first = doc["devices"][0]
    assert first["name"] == "sw2"
    assert first["status"]["label"] == "Down"
    assert first["status"]["bucket"] == "attention"
    assert first["status"]["icon"] == "✖"
    assert first["latency"] == {
        "text": "—",
        "value": None,
        "reason": "no probe answered",
        "stale": False,
    }
    assert first["loss"]["value"] == 100.0
    assert doc["health"]["telemetry"]["value"] == "ok"
    assert set(first) >= {"device_id", "address", "site", "tags", "freshness", "age", "uptime"}


def test_filters_and_exit_code_follow_the_listed_devices(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit status covers the devices listed; an empty filtered list is not "no data".

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(state, down=True)
    assert run(capsys, "--bucket", "active")[0] == 0
    assert run(capsys, "--bucket", "attention")[0] == 1
    code, out, _ = run(capsys, "--search", "nothing-here")
    assert code == 0
    assert "no devices match" in out


def test_device_detail_text_and_json(state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr status DEVICE``: facts with reasons, interfaces, and notes.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    core = seed(state)
    code, out, _ = run(capsys, "core1")
    assert code == 0
    assert out.startswith("core1 — ✔ OK")
    assert "System description" in out
    assert "Gi1/0/1" in out
    assert "no sample from east" in out
    assert "UPTIME-003" in out
    code, out, _ = run(capsys, str(core.id), "--json")
    doc = json.loads(out)
    assert (code, doc["device"]["name"]) == (0, "core1")
    labels = [f["label"] for f in doc["facts"]]
    assert labels[:3] == ["Status", "Poll address", "Admin state"]
    assert doc["interfaces"][0]["capacity"]["text"] == "1.00 Gb/s (ifHighSpeed)"
    code, _, err = run(capsys, "missing-device")
    assert code == 3
    assert "no device named 'missing-device'" in err


def test_missing_telemetry_lists_inventory_as_unknown(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """13.4: metadata plus explicit telemetry unavailability; Nagios 3, never Down.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(state, telemetry=False)
    code, out, _ = run(capsys)
    assert code == 3
    assert "Unknown — telemetry unavailable" in out
    assert "Down" not in out
    assert "telemetry unavailable: no telemetry database yet" in out
    assert not (state / TELEMETRY_FILE).exists()
    assert run(capsys, "core1")[0] == 3


def test_empty_inventory_and_unreadable_metadata(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nagios 3 when there are no devices, or the metadata file is not a database.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    metadata_migrations(state / METADATA_FILE).migrate()
    code, _, err = run(capsys)
    assert code == 3
    assert "no devices in inventory yet" in err
    (state / METADATA_FILE).unlink()
    (state / METADATA_FILE).write_bytes(b"not a database" * 100)
    code, _, err = run(capsys)
    assert code == 3
    assert "cannot read the metadata database" in err


def test_usage_errors_and_service_user_failure(
    state: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out-of-range paging is a usage error (2); a failed privilege drop is unknown (3).

    Args:
        state: State directory.
        capsys: Output capture.
        monkeypatch: Replaces the privilege drop.

    Returns:
        None

    Raises:
        None
    """
    seed(state)
    assert run(capsys, "--limit", "0")[0] == 2
    monkeypatch.setattr(cmd_status, "drop_to_service_user", lambda: "cannot switch")
    code, _, err = run(capsys)
    assert (code, "cannot switch" in err) == (3, True)
    assert utc_iso(None) is None
