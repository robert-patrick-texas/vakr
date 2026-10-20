"""Tests for ``vakr icmp probe`` output and exit codes (ICMP-001..012, 18.3).

The prober is replaced by a fake returning canned results, so the tests
check operator wording and the Nagios-style exit status without sending
packets: 0 reachable, 1 impaired, 2 unreachable, 3 unknown.

Dependencies:
    pytest: Output capture and monkeypatching.
    tests.unit.collectors.icmp.icmp_results: Compact result builders.
    vakr.cli.cmd_icmp, vakr.cli.icmp_render: Code under test.

Example:
    $ uv run pytest tests/unit/collectors/icmp/test_icmp_cli.py
"""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import TracebackType

import pytest

from tests.unit.collectors.icmp.icmp_results import make_result
from vakr.cli import cmd_icmp
from vakr.cli.main import COMMAND_MODULES, main
from vakr.collectors.icmp.settings import IcmpSettings
from vakr.ports.icmp import CollectorFault, ProbeRequest, ProbeResult


class CannedProber:
    """Stands in for ``SocketIcmpProber`` and returns one prepared result."""

    result: ProbeResult = make_result([1000])
    requests: list[ProbeRequest] = []  # noqa: RUF012 - reset by the fixture

    def __init__(self, **_kwargs: object) -> None:
        """Accept the real constructor's keywords.

        Args:
            **_kwargs: Ignored.

        Returns:
            None

        Raises:
            None
        """

    async def __aenter__(self) -> CannedProber:
        """Enter.

        Args:
            None

        Returns:
            CannedProber: This prober.

        Raises:
            None
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit.

        Args:
            exc_type: Ignored.
            exc: Ignored.
            tb: Ignored.

        Returns:
            None

        Raises:
            None
        """

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Return the prepared result for this request.

        Args:
            request: The cycle.

        Returns:
            ProbeResult: The canned result, re-labelled with ``request``.

        Raises:
            None
        """
        self.requests.append(request)
        return replace(type(self).result, request=request)


@pytest.fixture
def canned(monkeypatch: pytest.MonkeyPatch) -> type[CannedProber]:
    """Install the canned prober and packaged-default settings.

    Args:
        monkeypatch: Pytest monkeypatch.

    Returns:
        type[CannedProber]: The class, whose ``result`` tests set.

    Raises:
        None
    """
    CannedProber.requests = []
    monkeypatch.setattr(cmd_icmp, "SocketIcmpProber", CannedProber)
    monkeypatch.setattr(cmd_icmp, "configured_settings", IcmpSettings)
    return CannedProber


@pytest.mark.parametrize(
    ("specs", "fault", "code", "state_line"),
    [
        pytest.param(
            [1000, 1200, 1100, 1300, 1050],
            None,
            0,
            "state: reachable: 5 of 5 replies, 0.0% loss",
            id="reachable",
        ),
        pytest.param(
            [-1, 1500, "u", 2500, -1],
            None,
            1,
            "state: impaired (loss): 2 of 5 replies, 60.0% loss",
            id="impaired",
        ),
        pytest.param(
            [-1] * 5, None, 2, "state: unreachable: 0 of 5 replies, 100.0% loss", id="unreachable"
        ),
        pytest.param(
            ["n"] * 5,
            CollectorFault.PERMISSION_DENIED,
            3,
            "state: unknown: no probe could be sent",
            id="collector problem",
        ),
    ],
)
def test_exit_status_follows_the_derived_state(
    canned: type[CannedProber],
    capsys: pytest.CaptureFixture[str],
    specs: list[int | str],
    fault: CollectorFault | None,
    code: int,
    state_line: str,
) -> None:
    """Nagios convention: 0 reachable, 1 impaired, 2 unreachable, 3 unknown.

    Args:
        canned: The canned prober.
        capsys: Output capture.
        specs: Probe outcomes in compact notation.
        fault: Result-level fault.
        code: Expected exit status.
        state_line: Expected state line.

    Returns:
        None

    Raises:
        None
    """
    canned.result = make_result(specs, fault=fault)
    assert main(["icmp", "probe", "192.0.2.1"]) == code
    out = capsys.readouterr().out
    assert state_line in out.splitlines()
    assert out.startswith("probing 192.0.2.1 (IPv4) from the default source: 5 probes")


def test_output_explains_each_probe_in_operator_language(
    canned: type[CannedProber], capsys: pytest.CaptureFixture[str]
) -> None:
    """18.3: lost, unreachable, and unsent probes read differently; missing is not 0 ms.

    Args:
        canned: The canned prober.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    canned.result = make_result([1204, -1, "u", "n"], fault=CollectorFault.OVERLOADED)
    args = ["icmp", "probe", "192.0.2.1", "--count", "4", "--interval-ms", "50"]
    assert main([*args, "--timeout-ms", "500", "--source", "192.0.2.10"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "probing 192.0.2.1 (IPv4) from 192.0.2.10: 4 probes, 50 ms apart, 500 ms timeout"
    )
    assert lines[1:5] == [
        "  probe 1: reply in 1.204 ms",
        "  probe 2: no reply within 500 ms (lost)",
        "  probe 3: lost: host unreachable",
        "  probe 4: not sent: the collector was too busy to send it on time (collector); "
        "not counted as loss",
    ]
    assert "  collector problem: the collector was too busy to send it on time." in lines
    assert "  SNMP under icmp_gated: would be skipped (under 2 replies)" in lines
    assert "  quality flags: SINGLE_REPLY, COLLECTOR_DEGRADED" in lines
    assert canned.requests == [ProbeRequest("192.0.2.1", 4, 0.05, 0.5, "192.0.2.10")]


def test_rtt_summary_and_names(
    canned: type[CannedProber], capsys: pytest.CaptureFixture[str]
) -> None:
    """A resolved name is shown with its address; the summary is in milliseconds.

    Args:
        canned: The canned prober.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    canned.result = make_result([1000, 1200, 1100, 1300, 1050])
    assert main(["icmp", "probe", "localhost", "--source", "127.0.0.1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("probing localhost (127.0.0.1, IPv4)")
    assert "  rtt min/avg/median/max: 1.000/1.130/1.100/1.300 ms, jitter 0.108 ms" in out
    assert "  SNMP under icmp_gated: would run" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["224.0.0.1"], "target must be a unicast address"),
        (["192.0.2.1", "--count", "0"], "count must be between 1 and 100"),
        (["192.0.2.1", "--source", "2001:db8::1"], "same address family"),
    ],
)
def test_invalid_input_is_unknown_with_a_reason(
    canned: type[CannedProber], capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    """15.5: bad targets are refused before any packet; exit 3, never "unreachable".

    Args:
        canned: The canned prober.
        capsys: Output capture.
        argv: Arguments after ``icmp probe``.
        message: Expected error text.

    Returns:
        None

    Raises:
        None
    """
    assert main(["icmp", "probe", *argv]) == 3
    assert message in capsys.readouterr().err
    assert canned.requests == []


def test_resolution_failure_is_unknown(
    canned: type[CannedProber], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """9.11: a DNS failure is not a device failure.

    Args:
        canned: The canned prober.
        capsys: Output capture.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """

    async def fails(target: str, source: str | None) -> str:
        """Fail like a resolver.

        Args:
            target: Name.
            source: Source.

        Returns:
            str: Never returns.

        Raises:
            OSError: Always.
        """
        raise OSError(-2, f"Name or service not known: {target} {source}")

    monkeypatch.setattr(cmd_icmp, "resolve", fails)
    assert main(["icmp", "probe", "core1.example.net"]) == 3
    assert "cannot resolve core1.example.net" in capsys.readouterr().err
    assert canned.requests == []


def test_literals_are_canonicalised_without_dns() -> None:
    """Upper-case IPv6 becomes canonical; a source restricts the family for names.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert asyncio.run(cmd_icmp.resolve("2001:DB8::1", None)) == "2001:db8::1"
    assert asyncio.run(cmd_icmp.resolve("localhost", "::1")) == "::1"
    assert asyncio.run(cmd_icmp.resolve("localhost", None)) in {"127.0.0.1", "::1"}


def test_command_is_registered_and_settings_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command is listed, ``vakr icmp`` alone prints help, and bad config falls back.

    Args:
        monkeypatch: Pytest monkeypatch.
        tmp_path: Pytest temporary directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert "vakr.cli.cmd_icmp" in COMMAND_MODULES
    assert main(["icmp"]) == 2
    assert "probe" in capsys.readouterr().err
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    config = tmp_path / "etc/vakr/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("config_version = 1\n[icmp]\nprobes = 3\n")
    assert cmd_icmp.configured_settings().probes == 3
    config.chmod(0)  # unreadable: the probe still runs on the packaged defaults
    try:
        assert cmd_icmp.configured_settings() == IcmpSettings()
    finally:
        config.chmod(0o600)
