"""Blueprint 4.2 and 27.2: the MVP fleet is discovered and polled within one interval.

The reference workload is 100 IOS XE devices (80 routers, 20 switches) with
1,600 up/up interfaces at a 60 s interval. This check serves exactly that
fleet from snmpsim, discovers every device, polls each once with Stage 1
and Stage 2 at a global concurrency of 32 devices (one PDU in flight per
device), and requires everything to finish within one 60 s interval with a
per-device poll p95 well under the 15 s bound of blueprint 4.2.

It takes about half a minute on a 4 vCPU host, mostly in snmpsim, so it is
opt-in: ``VAKR_SNMP_LOAD=1 uv run pytest tests/simulators/snmp/test_snmp_load.py -s``
or ``make snmp-load``. The default run covers the same path with 11
devices (``test_snmpsim_collection.py``).

Dependencies:
    tests.simulators.snmp: The dataset, snmpsim runner, and load helper.

Example:
    $ make snmp-load
"""

import asyncio
import os
import platform
import sys
from pathlib import Path

import pytest

from tests.simulators.snmp.iosxe_dataset import device_records, reference_fleet
from tests.simulators.snmp.loadkit import run_cycle
from tests.simulators.snmp.simkit import fake_community
from tests.simulators.snmp.snmpsim_server import snmpsim

pytestmark = pytest.mark.skipif(
    os.environ.get("VAKR_SNMP_LOAD") != "1", reason="opt-in load check: set VAKR_SNMP_LOAD=1"
)


def test_mvp_fleet_within_one_interval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """4.2, 27.2: 100 devices discovered and polled in under 60 s, poll p95 well under 15 s.

    Args:
        tmp_path: snmpsim's data directory.
        capsys: Lets the measurements reach the terminal with ``-s``.

    Returns:
        None

    Raises:
        None
    """
    fleet = reference_fleet()
    communities = {spec.name: fake_community() for spec in fleet}
    devices = {communities[spec.name]: device_records(spec) for spec in fleet}
    with snmpsim(tmp_path, devices) as port:
        report = asyncio.run(run_cycle(port, communities, concurrency=32))
    durations = sorted(report.poll_durations_s)
    with capsys.disabled():
        print(
            f"\nSNMP load check on {platform.machine()}, {os.cpu_count()} CPUs, "
            f"Python {sys.version.split()[0]}: {report.devices} devices, "
            f"{report.interfaces} up/up interfaces sampled; discovery {report.discovery_s:.1f} s, "
            f"poll cycle {report.poll_s:.1f} s; per-device poll p50 "
            f"{durations[len(durations) // 2]:.2f} s, p95 {report.poll_p95_s:.2f} s, "
            f"max {durations[-1]:.2f} s"
        )
    assert report.answered == 100
    assert report.interfaces == 1_600
    assert report.discovery_s + report.poll_s < 60
    # "Well under" the 15 s bound. The simulator, one snmpsim process at about
    # 110 PDUs/s, dominates: switch polls queue there for up to ~5 s at this
    # concurrency, and take under 2 s at a concurrency of 8.
    assert report.poll_p95_s < 10
