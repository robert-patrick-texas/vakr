"""The ``IcmpProber`` adapter: unprivileged ICMP over shared, bounded sockets.

``SocketIcmpProber.probe`` runs one cycle: it sends ``count`` probes at
send times anchored to the cycle start (so lateness never accumulates),
waits for each reply or timeout, and returns every outcome. Probes to all
targets share one socket per (address family, source). Limits, all from
Appendix E.22:

- a global cap on probes in flight; a probe that cannot get a slot in time
  is ``NOT_SENT``/``OVERLOADED`` collector evidence, never packet loss;
- a cap on open sockets (``ChannelPool``);
- a pending-reply table per socket no larger than the in-flight cap;
- a per-cycle deadline: a probe is sent only if its whole timeout fits.

If the kernel drops replies on a full socket buffer during a cycle, the
cycle is marked ``RECEIVE_OVERFLOW`` so its losses read as collector
trouble, not target loss (invariant 2, OBS-003).

Dependencies:
    asyncio: Sleeps, timeouts, and the in-flight semaphore.
    vakr.adapters.icmp: Channel pool and sockets.
    vakr.ports: ``Clock`` and the ``IcmpProber`` contract.

Safety:
    Bounded everywhere; ``aclose`` (or ``async with``) aborts waiting
    probes and closes every socket, also on cancellation.

Example:
    >>> SocketIcmpProber(max_in_flight=16).counters().sockets_open
    0
"""

import asyncio
import ipaddress
from dataclasses import asdict
from types import TracebackType

from vakr.adapters.icmp.base import SocketOpenError, Tally
from vakr.adapters.icmp.channel import Channel
from vakr.adapters.icmp.pool import ChannelPool, Opener
from vakr.adapters.icmp.sockets import open_socket
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.icmp import (
    CollectorFault,
    IcmpCounters,
    ProbeOutcome,
    ProbeRequest,
    ProbeResult,
    ProbeSample,
)

DEFAULT_MAX_IN_FLIGHT = 1024
MAX_IN_FLIGHT_LIMIT = 16384
DEFAULT_MAX_SOCKETS = 8
MAX_SOCKETS_LIMIT = 64
_BEHIND = "too many probes in flight, or the collector fell behind schedule"


class SocketIcmpProber:
    """Runs ICMP probe cycles over shared non-blocking sockets.

    A prober belongs to the event loop that first uses it; create one per loop.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        max_in_flight: int = DEFAULT_MAX_IN_FLIGHT,
        max_sockets: int = DEFAULT_MAX_SOCKETS,
        opener: Opener = open_socket,
        raw_fallback: bool = True,
    ) -> None:
        """Create a prober; sockets open lazily on first use.

        Args:
            clock: Wall-clock and monotonic time; the system clock by default.
            max_in_flight: Most probes waiting for replies at once.
            max_sockets: Most sockets open at once.
            opener: Socket factory, replaced by a fake in unit tests.
            raw_fallback: Try a raw socket when a datagram socket is refused.

        Returns:
            None

        Raises:
            ValueError: If a limit is outside its range.
        """
        if not 1 <= max_in_flight <= MAX_IN_FLIGHT_LIMIT:
            raise ValueError(f"max_in_flight must be 1-{MAX_IN_FLIGHT_LIMIT}")
        if not 1 <= max_sockets <= MAX_SOCKETS_LIMIT:
            raise ValueError(f"max_sockets must be 1-{MAX_SOCKETS_LIMIT}")
        self._clock = clock or SystemClock()
        self._tally = Tally()
        self._slots = asyncio.Semaphore(max_in_flight)
        self._pool = ChannelPool(
            max_sockets=max_sockets,
            capacity=max_in_flight,
            clock=self._clock,
            tally=self._tally,
            opener=opener,
            raw_fallback=raw_fallback,
        )

    async def __aenter__(self) -> SocketIcmpProber:
        """Enter the async context.

        Args:
            None

        Returns:
            SocketIcmpProber: This prober.

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
        """Close every socket on the way out, whatever happened.

        Args:
            exc_type: Exception type, if any.
            exc: Exception, if any.
            tb: Traceback, if any.

        Returns:
            None

        Raises:
            None
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Abort waiting probes and close every socket.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._pool.close_all()

    def counters(self) -> IcmpCounters:
        """Return a snapshot of the running totals.

        Args:
            None

        Returns:
            IcmpCounters: Current values.

        Raises:
            None
        """
        self._tally.sockets_open = self._pool.open_count()
        return IcmpCounters(**asdict(self._tally))

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Run one cycle and return every probe's outcome.

        Args:
            request: Target, count, spacing, timeout, and optional source.

        Returns:
            ProbeResult: One sample per planned probe; ``fault`` names the
            first collector problem, if any.

        Raises:
            asyncio.CancelledError: If the caller cancels; waiting probes
                are abandoned and their slots released first.
        """
        started_at, t0 = self._clock.now_utc(), self._clock.monotonic()
        try:
            channel = self._pool.get(ipaddress.ip_address(request.target).version, request.source)
        except SocketOpenError as exc:
            self._tally.not_sent += request.count
            samples = tuple(
                ProbeSample(i, ProbeOutcome.NOT_SENT, fault=exc.fault, detail=exc.reason)
                for i in range(request.count)
            )
            return ProbeResult(request, started_at, 0.0, samples, exc.fault)
        drops_before = channel.drops()
        samples = await self._run(channel, request)
        fault = next((s.fault for s in samples if s.fault is not None), None)
        drops_after = channel.drops()
        if fault is None and drops_before is not None and drops_after is not None:
            # Replies dropped by our own full buffer would otherwise read as loss.
            fault = CollectorFault.RECEIVE_OVERFLOW if drops_after > drops_before else None
        return ProbeResult(request, started_at, self._clock.monotonic() - t0, samples, fault)

    async def _run(self, channel: Channel, request: ProbeRequest) -> tuple[ProbeSample, ...]:
        """Send the probes on schedule and collect their outcomes.

        Args:
            channel: Socket to send on.
            request: The cycle.

        Returns:
            tuple[ProbeSample, ...]: One sample per planned probe.

        Raises:
            asyncio.CancelledError: If the caller cancels.
        """
        loop = asyncio.get_running_loop()
        start = loop.time()
        latest = start + request.deadline_s - request.timeout_s
        samples: list[ProbeSample | None] = [None] * request.count
        futures: dict[int, asyncio.Future[ProbeSample]] = {}
        try:
            for i in range(request.count):
                if (delay := start + i * request.spacing_s - loop.time()) > 0:
                    await asyncio.sleep(delay)
                if not await self._acquire(loop, latest):
                    self._tally.not_sent += 1
                    fault = CollectorFault.OVERLOADED
                    samples[i] = ProbeSample(i, ProbeOutcome.NOT_SENT, fault=fault, detail=_BEHIND)
                    continue
                futures[i] = channel.send(i, request.target, request.timeout_s)
                futures[i].add_done_callback(self._release)
            for i, future in futures.items():
                samples[i] = await future
        finally:
            for future in futures.values():
                channel.book.abandon(future)
        return tuple(s for s in samples if s is not None)

    async def _acquire(self, loop: asyncio.AbstractEventLoop, latest: float) -> bool:
        """Take an in-flight slot, waiting no later than ``latest``.

        Args:
            loop: The running loop.
            latest: Loop time after which the probe may not be sent.

        Returns:
            bool: True with a slot held; False when the probe must not be sent.

        Raises:
            asyncio.CancelledError: If the caller cancels.
        """
        remaining = latest - loop.time()
        if remaining < 0:
            return False
        if self._slots.locked():
            try:
                async with asyncio.timeout(remaining):
                    await self._slots.acquire()
            except TimeoutError:
                return False
        else:
            await self._slots.acquire()  # returns at once: a slot is free
        self._tally.in_flight += 1
        self._tally.in_flight_peak = max(self._tally.in_flight_peak, self._tally.in_flight)
        return True

    def _release(self, _future: asyncio.Future[ProbeSample]) -> None:
        """Give back the slot of a probe that ended.

        Args:
            _future: The probe's future (unused).

        Returns:
            None

        Raises:
            None
        """
        self._tally.in_flight -= 1
        self._slots.release()
