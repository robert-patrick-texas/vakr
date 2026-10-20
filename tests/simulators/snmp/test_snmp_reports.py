"""Blueprint 18.3: the ``vakr snmp`` reports explain missing data in operator language.

Reports never show a missing value as ``0``; they say which table stopped
early and why, which interfaces lack fields, why a rate is unavailable
(for example an agent restart between samples), and when Stage 2 never ran.
An agent that answers with nothing usable exits 4 with advice about SNMP
views, rather than looking like success.

Dependencies:
    pytest: Output capture.
    vakr.cli: The report builders and the CLI.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmp_reports.py
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.simulators.snmp.simkit import fake_community
from tests.simulators.snmp.simlab import threaded_agent
from tests.unit.credentials.credentials_testkit import Install, v2c_table
from vakr.cli.main import main
from vakr.cli.snmp_poll_report import poll_report, rate_text
from vakr.cli.snmp_report import discovery_report, speed_text, uptime_text
from vakr.collectors.snmp import CapabilityChange, InterfaceSample, SnmpPollResult
from vakr.collectors.snmp.stage1 import InterfaceStatus, Stage1Result
from vakr.collectors.snmp.stage2 import Stage2Result
from vakr.discovery.model import (
    DiscoveredInterface,
    DiscoveryResult,
    SubtableResult,
    SubtableStatus,
    SystemFacts,
)
from vakr.domain.enums import CounterFamily, IfAdminStatus, IfOperStatus, SnmpVersion
from vakr.domain.quality import Quality
from vakr.ports.snmp import SnmpError, SnmpErrorKind, SnmpStats

AT = datetime(2026, 10, 20, tzinfo=UTC)
UP = (IfAdminStatus.UP, IfOperStatus.UP)


def discovery(complete: bool) -> DiscoveryResult:
    """Build a discovery result, complete or with gaps.

    Args:
        complete: Whether every table answered.

    Returns:
        DiscoveryResult: The result.

    Raises:
        None
    """
    timeout = SnmpError(SnmpErrorKind.TIMEOUT, "no answer from x within 2 s")
    tables = (
        SubtableResult("system", SubtableStatus.COMPLETE, 6),
        SubtableResult("ifXTable", SubtableStatus.COMPLETE if complete else SubtableStatus.PARTIAL,
                       9, () if complete else ("ifAlias",), None if complete else timeout),
        SubtableResult("ipAddressTable",
                       SubtableStatus.COMPLETE if complete else SubtableStatus.FAILED),
    )  # fmt: skip
    iface = DiscoveredInterface(
        1, if_name="Gi0/0/0", if_type=6, if_high_speed=1000, admin_status=UP[0], oper_status=UP[1],
        hc_octets=True, missing=() if complete else ("ifAlias",),
    )  # fmt: skip
    quality = Quality.NONE if complete else Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS
    system = SystemFacts(sys_name="rtr", sys_uptime_ticks=8_640_000)
    return DiscoveryResult("x:161", AT, AT, 0.1, system, (iface,), tables, SnmpStats(1, 1), quality)


def poll(ticks: int, octets: int, at: float, changes: tuple = ()) -> SnmpPollResult:
    """Build a poll of interface 1.

    Args:
        ticks: ``sysUpTime``.
        octets: Inbound and outbound octets.
        at: Monotonic time.
        changes: Learned capabilities.

    Returns:
        SnmpPollResult: The poll.

    Raises:
        None
    """
    stamp = AT + timedelta(seconds=at)
    stage1 = Stage1Result(stamp, at, ticks, (InterfaceStatus(1, *UP),), SnmpStats(1, 1))
    sample = InterfaceSample(
        1, None, stamp, at, CounterFamily.HC64, *UP, octets, octets, 10, 10, 0, 0
    )
    stage2 = Stage2Result((sample,), changes, SnmpStats(1, 1))
    return SnmpPollResult("x:161", SnmpVersion.V2C, stamp, at, 0.1, stage1, stage2, SnmpStats(2, 2))


def test_discovery_report_explains_gaps() -> None:
    """18.3: partial tables, missing fields, and incomplete addresses are spelled out.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = "\n".join(discovery_report(discovery(complete=False), "net"))
    assert "stopped early: no answer from x within 2 s" in text
    assert "Some data did not arrive. Values shown as ? are missing, not zero." in text
    assert "Interface addresses are incomplete" in text
    assert "Interfaces with missing fields: 1 (ifAlias)" in text
    assert "sysDescr     ?" in text
    assert "1 d 00:00:00" in text
    clean = "\n".join(discovery_report(discovery(complete=True), "net"))
    assert "did not arrive" not in clean


@pytest.mark.parametrize(
    ("speed", "high", "text"),
    [(None, 10_000, "10 Gb/s"), (None, 1_500, "1500 Mb/s"), (64_000, 0, "64 kb/s"),
     (2_000_000, None, "2 Mb/s"), (4_294_967_295, 0, "?"), (None, None, "?")],
)  # fmt: skip
def test_speed_and_rate_text(speed: int | None, high: int | None, text: str) -> None:
    """RATE-008: speeds prefer ifHighSpeed; saturated or missing speeds are unknown.

    Args:
        speed: ``ifSpeed``.
        high: ``ifHighSpeed``.
        text: Expected rendering.

    Returns:
        None

    Raises:
        None
    """
    assert speed_text(speed, high) == text
    assert uptime_text(None) == "?"
    assert [rate_text(v) for v in (None, 2.5e9, 512.0)] == ["?", "2.5 Gb/s", "512 b/s"]


def test_poll_report_explains_unavailable_rates_and_learning() -> None:
    """18.3, RATE-004: an agent restart between samples withholds rates and says why.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    learned = (CapabilityChange(1, "discontinuity", False),)
    first, rebooted = poll(1_000_000, 900_000, 0.0), poll(100, 10, 60.0, learned)
    text = "\n".join(poll_report(discovery(True), [first, rebooted], "net"))
    assert "unavailable: the SNMP agent restarted between the samples" in text
    assert (
        "Learned unsupported counters (not requested again until re-probe): 1 discontinuity" in text
    )
    single = "\n".join(poll_report(discovery(True), [first], "net"))
    assert "add --rate-after SECONDS" in single
    no_stage2 = SnmpPollResult("x:161", SnmpVersion.V2C, AT, 0.0, 2.0, first.stage1, None)
    assert "Stage 2 was not run" in poll_report(discovery(True), [no_stage2], "net")[-1]


def test_agent_with_nothing_usable_exits_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.2 row 7: an agent that answers with nothing usable is "SNMP failing", not success.

    Args:
        tmp_path: The relocated install.
        monkeypatch: Sets ``VAKR_ROOT``.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    community = fake_community()
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    Install.under(tmp_path).write(v2c_table("empty", community))
    with threaded_agent({community: []}) as port:  # a view that holds nothing
        argv = ["snmp", "poll", "127.0.0.1", "--port", str(port), "--credential", "empty", "--dev"]
        code = main(argv)
    out = capsys.readouterr().out
    assert code == 4
    assert "answered, but with nothing usable" in out
    assert "SNMP view" in out
