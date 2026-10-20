"""Walk one SNMP subtree with GETBULK, keeping every row received (DISC-008).

Discovery reads tables such as ``ifDescr`` or ``ipAddressIfIndex`` one
column at a time: each GETBULK asks for up to ``max_repetitions`` rows after
the last OID received, and the walk ends when the agent's answer leaves the
subtree or reaches the end of its MIB view. A walk also stops on a timeout,
an error status, an answer whose OIDs stop increasing (a looping agent), or
after ``max_walk_rows`` rows, and then reports why. Rows received before
the stop are kept, so one bad table never erases good data.

Dependencies:
    vakr.adapters.snmp.engine: Sends each request with retries.
    vakr.adapters.snmp.messages: Operator-language errors.
    vakr.ports.snmp: OIDs, varbinds, and walk results.

Safety:
    Every walk is bounded by ``max_walk_rows`` and by the per-request
    timeout and retry budget.

Example:
    >>> absorb((1, 3), (1, 3), (), [], 10)
    'an empty GETBULK answer'
"""

from vakr.adapters.snmp import messages
from vakr.adapters.snmp.engine import RequestEngine
from vakr.adapters.snmp.pdu import Pdu, PduError, PduKind, Reply
from vakr.ports.snmp import (
    EXCEPTION_KINDS,
    Oid,
    SnmpError,
    SnmpStats,
    VarBind,
    WalkResult,
    in_subtree,
    require_oid,
)
from vakr.ports.snmp.errors import TOO_BIG

END = "end"
"""``absorb`` result: the walk reached the end of the subtree."""
LIMIT = "limit"
"""``absorb`` result: the row bound was reached."""


async def walk(engine: RequestEngine, root: Oid) -> WalkResult:
    """Walk one subtree with GETBULK.

    Args:
        engine: The session's request engine.
        root: The subtree.

    Returns:
        WalkResult: The rows and, if the walk stopped early, why.

    Raises:
        ValidationError: If ``root`` is not a valid OID.
    """
    require_oid(root, "root")
    before = engine.stats
    rows: list[VarBind] = []
    cursor = root
    while True:
        reps = min(engine.settings.max_repetitions, engine.max_varbinds)
        try:
            reply = await engine.exchange(Pdu(PduKind.GETBULK, (cursor,), reps))
        except PduError as failure:
            return _walked(engine, root, rows, failure.error, before)
        if reply is not None and reply.status == TOO_BIG and engine.reduce():
            continue  # RFC 3416 says agents should truncate GETBULK; weak ones send tooBig
        if reply is None or reply.status:
            return _walked(engine, root, rows, _reply_error(engine, reply, reps), before)
        end = absorb(root, cursor, reply.varbinds, rows, engine.settings.max_walk_rows)
        if end is not None:
            error = None if end == END else _stop_error(engine, end)
            return _walked(engine, root, rows, error, before)
        cursor = rows[-1].oid


def _reply_error(engine: RequestEngine, reply: Reply | None, reps: int) -> SnmpError:
    """Turn a missing or error-status GETBULK reply into an error.

    Args:
        engine: The session's request engine.
        reply: The reply, or ``None`` after every attempt timed out.
        reps: Repetitions in the last request.

    Returns:
        SnmpError: ``TIMEOUT``, ``TOO_BIG`` at the size floor, or ``AGENT_ERROR``.

    Raises:
        None
    """
    settings = engine.settings
    if reply is None:
        return messages.timeout(engine.label, settings.timeout_s, settings.attempts)
    if reply.status == TOO_BIG:
        return messages.too_big(engine.label, reps)
    return messages.agent_error(engine.label, reply.status, reply.index, None)


def _stop_error(engine: RequestEngine, reason: str) -> SnmpError:
    """Turn an ``absorb`` stop reason into an error.

    Args:
        engine: The session's request engine.
        reason: ``LIMIT`` or a malformation description.

    Returns:
        SnmpError: ``LIMIT`` or ``MALFORMED``.

    Raises:
        None
    """
    if reason == LIMIT:
        return messages.walk_limit(engine.label, engine.settings.max_walk_rows)
    return messages.malformed(engine.label, reason)


def _walked(
    engine: RequestEngine,
    root: Oid,
    rows: list[VarBind],
    error: SnmpError | None,
    before: SnmpStats,
) -> WalkResult:
    """Build a walk result with the effort spent since ``before``.

    Args:
        engine: The session's request engine.
        root: The subtree.
        rows: Rows received.
        error: Why the walk stopped early, or ``None`` when complete.
        before: Stats snapshot taken when the walk started.

    Returns:
        WalkResult: The result.

    Raises:
        None
    """
    return WalkResult(root, tuple(rows), error is None, error, engine.stats - before)


def absorb(
    root: Oid, cursor: Oid, varbinds: tuple[VarBind, ...], rows: list[VarBind], limit: int
) -> str | None:
    """Append a GETBULK reply's in-subtree varbinds to ``rows``.

    Args:
        root: The subtree.
        cursor: The OID the request started after.
        varbinds: The reply's varbinds, in order.
        rows: Rows so far; extended in place.
        limit: Most rows allowed.

    Returns:
        str | None: ``END`` at the end of the subtree, another stop reason
        (``LIMIT`` or a malformation) to stop early, or ``None`` to go on.

    Raises:
        None
    """
    if not varbinds:
        return "an empty GETBULK answer"
    for vb in varbinds:
        if vb.kind in EXCEPTION_KINDS or not in_subtree(vb.oid, root):
            return END
        if vb.oid <= cursor:
            # A looping agent would otherwise keep the walk going until the row limit.
            return "OIDs out of order; the walk stopped to avoid a loop"
        if len(rows) >= limit:
            return LIMIT
        rows.append(vb)
        cursor = vb.oid
    return None
