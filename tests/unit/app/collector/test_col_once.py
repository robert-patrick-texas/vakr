"""``vakr collector once DEVICE``: one diagnostic cycle and its operator report (18.3, OBS-002).

Dependencies:
    tests.simulators.snmp.simlab: The in-process agent.
    tests.integration.collector.collector_lab: Install root and lab inventory.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_once.py
"""

import asyncio
from pathlib import Path

import pytest

from tests.integration.collector.collector_lab import (
    LabInventory,
    icmp_unavailable,
    lab_device,
    make_root,
)
from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, lab
from tests.unit.app.collector.col_fakes import T0, make_device, make_target
from tests.unit.collectors.icmp.icmp_results import make_result
from vakr.app.collector import inventory as inventory_seam
from vakr.app.collector.once import OnceResult, find_device, poll_once
from vakr.app.collector.once_report import EXIT_UNKNOWN, render_once
from vakr.app.collector.preflight import PreflightError
from vakr.cli.main import main
from vakr.collectors.icmp.cycle import PollContext as IcmpContext
from vakr.collectors.icmp.cycle import build_cycle
from vakr.collectors.snmp import skipped_snmp_observation
from vakr.discovery import discover
from vakr.domain.enums import AdminState
from vakr.domain.ids import CollectorId, PollId, new_id
from vakr.ports.clock import SystemClock
from vakr.scheduler.machine import Phase
from vakr.scheduler.runner import PollProgress, StepRecord
from vakr.scheduler.steps import StepStatus

ICMP_SKIP = icmp_unavailable()


def icmp_only(replies: list[int | str]) -> OnceResult:
    """Build a result with one ICMP cycle and SNMP skipped.

    Args:
        replies: Probe specs (``icmp_results`` notation).

    Returns:
        OnceResult: The cycle.

    Raises:
        None
    """
    device = make_device(1)
    poll, cid = new_id(PollId), new_id(CollectorId)
    cycle = build_cycle(make_result(replies), IcmpContext(poll, cid, device.id))
    progress = PollProgress()
    progress.steps.append(StepRecord(Phase.ICMP, StepStatus.OK, 0, 1))
    assert cycle.observation is not None
    progress.observations += [cycle.observation, skipped_snmp_observation(
        poll_id=poll, collector_id=cid, device_id=device.id, timestamp=T0)]  # fmt: skip
    return OnceResult(make_target(device), None, progress, 0.5, "vakr-east")


def test_report_says_down_only_for_target_evidence() -> None:
    """E.2: 0 replies is red Down (exit 2); collector trouble is unknown (exit 3), never Down.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    lines, code = render_once(icmp_only([-1, -1, -1, -1, -1]), T0)
    assert code == 2
    assert any(line.startswith("Verdict: Down (red") for line in lines)
    assert "not discovered yet" in "\n".join(lines)
    lines, code = render_once(icmp_only([900, 950]), T0)
    assert any("skipped" in line for line in lines)
    progress = PollProgress()
    progress.steps.append(StepRecord(Phase.ICMP, StepStatus.ERROR, 0, 1, "IcmpNotSentError"))
    unknown = OnceResult(make_target(make_device(1)), None, progress, 0.1, "vakr-east")
    lines, code = render_once(unknown, T0)
    assert code == EXIT_UNKNOWN
    assert "IcmpNotSentError" in lines[1]
    assert lines[2].startswith("SNMP: not polled")


def test_find_device_by_name_or_uuid() -> None:
    """Operators name a device or paste its UUID.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    devices = [make_device(1), make_device(2)]
    assert find_device(devices, "dev002") == devices[1]
    assert find_device(devices, str(devices[0].id)) == devices[0]
    assert find_device(devices, "nope") is None


@pytest.mark.skipif(ICMP_SKIP is not None, reason=ICMP_SKIP or "")
def test_once_polls_through_the_real_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """18.3: one cycle with the collector's own steps; nothing is spooled or stored.

    Args:
        tmp_path: Test directory.
        monkeypatch: Wires the lab inventory.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> tuple[list[str], int]:
        """Discover the simulated router, then poll it once.

        Args:
            None

        Returns:
            tuple[list[str], int]: Report lines and exit status.

        Raises:
            None
        """
        async with lab([router(1)]) as sim:
            paths = make_root(tmp_path, sim.port)
            device = lab_device("rtr-sim-001", 1)
            inventory = LabInventory([device])
            monkeypatch.setattr(inventory_seam, "INVENTORY_FACTORY", lambda _ctx: inventory)
            async with sim.factory.open(sim.target("rtr-sim-001"), FAST) as session:
                found = await discover(session, SystemClock())
            inventory.record_discovery(device.id, found, T0)
            result = await poll_once(paths, "rtr-sim-001", snmp_factory=sim.factory)
            assert not (paths.state_dir / "spool").exists()  # diagnostic: nothing spooled
            return render_once(result, T0)

    lines, code = asyncio.run(scenario())
    text = "\n".join(lines)
    assert code == 0, text
    assert "SNMP: responding" in text
    assert "sampled" in text
    assert "rates need a previous sample" in text


def test_once_refuses_unknown_and_disabled_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI exits 78 with an operator sentence when the device cannot be polled.

    Args:
        tmp_path: Test directory.
        monkeypatch: Relocates the install and wires the inventory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    paths = make_root(tmp_path, 161)
    monkeypatch.setenv("VAKR_ROOT", str(paths.root))
    off = make_device(5, admin_state=AdminState.DISABLED)
    monkeypatch.setattr(inventory_seam, "INVENTORY_FACTORY", lambda _ctx: LabInventory([off]))
    assert main(["collector", "once", "nosuch"]) == 78
    assert "no device 'nosuch'" in capsys.readouterr().err
    assert main(["collector", "once", "dev005"]) == 78
    assert "disabled, so it is not polled" in capsys.readouterr().err
    with pytest.raises(PreflightError):
        asyncio.run(poll_once(paths, "dev005"))
