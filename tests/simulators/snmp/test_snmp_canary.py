"""SEC-003, SNMP-019, invariant 9: a canary community never leaks anywhere.

One unique canary community is generated per test and used for real SNMP
traffic. Every path that could expose it is then searched: log records at
DEBUG from every logger (vakr, PySNMP, asyncio in debug mode), ``repr`` of
every adapter object and result, exception messages and tracebacks with
captured local variables, and CLI output. The agent also sends truncated
copies of each answer (which contain the community) and copies from a
stranger's socket, and PySNMP's packet debugging is switched on first, to
prove those paths are closed too. The search covers the plain text and the
hex forms a packet dump would use.

Dependencies:
    pytest: Log and output capture.
    tests.simulators.snmp: The fault agent and lab helpers.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmp_canary.py
"""

import asyncio
import logging
import traceback
from pathlib import Path

import pytest
from pysnmp import debug as snmp_debug

from tests.simulators.snmp.iosxe_dataset import device_records, router
from tests.simulators.snmp.simkit import StaticResolver, fake_community
from tests.simulators.snmp.simlab import FAST, lab, threaded_agent
from tests.unit.credentials.credentials_testkit import Install, v2c_table
from vakr.adapters.snmp import PysnmpSessionFactory, factory
from vakr.adapters.snmp.session import PysnmpSession
from vakr.cli.main import main
from vakr.collectors.snmp import plan_from_discovery, poll_device
from vakr.credentials import CredentialReference, SnmpV2cCredential
from vakr.discovery import discover
from vakr.domain.enums import SnmpVersion
from vakr.ports.snmp import SnmpError, SnmpErrorKind, SnmpSessionError, SnmpTarget


def forms(secret: str) -> list[str]:
    """Return the ways a community could appear in text or a packet dump.

    Args:
        secret: The canary.

    Returns:
        list[str]: Plain, bytes-repr, and hex forms.

    Raises:
        None
    """
    raw = secret.encode()
    return [
        secret,
        repr(raw),
        raw.hex(),
        raw.hex().upper(),
        " ".join(f"{b:02X}" for b in raw),
        " ".join(f"{b:02x}" for b in raw),
    ]


def assert_absent(secret: str, *texts: str) -> None:
    """Fail if any form of the secret appears in any text.

    Args:
        secret: The canary.
        *texts: Everything observed.

    Returns:
        None

    Raises:
        AssertionError: On a leak, naming the form but not the text.
    """
    blob = "\n".join(texts)
    for form in forms(secret):
        assert form not in blob, "canary community leaked"


def failure_texts(error: BaseException) -> list[str]:
    """Render an exception every way an operator or log could see it.

    Args:
        error: The exception.

    Returns:
        list[str]: ``str``, ``repr``, a formatted traceback, and a traceback
        with captured local variables.

    Raises:
        None
    """
    with_locals = traceback.TracebackException.from_exception(error, capture_locals=True)
    return [
        str(error),
        repr(error),
        "".join(traceback.format_exception(error)),
        "".join(with_locals.format()),
    ]


def test_canary_never_appears_in_logs_reprs_or_results(caplog: pytest.LogCaptureFixture) -> None:
    """SEC-003: real traffic, decoy datagrams, and PySNMP debugging leak nothing.

    Args:
        caplog: Captures every log record at DEBUG.

    Returns:
        None

    Raises:
        None
    """
    seen: list[str] = []

    async def scenario() -> None:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        async with lab([router(1)]) as sim:
            canary = sim.communities["rtr-sim-001"]
            device = sim.devices["rtr-sim-001"]
            device.faults.truncated_copies = 1
            device.faults.stranger_copy = True
            snmp_debug.set_logger(snmp_debug.Debug("all"))  # vakr must turn this off
            async with sim.factory.open(sim.target("rtr-sim-001"), FAST) as session:
                found = await discover(session, sim.factory._clock)
                polled = await poll_device(session, plan_from_discovery(found), sim.factory._clock)
                assert isinstance(session, PysnmpSession)
                transport = session._transport
                seen.extend([repr(sim.factory), repr(session), repr(transport)])
                seen.extend(
                    [repr(transport._dispatcher), repr(transport._auth), repr(found), repr(polled)]
                )
            seen.append(repr(session))  # after close, the credential is gone
            seen.append(canary)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario(), debug=True)
    canary = seen.pop()
    assert "undecodable" in caplog.text  # the decoys did reach the guard
    assert_absent(canary, caplog.text, *seen)


def test_canary_never_appears_in_failures(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-003: timeouts and every collector-side failure render without the community.

    Args:
        caplog: Captures every log record at DEBUG.
        monkeypatch: Breaks endpoint creation after the community is resolved.

    Returns:
        None

    Raises:
        None
    """
    # The canary lives only inside the resolver until the final check: a local
    # variable in the scenario would itself show up in captured-locals tracebacks.
    resolver = StaticResolver({"rtr-sim-001": fake_community("canary")})
    texts: list[str] = []

    async def scenario() -> None:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        async with lab([router(1)]) as sim:
            wrong = PysnmpSessionFactory(resolver, resolve_timeout_s=2)
            async with wrong.open(sim.target("rtr-sim-001"), FAST) as session:  # agent ignores it
                result = await session.get([(1, 3, 6, 1, 2, 1, 1, 3, 0)])
                texts.extend([repr(result), repr(session.stats)])
            port = sim.port
        targets = [
            SnmpTarget("127.0.0.1", "missing", port=port),
            SnmpTarget("127.0.0.1", "rtr-sim-001", port=port, version=SnmpVersion.V3),
            SnmpTarget("127.0.0.1", "rtr-sim-001", port=port, source_address="192.0.2.99"),
            SnmpTarget("no-such-host.invalid", "rtr-sim-001", port=port),
        ]
        for target in targets:
            with pytest.raises(SnmpSessionError) as caught:
                async with wrong.open(target, FAST):
                    pass
            texts.extend(failure_texts(caught.value))

        async def broken(*_: object) -> None:
            """Fail endpoint creation after the community has been resolved.

            Args:
                _: Ignored.

            Returns:
                None

            Raises:
                SnmpSessionError: Always.
            """
            raise SnmpSessionError(SnmpError(SnmpErrorKind.TRANSPORT, "endpoint failed"))

        monkeypatch.setattr(factory, "_endpoint", broken)
        with pytest.raises(SnmpSessionError) as caught:
            async with wrong.open(SnmpTarget("127.0.0.1", "rtr-sim-001", port=port), FAST):
                pass
        texts.extend(failure_texts(caught.value))  # its frames hold the wrapped community

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario(), debug=True)
    credential = resolver.resolve(CredentialReference("rtr-sim-001"))
    assert isinstance(credential, SnmpV2cCredential)
    assert_absent(credential.community.reveal(), caplog.text, *texts)


def test_canary_never_appears_in_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC-003, SNMP-019: CLI reports, errors, and refusals never print the community.

    Args:
        tmp_path: The relocated install.
        monkeypatch: Sets ``VAKR_ROOT``.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    canary = fake_community("canary")
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    Install.under(tmp_path).write(
        v2c_table("rtr-sim-001", canary) + v2c_table("other", fake_community())
    )
    outputs = []
    with threaded_agent({canary: device_records(router(1))}) as port:
        base = ["127.0.0.1", "--port", str(port), "--dev", "--timeout", "1", "--retries", "0"]
        assert main(["snmp", "discover", *base, "--credential", "rtr-sim-001"]) == 0
        assert main(["snmp", "poll", *base, "--credential", "rtr-sim-001"]) == 0
        assert main(["snmp", "discover", *base, "--credential", "other"]) == 4
        assert main(["snmp", "discover", *base, "--credential", "absent"]) == 5
        with pytest.raises(SystemExit):
            main(["snmp", "discover", *base, "--credential", "x", "--community", canary])
        captured = capsys.readouterr()
        outputs += [captured.out, captured.err]
    assert "13 interfaces discovered" in outputs[0]
    assert_absent(canary, *outputs)
