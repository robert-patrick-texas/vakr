"""GET exact OIDs in batches under the session's size limit (SNMP-014, SNMP-017).

Stage 1 and Stage 2 fetch hundreds of exact OIDs, and one PDU carries at
most ``max_varbinds`` of them. Batches go out one at a time, and each
outcome is handled so that whatever the agent did answer is kept:

- ``tooBig``: the size halves (E.4) and the same OIDs go out again in
  smaller batches.
- A batch unanswered after every attempt, from an agent that *has* answered
  in this session and with more than the floor: the size halves once and
  the batch is resent, because a large reply lost to fragmentation looks
  exactly like this (E.4). Otherwise its OIDs fail with ``TIMEOUT``.
- After ``abandon_after`` consecutive unanswered batches the rest is not
  sent (``ABANDONED``), so a device that stops answering halfway costs a few
  timeouts rather than one per batch.
- An error status such as ``genErr``: the varbind the agent named is
  excluded and the rest resent, up to ``MAX_EXCLUSIONS`` times per GET
  (E.23); after that the batch fails.

``noSuchObject`` and ``noSuchInstance`` are answers and are returned as such.

Dependencies:
    vakr.adapters.snmp.engine: Sends each batch with retries.
    vakr.ports.snmp: OIDs, varbinds, and results.

Safety:
    Every loop either consumes OIDs or shrinks the batch toward its floor,
    so a GET always ends.

Example:
    >>> MAX_EXCLUSIONS
    3
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from vakr.adapters.snmp import messages
from vakr.adapters.snmp.engine import RequestEngine
from vakr.adapters.snmp.pdu import Pdu, PduError, PduKind, Reply
from vakr.ports.snmp import GetResult, Oid, SnmpError, VarBind, require_oid
from vakr.ports.snmp.errors import TOO_BIG

MAX_EXCLUSIONS = 3  # E.23: varbinds dropped after error-status answers, per GET


@dataclass(slots=True)
class _State:
    """Progress of one GET.

    Attributes:
        pending: OIDs not yet answered or failed, in request order.
        answers: Varbinds received.
        failed: OIDs that will not be answered.
        error: The first failure.
        misses: Consecutive batches without an answer.
        timeout_reduced: Whether the one size reduction after a timeout was used.
        exclusions: Varbinds excluded after error-status answers.
    """

    pending: list[Oid]
    answers: dict[Oid, VarBind] = field(default_factory=dict)
    failed: list[Oid] = field(default_factory=list)
    error: SnmpError | None = None
    misses: int = 0
    timeout_reduced: bool = False
    exclusions: int = 0

    def fail(self, oids: Sequence[Oid], error: SnmpError) -> None:
        """Give up on ``oids``, remembering the first error.

        Args:
            oids: OIDs that will not be answered.
            error: Why.

        Returns:
            None

        Raises:
            None
        """
        dropped = set(oids)
        self.pending = [oid for oid in self.pending if oid not in dropped]
        self.failed.extend(oids)
        if self.error is None:
            self.error = error


async def get(engine: RequestEngine, oids: Sequence[Oid]) -> GetResult:
    """GET exact OIDs in as few requests as the size limit allows.

    Args:
        engine: The session's request engine.
        oids: OIDs to fetch; duplicates are fetched once.

    Returns:
        GetResult: Every varbind that arrived, in request order, the OIDs
        that did not, and the first error.

    Raises:
        ValueError: If ``oids`` is empty.
        ValidationError: If an OID is invalid.
    """
    requested = tuple(dict.fromkeys(require_oid(oid) for oid in oids))
    if not requested:
        raise ValueError("get() needs at least one OID")
    before = engine.stats
    state = _State(list(requested))
    while state.pending:
        await _step(engine, state)
    varbinds = tuple(state.answers[oid] for oid in requested if oid in state.answers)
    failed = set(state.failed)
    return GetResult(
        requested,
        varbinds,
        tuple(oid for oid in requested if oid in failed),
        state.error,
        engine.stats - before,
    )


async def _step(engine: RequestEngine, state: _State) -> None:
    """Send the next batch and apply its outcome.

    Args:
        engine: The session's request engine.
        state: Progress so far; updated in place.

    Returns:
        None

    Raises:
        None
    """
    if state.misses >= engine.settings.abandon_after:
        state.fail(list(state.pending), messages.abandoned(engine.label))
        return
    chunk = tuple(state.pending[: engine.max_varbinds])
    try:
        reply = await engine.exchange(Pdu(PduKind.GET, chunk))
    except PduError as failure:
        if failure.error.collector_side:
            # Nothing can be sent at all, so every remaining OID fails the same way.
            state.fail(list(state.pending), failure.error)
        else:
            state.misses = 0
            state.fail(chunk, failure.error)
        return
    if reply is None:
        _on_timeout(engine, state, chunk)
        return
    state.misses = 0
    if reply.status == 0:
        _on_answer(engine, state, chunk, reply)
    elif reply.status == TOO_BIG:
        if not engine.reduce():
            state.fail(chunk, messages.too_big(engine.label, len(chunk)))
    else:
        _on_error(engine, state, chunk, reply)


def _on_timeout(engine: RequestEngine, state: _State, chunk: tuple[Oid, ...]) -> None:
    """Handle a batch unanswered after every attempt.

    Args:
        engine: The session's request engine.
        state: Progress so far.
        chunk: The batch.

    Returns:
        None

    Raises:
        None
    """
    state.misses += 1
    floor = engine.settings.min_varbinds
    if engine.answered and len(chunk) > floor and not state.timeout_reduced and engine.reduce():
        state.timeout_reduced = True
        return
    settings = engine.settings
    state.fail(chunk, messages.timeout(engine.label, settings.timeout_s, settings.attempts))


def _on_answer(engine: RequestEngine, state: _State, chunk: tuple[Oid, ...], reply: Reply) -> None:
    """Record a ``noError`` answer, which must name exactly the requested OIDs.

    Args:
        engine: The session's request engine.
        state: Progress so far.
        chunk: The batch.
        reply: The answer.

    Returns:
        None

    Raises:
        None
    """
    names = tuple(vb.oid for vb in reply.varbinds)
    if names != chunk:
        state.fail(chunk, messages.malformed(engine.label, "the answer named other OIDs"))
        return
    state.answers.update(zip(chunk, reply.varbinds, strict=True))
    answered = set(chunk)
    state.pending = [oid for oid in state.pending if oid not in answered]


def _on_error(engine: RequestEngine, state: _State, chunk: tuple[Oid, ...], reply: Reply) -> None:
    """Handle an error-status answer, excluding the named varbind when possible.

    Args:
        engine: The session's request engine.
        state: Progress so far.
        chunk: The batch.
        reply: The answer.

    Returns:
        None

    Raises:
        None
    """
    index = reply.index
    named = chunk[index - 1] if 1 <= index <= len(chunk) else None
    error = messages.agent_error(engine.label, reply.status, index, named)
    if named is not None and len(chunk) > 1 and state.exclusions < MAX_EXCLUSIONS:
        # Keep the rest of the batch: one broken object must not cost the others (SNMP-014).
        state.exclusions += 1
        state.fail([named], error)
        return
    state.fail(chunk, error)
