"""The SNMP request engine: complete-attempt retries, size adaptation, and accounting.

Every request goes through ``RequestEngine.exchange``, which sends one PDU
at a time while holding the device's lock, so a device never has more than
one request outstanding (SNMP-012). A timeout is followed by a *complete*
new attempt with a fresh request ID, up to ``SnmpSettings.retries`` times,
and every attempt is counted (SNMP-011, SNMP-018). After ``tooBig`` the
request size halves, down to the E.4 floor, and stays reduced for the rest
of the session (SNMP-017).

GET batching lives in ``vakr.adapters.snmp.getbatch`` and subtree walks in
``vakr.adapters.snmp.walking``; both send through this engine.

Dependencies:
    asyncio: The per-device lock.
    vakr.adapters.snmp.pdu: The transport seam.
    vakr.ports.clock, vakr.ports.snmp: Monotonic time and port types.

Safety:
    The engine never sees a credential; the transport holds it.

Example:
    >>> RequestEngine.__init__.__doc__.splitlines()[0]
    'Create an engine for one session.'
"""

import asyncio
from dataclasses import dataclass

from vakr.adapters.snmp.pdu import Pdu, PduError, PduTransport, Reply
from vakr.ports.clock import Clock
from vakr.ports.snmp import SnmpSettings, SnmpStats


@dataclass(slots=True)
class _Counters:
    """Mutable effort counters behind ``RequestEngine.stats``.

    Attributes:
        requests: PDUs sent.
        responses: PDUs answered.
        timeouts: Attempts without an answer.
        retries: Attempts after a timeout.
        size_reductions: Request-size halvings.
        elapsed_s: Seconds spent waiting.
    """

    requests: int = 0
    responses: int = 0
    timeouts: int = 0
    retries: int = 0
    size_reductions: int = 0
    elapsed_s: float = 0.0


class RequestEngine:
    """Sends requests for one session with retries, size limits, and accounting.

    Attributes:
        settings: The request budget.
        label: The agent as ``address:port``, for messages.
        answered: Whether the agent has answered anything in this session;
            only then can a timeout suggest a request that was too large.
    """

    def __init__(
        self,
        transport: PduTransport,
        settings: SnmpSettings,
        lock: asyncio.Lock,
        clock: Clock,
        label: str,
    ) -> None:
        """Create an engine for one session.

        Args:
            transport: Sends single PDU attempts.
            settings: The request budget.
            lock: The device's lock, shared by every session to that device.
            clock: Monotonic time for the elapsed count.
            label: The agent as ``address:port``.

        Returns:
            None

        Raises:
            None
        """
        self.settings = settings
        self.label = label
        self._transport = transport
        self._lock = lock
        self._clock = clock
        self._batch = settings.max_varbinds
        self._counts = _Counters()
        self.answered = False

    @property
    def max_varbinds(self) -> int:
        """Return the current varbinds-per-request limit.

        Args:
            None

        Returns:
            int: Between ``min_varbinds`` and ``max_varbinds``.

        Raises:
            None
        """
        return self._batch

    @property
    def stats(self) -> SnmpStats:
        """Return a snapshot of the effort so far.

        Args:
            None

        Returns:
            SnmpStats: Cumulative counts.

        Raises:
            None
        """
        c = self._counts
        return SnmpStats(
            c.requests, c.responses, c.timeouts, c.retries, c.size_reductions, c.elapsed_s
        )

    def reduce(self) -> bool:
        """Halve the request size after tooBig or a size-related timeout (E.4).

        Args:
            None

        Returns:
            bool: False when the size is already at the floor.

        Raises:
            None
        """
        floor = self.settings.min_varbinds
        if self._batch <= floor:
            return False
        self._batch = max(floor, self._batch // 2)
        self._counts.size_reductions += 1
        return True

    async def exchange(self, pdu: Pdu) -> Reply | None:
        """Send ``pdu`` with complete-attempt retries (SNMP-011, SNMP-018).

        Args:
            pdu: The request.

        Returns:
            Reply | None: The first reply, or ``None`` after every attempt
            timed out.

        Raises:
            PduError: When the transport fails or the reply is unusable.
        """
        counts = self._counts
        for attempt in range(self.settings.attempts):
            counts.requests += 1
            if attempt:
                counts.retries += 1
            # One PDU in flight per device: weak agents drop or time out
            # parallel requests (SNMP-012).
            async with self._lock:
                started = self._clock.monotonic()
                try:
                    reply = await self._transport.send(pdu, self.settings.timeout_s)
                except PduError as failure:
                    if not failure.error.collector_side:
                        counts.responses += 1
                        self.answered = True
                    raise
                finally:
                    counts.elapsed_s += self._clock.monotonic() - started
            if reply is not None:
                counts.responses += 1
                self.answered = True
                return reply
            counts.timeouts += 1
        return None
