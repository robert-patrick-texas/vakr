"""Bookkeeping for the probes waiting on one socket: timers, futures, matching.

Each probe sent gets one future and one timer, and resolves exactly once:
a matched reply, a timeout, an ICMP error, or abandonment by the collector.
The RTT runs on the monotonic clock from just before ``sendto`` to the
reply's arrival: the read time, minus how long the kernel says the reply
sat unread (``SO_TIMESTAMPNS``), so event-loop lag does not inflate it. A
reply that arrives after its timeout counts as late even if the timer had
not fired yet, so no RTT ever exceeds the per-probe timeout. Replies and
errors that match nothing are counted and dropped (OBS-001).

Dependencies:
    asyncio: Futures and timers on the running loop.
    vakr.adapters.icmp: Tables, tallies, decoded messages.
    vakr.ports: ``Clock`` and the probe value types.

Safety:
    Bounded by the ``ProbeTable`` capacity. Pure bookkeeping: no I/O.

Example:
    >>> RTT_US_PER_S
    1000000
"""

import asyncio
from datetime import datetime

from vakr.adapters.icmp.base import Tally
from vakr.adapters.icmp.table import ProbeTable, Verdict, Waiting
from vakr.adapters.icmp.wire import Echo
from vakr.adapters.icmp.wire_errors import IcmpError
from vakr.ports.clock import Clock
from vakr.ports.icmp import CollectorFault, ProbeOutcome, ProbeSample

RTT_US_PER_S = 1_000_000
_DROPPED = {Verdict.LATE: "late", Verdict.DUPLICATE: "duplicate", Verdict.FOREIGN: "foreign"}


class ProbeBook:
    """Waiting probes of one socket and how each one ends."""

    def __init__(
        self, capacity: int, clock: Clock, tally: Tally, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Create an empty book.

        Args:
            capacity: Most probes that may wait at once.
            clock: Source of monotonic time for RTTs.
            tally: Shared running totals.
            loop: Loop that owns the futures and timers.

        Returns:
            None

        Raises:
            ValueError: If ``capacity`` is out of range.
        """
        self.table = ProbeTable(capacity)
        self._clock, self._tally, self._loop = clock, tally, loop
        self._seq_of: dict[asyncio.Future[ProbeSample], int] = {}

    def add(self, probe: Waiting, index: int, sent_at: datetime) -> asyncio.Future[ProbeSample]:
        """Start waiting for a probe that was just sent.

        Args:
            probe: The probe; its ``handle`` is filled in here.
            index: Position of the probe in its cycle.
            sent_at: Wall-clock UTC send time.

        Returns:
            asyncio.Future[ProbeSample]: Resolves when the probe ends.

        Raises:
            TableFullError: If the table is at capacity.
        """
        self.table.add(probe)
        future: asyncio.Future[ProbeSample] = self._loop.create_future()
        timer = self._loop.call_later(probe.timeout_s, self.expire, probe.seq)
        probe.handle = (future, timer, index, sent_at)
        self._seq_of[future] = probe.seq
        return future

    def resolve(
        self,
        probe: Waiting,
        outcome: ProbeOutcome,
        rtt_us: int = -1,
        *,
        fault: CollectorFault | None = None,
        detail: str | None = None,
    ) -> None:
        """Finish a waiting probe and resolve its future.

        Args:
            probe: The waiting probe.
            outcome: How it ended.
            rtt_us: RTT for ``REPLY``; -1 otherwise.
            fault: Collector reason for ``ABORTED``.
            detail: Operator-readable note.

        Returns:
            None

        Raises:
            None
        """
        self.table.finish(probe.seq, answered=outcome is ProbeOutcome.REPLY)
        future, timer, index, sent_at = probe.handle
        timer.cancel()
        self._seq_of.pop(future, None)
        if not future.done():
            future.set_result(ProbeSample(index, outcome, rtt_us, sent_at, fault, detail))

    def expire(self, seq: int) -> None:
        """Time out a probe that is still waiting.

        Args:
            seq: Its sequence number.

        Returns:
            None

        Raises:
            None
        """
        if (probe := self.table.get(seq)) is not None:
            self._tally.timeouts += 1
            self.resolve(probe, ProbeOutcome.TIMEOUT)

    def abandon(self, future: asyncio.Future[ProbeSample]) -> None:
        """Stop waiting for a probe whose cycle was cancelled.

        Args:
            future: Future returned by ``add``.

        Returns:
            None

        Raises:
            None
        """
        seq = self._seq_of.get(future)
        probe = None if seq is None else self.table.get(seq)
        if probe is not None:
            self.resolve(probe, ProbeOutcome.ABORTED, fault=CollectorFault.CANCELLED)

    def abort_all(self, why: str) -> None:
        """Abort every waiting probe because the socket is going away.

        Args:
            why: Operator-readable reason recorded on each probe.

        Returns:
            None

        Raises:
            None
        """
        for probe in self.table.waiting():
            self.resolve(probe, ProbeOutcome.ABORTED, fault=CollectorFault.SOCKET_ERROR, detail=why)

    def unsent(self, index: int, fault: CollectorFault, why: str) -> asyncio.Future[ProbeSample]:
        """Return a resolved future for a probe the collector could not send.

        Args:
            index: Probe position.
            fault: Collector-side reason.
            why: Operator-readable cause.

        Returns:
            asyncio.Future[ProbeSample]: Already resolved as ``NOT_SENT``.

        Raises:
            None
        """
        self._tally.not_sent += 1
        future: asyncio.Future[ProbeSample] = self._loop.create_future()
        future.set_result(ProbeSample(index, ProbeOutcome.NOT_SENT, fault=fault, detail=why))
        return future

    def on_reply(self, echo: Echo, source: bytes, unread_s: float | None = None) -> None:
        """Match an Echo reply to its probe, or count why it was dropped.

        Args:
            echo: Decoded reply.
            source: Packed sender address.
            unread_s: Seconds the reply waited in the socket before this
                read, from the kernel's arrival stamp; ``None`` if unknown.

        Returns:
            None

        Raises:
            None
        """
        verdict, probe = self.table.match_reply(echo.seq, echo.token, source)
        if probe is None:
            field = _DROPPED[verdict]
            setattr(self._tally, field, getattr(self._tally, field) + 1)
            return
        rtt_s = max(0.0, self._clock.monotonic() - probe.sent_mono)
        # An implausible stamp (a wall-clock step) is ignored rather than trusted.
        if unread_s is not None and 0.0 <= unread_s <= rtt_s:
            rtt_s -= unread_s
        if rtt_s > probe.timeout_s:  # read after its window, even if the timer had not fired
            self._tally.late += 1
            self._tally.timeouts += 1
            self.resolve(probe, ProbeOutcome.TIMEOUT)
            return
        self._tally.replies += 1
        self.resolve(probe, ProbeOutcome.REPLY, round(rtt_s * RTT_US_PER_S))

    def on_error(self, error: IcmpError) -> None:
        """Resolve the probe an ICMP or local error is about.

        A network error means the probe is lost, with a reason. A local
        error means the packet never left this host: collector evidence.

        Args:
            error: Decoded error.

        Returns:
            None

        Raises:
            None
        """
        probe = self.table.match_error(error.seq, error.token, error.target)
        if probe is None:
            self._tally.late += 1
            return
        if error.local:
            fault = CollectorFault.SEND_ERROR
            self.resolve(probe, ProbeOutcome.ABORTED, fault=fault, detail=error.reason)
            return
        self._tally.unreachable += 1
        detail = f"{error.reason} (reported by {error.reporter or 'an unknown router'})"
        self.resolve(probe, ProbeOutcome.UNREACHABLE, detail=detail)
