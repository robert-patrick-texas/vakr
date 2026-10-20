"""A host that cannot ping is a hard stop for the collector (ICMP-012, owner decision 2026-10-20).

The collector refuses to start when this process can open neither an
unprivileged ping socket nor a raw socket (the ``CAP_NET_RAW`` drop-in), and
it stops with exit status 78, which systemd does not restart, when the host
withdraws permission while it runs. ``vakr check health`` reports the host
as CRITICAL to monitoring, but the self-update gate counts it as a warning,
because rolling a release back cannot fix a sysctl (UPD-006).

Dependencies:
    pytest; the collector lab install and the scripted ICMP prober.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_icmp_hard_stop.py
"""

import argparse
import asyncio
from pathlib import Path

import pytest

from tests.integration.collector.collector_lab import make_root
from tests.unit.app.collector.col_fakes import ScriptedProber, make_device, make_target, poll_ctx
from tests.unit.collectors.icmp.icmp_results import make_result
from vakr.app.collector import preflight
from vakr.app.collector.build import build_parts
from vakr.app.collector.icmp_step import CollectorIcmpStep, IcmpNotSentError
from vakr.app.collector.tally import FaultTally
from vakr.app.collector.timings import EX_PREFLIGHT
from vakr.cli import cmd_check
from vakr.collectors.icmp.cycle import IcmpCollector
from vakr.collectors.icmp.settings import IcmpSettings
from vakr.health.model import CheckResult, Status
from vakr.ports.icmp_types import CollectorFault, ProbeRequest, ProbeResult

REFUSED = "Operation not permitted"


@pytest.mark.real_icmp_preflight
def test_a_host_that_cannot_ping_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ICMP-012: neither socket opens, so preflight stops with the fix (exit 78).

    Args:
        tmp_path: Test directory.
        monkeypatch: Makes both socket tests fail.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(preflight, "socket_problem", lambda: REFUSED)
    monkeypatch.setattr(preflight, "raw_socket_problem", lambda: REFUSED)
    paths = make_root(tmp_path, 161)
    with pytest.raises(preflight.PreflightError) as refused:
        asyncio.run(build_parts(paths))
    assert refused.value.check == "icmp"
    assert "this host cannot send ICMP Echo" in refused.value.message
    assert not (paths.state_dir / "spool").exists()  # stopped before opening the spool


@pytest.mark.real_icmp_preflight
def test_either_socket_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """ICMP-012: the ping socket, or the raw-socket fallback, lets the collector start.

    Args:
        monkeypatch: Chooses which socket opens.

    Returns:
        None

    Raises:
        None
    """
    for ping, raw in ((None, REFUSED), (REFUSED, None)):
        monkeypatch.setattr(preflight, "socket_problem", lambda value=ping: value)
        monkeypatch.setattr(preflight, "raw_socket_problem", lambda value=raw: value)
        assert preflight.check_icmp_capability() is None


class RefusedProber(ScriptedProber):
    """A prober whose host has just withdrawn ICMP permission."""

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Refuse every probe as the kernel does when ping_group_range excludes us.

        Args:
            request: The request.

        Returns:
            ProbeResult: Nothing sent, with ``PERMISSION_DENIED``.

        Raises:
            None
        """
        self.requests.append(request)
        return make_result(["n", "n"], request.target, fault=CollectorFault.PERMISSION_DENIED)


def test_losing_permission_while_running_stops_the_collector() -> None:
    """ICMP-012: a refused cycle calls the hard-stop hook before reporting the error.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    collector = IcmpCollector(RefusedProber(["n"]), IcmpSettings(probes=2))
    step = CollectorIcmpStep(collector, FaultTally())
    stops: list[str] = []
    step.on_permission_lost = lambda: stops.append("stop")
    with pytest.raises(IcmpNotSentError):
        asyncio.run(step.icmp(poll_ctx(make_target(make_device(1)))))
    assert stops == ["stop"]
    assert EX_PREFLIGHT == 78  # systemd's RestartPreventExitStatus keeps it stopped


def test_check_health_is_critical_but_does_not_roll_back_an_update(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """UPD-006: monitoring sees CRITICAL; the self-update gate sees a host-setting warning.

    Args:
        monkeypatch: Supplies a failing ICMP check and the updater's variable.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    refused = CheckResult("icmp", Status.CRITICAL, "unprivileged ICMP refused; fix: ...")
    monkeypatch.setattr(cmd_check, "run_checks", lambda: (refused,))
    monkeypatch.delenv(cmd_check.RETRY_ENV, raising=False)
    assert cmd_check.run_health(argparse.Namespace()) == Status.CRITICAL.exit_code
    assert capsys.readouterr().out.startswith("VAKR CRITICAL - icmp:")
    monkeypatch.setenv(cmd_check.RETRY_ENV, "0")
    assert cmd_check.run_health(argparse.Namespace()) == Status.WARNING.exit_code
    assert "an update rollback cannot fix it" in capsys.readouterr().out
