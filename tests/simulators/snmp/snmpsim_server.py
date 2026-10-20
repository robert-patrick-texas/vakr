"""Run the real snmpsim responder over simulator records (ADR-034).

snmpsim picks a data file by community: a request with community ``X`` is
answered from ``X.snmprec``. The records are written under a temporary
directory with communities generated at test time, so no community is ever
committed or placed on a command line (the command line names only the
directory). The responder listens on 127.0.0.1 and is stopped when the
context exits.

Dependencies:
    subprocess, socket: The responder process and a free port.
    snmpsim: The ``snmpsim-command-responder`` entry point (dev dependency).
    tests.simulators.snmp.snmprec: ``write_snmprec``.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmpsim_discovery.py
"""

import socket
import subprocess
import sys
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import rfc1902
from pysnmp.proto.api import v2c as V2C  # noqa: N812

from tests.simulators.snmp.simtypes import DeviceSpec
from tests.simulators.snmp.snmprec import Record, write_snmprec

READY_TIMEOUT_S = 60.0
"""How long snmpsim may take to index its data files and answer."""
SYS_UPTIME = (1, 3, 6, 1, 2, 1, 1, 3, 0)


@dataclass(frozen=True)
class SnmpsimFleet:
    """Devices served by one running snmpsim.

    Attributes:
        port: The UDP port on 127.0.0.1.
        communities: Generated community by device name.
        specs: The devices.
    """

    port: int
    communities: dict[str, str]
    specs: tuple[DeviceSpec, ...]


def responder_path() -> Path:
    """Return the snmpsim responder installed next to this interpreter.

    Args:
        None

    Returns:
        Path: ``snmpsim-command-responder`` in the virtual environment.

    Raises:
        None
    """
    return Path(sys.executable).with_name("snmpsim-command-responder")


def free_udp_port() -> int:
    """Return a UDP port on 127.0.0.1 that is free right now.

    Args:
        None

    Returns:
        int: The port.

    Raises:
        OSError: If no socket can be bound.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _answers(port: int, community: str) -> bool:
    """Send one raw GET for ``sysUpTime.0`` and report whether it was answered.

    Args:
        port: The responder's port.
        community: A community with a data file.

    Returns:
        bool: True when a response arrived within 0.5 s.

    Raises:
        None
    """
    message = V2C.Message()
    V2C.apiMessage.set_defaults(message)
    V2C.apiMessage.set_community(message, community)
    pdu = V2C.GetRequestPDU()
    V2C.apiPDU.set_defaults(pdu)
    V2C.apiPDU.set_varbinds(pdu, [(rfc1902.ObjectName(SYS_UPTIME), V2C.null)])
    V2C.apiMessage.set_pdu(message, pdu)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.settimeout(0.5)
        probe.sendto(encoder.encode(message), ("127.0.0.1", port))
        try:
            data = probe.recv(65535)
        except TimeoutError:
            return False
    decoder.decode(data, asn1Spec=V2C.Message())
    return True


@contextmanager
def snmpsim(workdir: Path, devices: Mapping[str, Sequence[Record]]) -> Generator[int]:
    """Serve each community's records with snmpsim until the context exits.

    Args:
        workdir: An empty temporary directory.
        devices: Records by community.

    Returns:
        Generator[int]: Yields the UDP port on 127.0.0.1.

    Raises:
        RuntimeError: If the responder does not answer within 60 seconds.
    """
    data = workdir / "data"
    data.mkdir()
    for community, records in devices.items():
        write_snmprec(data / f"{community}.snmprec", records)
    port = free_udp_port()
    argv = [
        str(responder_path()),
        f"--data-dir={data}",
        f"--cache-dir={workdir / 'cache'}",
        f"--agent-udpv4-endpoint=127.0.0.1:{port}",
        "--log-level=error",
    ]
    with (workdir / "snmpsim.log").open("wb") as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
    try:
        _wait_ready(process, port, next(iter(devices)))
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _wait_ready(process: subprocess.Popen[bytes], port: int, community: str) -> None:
    """Wait until the responder answers, or fail.

    Args:
        process: The responder.
        port: Its port.
        community: A community it serves.

    Returns:
        None

    Raises:
        RuntimeError: If it exits or stays silent for ``READY_TIMEOUT_S``.
    """
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("snmpsim exited during startup; see snmpsim.log")
        if _answers(port, community):
            return
    raise RuntimeError("snmpsim did not answer within 60 s")
