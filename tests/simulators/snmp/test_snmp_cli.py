"""``vakr snmp discover|poll``: operator output and exit status against snmpsim.

The commands read the credential by name from a real credentials file in a
relocated install (``VAKR_ROOT``), talk to snmpsim, and print operator
language (blueprint 18.3). A community is refused on the command line
without being echoed (SNMP-019), a collector problem exits 5, and a silent
device exits 4 with a hint that silence is not proof the device is down.

Dependencies:
    pytest: Output capture and environment patching.
    tests.unit.credentials.credentials_testkit: The relocated secrets layout.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmp_cli.py
"""

from pathlib import Path

import pytest

from tests.simulators.snmp.simkit import fake_community
from tests.simulators.snmp.snmpsim_server import SnmpsimFleet, free_udp_port
from tests.unit.credentials.credentials_testkit import Install, v2c_table
from vakr.cli.main import main


@pytest.fixture
def install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snmpsim_fleet: SnmpsimFleet
) -> SnmpsimFleet:
    """Relocate the install and write a credentials file naming the fleet's devices.

    Args:
        tmp_path: Test directory.
        monkeypatch: Sets ``VAKR_ROOT``.
        snmpsim_fleet: The running fleet.

    Returns:
        SnmpsimFleet: The fleet, whose device names are now credential names.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    layout = Install.under(tmp_path)
    layout.write("".join(v2c_table(n, c) for n, c in snmpsim_fleet.communities.items()))
    return snmpsim_fleet


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run ``vakr`` and capture its output.

    Args:
        argv: Arguments after ``vakr``.
        capsys: Output capture.

    Returns:
        tuple[int, str, str]: Exit status, stdout, stderr.

    Raises:
        None
    """
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_discover_prints_operator_report(
    install: SnmpsimFleet, capsys: pytest.CaptureFixture[str]
) -> None:
    """DISC-003 (CLI), 18.3: discovery output names uptime as the agent's, not the chassis's.

    Args:
        install: The fleet and credentials.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    argv = ["snmp", "discover", "127.0.0.1", "--port", str(install.port)]
    code, out, _ = run([*argv, "--credential", "rtr-sim-001", "--dev"], capsys)
    assert code == 0
    assert "sysName      rtr-sim-001.example.net" in out
    assert "13 interfaces discovered, 10 admin up and oper up" in out
    assert "management-agent uptime" in out
    assert "GigabitEthernet0/0/0" in out
    assert "32-bit" in out
    assert install.communities["rtr-sim-001"] not in out


def test_poll_with_rates(install: SnmpsimFleet, capsys: pytest.CaptureFixture[str]) -> None:
    """RATE-001, SNMP-T05: two polls a second apart show traffic rates per interface.

    Args:
        install: The fleet and credentials.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    argv = ["snmp", "poll", "127.0.0.1", "--port", str(install.port), "--rate-after", "1"]
    code, out, _ = run([*argv, "--credential", "rtr-sim-003", "--dev"], capsys)
    assert code == 0
    assert "Stage 2: 10 up/up interfaces sampled" in out
    assert "IN-RATE" in out
    assert "Mb/s" in out
    assert "legacy32" in out


@pytest.mark.parametrize(
    ("extra", "code", "stream", "text"),
    [
        (["--credential", "no-such-device"], 5, "err", "collector problem, not a device problem"),
        (["--credential", "rtr-sim-001", "--port", "0"], 2, "err", "port must be an integer"),
        (["--credential", "rtr-sim-001", "--timeout", "0.5"], 2, "err", "--timeout must be"),
    ],
)
def test_collector_and_usage_problems_are_not_device_problems(
    install: SnmpsimFleet,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    code: int,
    stream: str,
    text: str,
) -> None:
    """Invariant 2: a missing credential is a collector problem; bad input is a usage error.

    Args:
        install: The fleet and credentials.
        capsys: Output capture.
        extra: Arguments that cause the problem.
        code: Expected exit status.
        stream: ``out`` or ``err``.
        text: Expected text.

    Returns:
        None

    Raises:
        None
    """
    port = ["--port", str(install.port)] if "--port" not in extra else []
    got, out, err = run(["snmp", "discover", "127.0.0.1", *port, *extra, "--dev"], capsys)
    assert got == code
    assert text in (out if stream == "out" else err)


@pytest.mark.usefixtures("install")
def test_silent_device_is_not_called_down(capsys: pytest.CaptureFixture[str]) -> None:
    """18.3: no SNMP answer exits 4 and says it does not prove the device is down.

    Args:
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    argv = ["snmp", "discover", "127.0.0.1", "--port", str(free_udp_port())]
    code, out, _ = run(
        [*argv, "--credential", "rtr-sim-001", "--timeout", "1", "--retries", "0", "--dev"], capsys
    )
    assert code == 4
    assert "No SNMP answer from 127.0.0.1" in out
    assert "does not mean the device is down" in out


@pytest.mark.parametrize("flag", ["--community", "-c"])
def test_snmp_019_community_on_the_command_line_is_refused_unechoed(
    capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    """SNMP-019, SEC-003: a community argument is refused and never printed back.

    Args:
        capsys: Output capture.
        flag: The refused option.

    Returns:
        None

    Raises:
        None
    """
    secret = fake_community()
    with pytest.raises(SystemExit) as caught:
        main(["snmp", "poll", "192.0.2.1", "--credential", "x", flag, secret])
    out = capsys.readouterr()
    assert caught.value.code == 2
    assert "never accepted on the command line" in out.err
    assert secret not in out.err + out.out


def test_multicast_target_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """15.5: a multicast address cannot be an SNMP agent.

    Args:
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    code, _, err = run(["snmp", "discover", "224.0.0.9", "--credential", "x"], capsys)
    assert code == 2
    assert "cannot be an SNMP agent" in err
