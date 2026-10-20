"""An in-process asyncio SNMPv2c agent with deterministic fault injection.

snmpsim serves the dataset faithfully but cannot misbehave on cue. This agent
serves the same ``Record`` lists from the test's own event loop and can,
per device:

- answer ``tooBig`` above a varbind count, or silently drop large requests,
  as fragmenting agents do (SNMP-T10);
- drop requests (all, the next N, or those touching a subtree) to produce
  timeouts and partial walks (SNMP-T08, DISC-T04);
- answer an error status for a subtree (SNMP-T09);
- delay answers, to show the event loop stays free (SNMP-T12) and to
  measure how many requests a client keeps in flight (SNMP-012);
- send a truncated copy of each answer, or a copy from a stranger's socket,
  before the real one (the community-leak tests);
- restart, which resets ``sysUpTime`` and every counter, or change any
  value between polls (counter discontinuity).

Values with a rate advance with the agent's clock, so a test that passes a
manual clock gets exact counter values.

Dependencies:
    asyncio, bisect: The UDP endpoint and OID lookups.
    pyasn1, pysnmp.proto: SNMPv2c message encoding.
    tests.simulators.snmp.snmprec: ``Record``.

Example:
    >>> agent = SimAgent()
    >>> agent.add("generated-at-test-time", []).requests
    0
"""

import asyncio
import time
from collections.abc import Callable, Sequence
from typing import Any

from pyasn1.codec.ber import decoder, encoder
from pyasn1.error import PyAsn1Error
from pysnmp.proto.api import v2c as V2C  # noqa: N812

from tests.simulators.snmp.agent_device import AgentDevice
from tests.simulators.snmp.snmprec import Record


class SimAgent(asyncio.DatagramProtocol):
    """Serves simulated devices on one UDP port, selected by community.

    Attributes:
        devices: Simulated devices by community.
        stray: Datagrams that were not decodable SNMPv2c messages.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an agent with no devices.

        Args:
            clock: Seconds, used to advance values with a rate.

        Returns:
            None

        Raises:
            None
        """
        self.devices: dict[bytes, AgentDevice] = {}
        self.stray = 0
        self._clock = clock
        self._transport: asyncio.DatagramTransport | None = None
        self._stranger: asyncio.DatagramTransport | None = None

    def add(self, community: str, records: Sequence[Record]) -> AgentDevice:
        """Serve ``records`` to requests carrying ``community``.

        Args:
            community: Generated at test time; never a real community.
            records: The device's records.

        Returns:
            AgentDevice: The device, for fault injection and inspection.

        Raises:
            None
        """
        device = AgentDevice(records, self._clock)
        self.devices[community.encode()] = device
        return device

    async def start(self, host: str = "127.0.0.1") -> int:
        """Listen on an ephemeral UDP port.

        Args:
            host: Local address to bind.

        Returns:
            int: The port.

        Raises:
            OSError: If the socket cannot be bound.
        """
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, 0))
        stranger, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=(host, 0)
        )
        self._transport, self._stranger = transport, stranger
        return transport.get_extra_info("sockname")[1]

    def close(self) -> None:
        """Close both sockets.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for transport in (self._transport, self._stranger):
            if transport is not None:
                transport.close()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        """Answer one request, or misbehave as the device's faults say.

        Args:
            data: The datagram.
            addr: The sender.

        Returns:
            None

        Raises:
            None
        """
        try:
            message, _ = decoder.decode(data, asn1Spec=V2C.Message())
        except PyAsn1Error:
            self.stray += 1
            return
        device = self.devices.get(bytes(V2C.apiMessage.get_community(message)))
        if device is None:
            return  # a wrong community gets silence, as from a real agent
        answer = device.handle(message)
        if answer is None:
            return
        delay = device.faults.delay_s
        device.begin()
        if delay:
            asyncio.get_running_loop().call_later(delay, self._send, device, answer, addr)
        else:
            self._send(device, answer, addr)

    def _send(self, device: AgentDevice, answer: Any, addr: Any) -> None:
        """Encode and send an answer, preceded by any decoy datagrams.

        Args:
            device: The device answering.
            answer: The response message.
            addr: The requester.

        Returns:
            None

        Raises:
            None
        """
        device.end()
        if self._transport is None or self._transport.is_closing():
            return
        wire = encoder.encode(answer)
        for _ in range(device.faults.truncated_copies):
            self._transport.sendto(wire[:-3], addr)  # decodable version, broken PDU
        if device.faults.stranger_copy and self._stranger is not None:
            self._stranger.sendto(wire, addr)
        self._transport.sendto(wire, addr)
