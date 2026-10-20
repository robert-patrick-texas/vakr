r"""Bounded tables that match replies to probes and classify the rest.

Each socket keeps its waiting probes keyed by Echo sequence number. A reply
matches only if its sequence number, payload token, and source address all
agree with a waiting probe, because the kernel rewrites the identifier on
ping sockets and a stale or spoofed reply must never count as success.
Probes that finished recently are remembered, within a bound, so a reply
that arrives after its timeout is counted as *late* and a second reply as
a *duplicate*; anything else is *foreign*. All three are dropped.

Dependencies:
    collections, dataclasses, enum: Ordered bounded memory and value types.

Safety:
    Both tables are bounded: ``add`` refuses past ``capacity`` and the
    recent table forgets its oldest entry first. Pure and synchronous;
    the caller owns timers and futures.

Example:
    >>> table = ProbeTable(capacity=2)
    >>> seq = table.next_seq()
    >>> table.add(Waiting(seq, b"t" * 8, b"\x7f\x00\x00\x01", 0.0, 1.0, None))
    >>> table.match_reply(seq, b"t" * 8, b"\x7f\x00\x00\x01")[0].name
    'MATCHED'
    >>> table.finish(seq, answered=True) is not None, table.match_reply(seq, b"t" * 8, None)[0].name
    (True, 'DUPLICATE')
"""

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

SEQ_SPACE = 1 << 16


class Verdict(Enum):
    """How an arriving reply relates to the probes this socket sent."""

    MATCHED = auto()
    LATE = auto()  # its probe already timed out
    DUPLICATE = auto()  # its probe was already answered
    FOREIGN = auto()  # no probe of ours, or a source or token mismatch


@dataclass(slots=True)
class Waiting:
    """A probe waiting for its reply.

    Attributes:
        seq: Echo sequence number.
        token: Random payload token.
        target: Packed destination address.
        sent_mono: Monotonic send time in seconds.
        timeout_s: Per-probe timeout.
        handle: Caller data, such as the future and timer to resolve.
    """

    seq: int
    token: bytes
    target: bytes
    sent_mono: float
    timeout_s: float
    handle: Any


class TableFullError(Exception):
    """The pending table is at capacity; the probe must not be sent."""


class ProbeTable:
    """Waiting probes of one socket plus a bounded memory of finished ones."""

    def __init__(self, capacity: int, recent_capacity: int | None = None) -> None:
        """Create empty tables.

        Args:
            capacity: Most probes that may wait at once (at most 65,535).
            recent_capacity: Finished probes remembered for late and
                duplicate detection; defaults to ``2 * capacity``.

        Returns:
            None

        Raises:
            ValueError: If a capacity is outside its range.
        """
        if not 1 <= capacity < SEQ_SPACE:
            raise ValueError(f"capacity must be 1-{SEQ_SPACE - 1}")
        self.capacity = capacity
        self.recent_capacity = 2 * capacity if recent_capacity is None else recent_capacity
        if self.recent_capacity < 1:
            raise ValueError("recent_capacity must be positive")
        self._waiting: dict[int, Waiting] = {}
        self._recent: OrderedDict[tuple[int, bytes], bool] = OrderedDict()
        self._next = 0

    def __len__(self) -> int:
        """Return the number of waiting probes.

        Args:
            None

        Returns:
            int: Probes waiting for a reply.

        Raises:
            None
        """
        return len(self._waiting)

    def next_seq(self) -> int:
        """Return a sequence number no waiting probe uses.

        Args:
            None

        Returns:
            int: 0-65535. Numbers advance and wrap, so a recent number is
            reused as late as possible.

        Raises:
            TableFullError: If the table is at capacity.
        """
        if len(self._waiting) >= self.capacity:
            raise TableFullError
        # Capacity < 2^16 guarantees a free number within capacity + 1 steps.
        while self._next in self._waiting:
            self._next = (self._next + 1) % SEQ_SPACE
        seq, self._next = self._next, (self._next + 1) % SEQ_SPACE
        return seq

    def add(self, probe: Waiting) -> None:
        """Start waiting for a probe's reply.

        Args:
            probe: The probe just sent; its ``seq`` came from ``next_seq``.

        Returns:
            None

        Raises:
            TableFullError: If the table is at capacity.
            ValueError: If the sequence number is already waiting.
        """
        if len(self._waiting) >= self.capacity:
            raise TableFullError
        if probe.seq in self._waiting:
            raise ValueError(f"sequence {probe.seq} is already waiting")
        self._waiting[probe.seq] = probe

    def get(self, seq: int) -> Waiting | None:
        """Return the waiting probe with this sequence number.

        Args:
            seq: Echo sequence number.

        Returns:
            Waiting | None: The probe, or ``None``.

        Raises:
            None
        """
        return self._waiting.get(seq)

    def waiting(self) -> tuple[Waiting, ...]:
        """Return every waiting probe.

        Args:
            None

        Returns:
            tuple[Waiting, ...]: A snapshot, in send order.

        Raises:
            None
        """
        return tuple(self._waiting.values())

    def match_reply(
        self, seq: int, token: bytes | None, source: bytes | None
    ) -> tuple[Verdict, Waiting | None]:
        """Classify an Echo reply without changing the tables.

        Args:
            seq: Reply sequence number.
            token: Token from the reply payload, if present.
            source: Packed source address of the reply.

        Returns:
            tuple[Verdict, Waiting | None]: The verdict, and the waiting
            probe when it is ``MATCHED``.

        Raises:
            None
        """
        probe = self._waiting.get(seq)
        if probe is not None and token == probe.token and source == probe.target:
            return Verdict.MATCHED, probe
        answered = None if token is None else self._recent.get((seq, token))
        if answered is None:
            return Verdict.FOREIGN, None
        return (Verdict.DUPLICATE if answered else Verdict.LATE), None

    def match_error(self, seq: int, token: bytes | None, target: bytes | None) -> Waiting | None:
        """Find the waiting probe an ICMP error is about.

        The token is checked when the error quoted it; routers that quote
        only 8 bytes leave the sequence number and destination to decide.

        Args:
            seq: Sequence number of the quoted request.
            token: Quoted token, if any.
            target: Packed destination of the quoted request, if known.

        Returns:
            Waiting | None: The probe, or ``None`` if nothing matches.

        Raises:
            None
        """
        probe = self._waiting.get(seq)
        if probe is None or (token is not None and token != probe.token):
            return None
        return probe if target is None or target == probe.target else None

    def finish(self, seq: int, *, answered: bool) -> Waiting | None:
        """Stop waiting for a probe and remember how it ended.

        Args:
            seq: Sequence number of the probe.
            answered: True for a matched reply; False for a timeout, an ICMP
                error, or an abandoned probe.

        Returns:
            Waiting | None: The probe, or ``None`` if it was not waiting.

        Raises:
            None
        """
        probe = self._waiting.pop(seq, None)
        if probe is not None:
            self._recent[(probe.seq, probe.token)] = answered
            while len(self._recent) > self.recent_capacity:
                self._recent.popitem(last=False)
        return probe
