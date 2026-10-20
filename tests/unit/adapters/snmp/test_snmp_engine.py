"""SNMP-T08, SNMP-T10, SNMP-012, SNMP-014: the request engine against a scripted transport.

A fake transport answers each PDU from a script, so retries, tooBig size
halving, size-related timeouts, abandonment, error-status exclusion, and walk
stop conditions are checked exactly and without sockets.

Dependencies:
    asyncio, pytest: The engine is async; tests run it with ``asyncio.run``.
    vakr.adapters.snmp: Engine, GET batching, and walks.

Example:
    $ uv run pytest tests/unit/adapters/snmp/test_snmp_engine.py
"""

import asyncio

import pytest

from tests.unit.adapters.snmp.snmp_fakes import COLUMN, FakeTransport, Script, answer, oids
from vakr.adapters.snmp import getbatch, walking
from vakr.adapters.snmp.engine import RequestEngine
from vakr.adapters.snmp.pdu import Pdu, PduError, PduKind, Reply
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import (
    Oid,
    SnmpError,
    SnmpErrorKind,
    SnmpSettings,
    ValueKind,
    VarBind,
)


def engine(script: Script, lock: asyncio.Lock | None = None, **settings: int) -> RequestEngine:
    """Build an engine over a fake transport.

    Args:
        script: The transport's script.
        lock: The device lock; a fresh one when ``None``.
        **settings: ``SnmpSettings`` overrides.

    Returns:
        RequestEngine: The engine; ``engine._transport`` is the fake.

    Raises:
        None
    """
    return RequestEngine(
        FakeTransport(script),
        SnmpSettings(**settings),
        lock or asyncio.Lock(),
        SystemClock(),
        "sim",
    )


def test_snmp_t08_timeout_then_complete_retry_is_counted() -> None:
    """SNMP-T08, SNMP-018: a timeout is followed by one complete retry, and both are counted.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    replies = iter([None, "answer"])
    e = engine(lambda pdu: None if next(replies) is None else answer(pdu))
    result = asyncio.run(getbatch.get(e, oids(3)))
    assert result.complete
    assert len(result.varbinds) == 3
    assert (result.stats.requests, result.stats.timeouts, result.stats.retries) == (2, 1, 1)
    assert result.stats.quality.name == "RETRIED"


def test_snmp_t08_unanswered_get_fails_after_its_budget() -> None:
    """SNMP-T08: every attempt timed out gives TIMEOUT, attempt count, and no data.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    e = engine(lambda _pdu: None, retries=2)
    result = asyncio.run(getbatch.get(e, oids(4)))
    assert result.varbinds == ()
    assert result.failed == tuple(oids(4))
    assert result.error is not None
    assert result.error.kind is SnmpErrorKind.TIMEOUT
    assert (result.stats.requests, result.stats.responses, result.stats.retries) == (3, 0, 2)
    assert "3 attempts" in result.error.message


def test_snmp_t10_too_big_halves_the_batch_and_keeps_it_reduced() -> None:
    """SNMP-T10, SNMP-017, E.4: tooBig halves 30 -> 15 -> 7; later GETs stay at 7.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    e = engine(lambda pdu: Reply(1, 0, ()) if len(pdu.oids) > 8 else answer(pdu))
    result = asyncio.run(getbatch.get(e, oids(20)))
    assert result.complete
    assert [vb.oid for vb in result.varbinds] == oids(20)
    assert e.max_varbinds == 7
    assert result.stats.size_reductions == 2
    later = asyncio.run(getbatch.get(e, oids(14)))
    assert later.stats.requests == 2
    assert later.stats.size_reductions == 0


def test_snmp_t10_too_big_at_the_floor_fails_with_status() -> None:
    """SNMP-T10: tooBig at the 5-varbind floor is an explicit TOO_BIG failure.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    e = engine(lambda _pdu: Reply(1, 0, ()))
    result = asyncio.run(getbatch.get(e, oids(6)))
    assert e.max_varbinds == 5
    assert len(result.failed) == 6
    assert result.error is not None
    assert result.error.kind is SnmpErrorKind.TOO_BIG
    assert result.error.status == 1


def test_timeout_that_suggests_size_trouble_halves_once() -> None:
    """E.4: an agent that answered before but drops a large request gets a smaller one.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    e = engine(lambda pdu: None if len(pdu.oids) > 15 else answer(pdu))
    asyncio.run(getbatch.get(e, oids(1)))  # the agent has answered in this session
    result = asyncio.run(getbatch.get(e, oids(20)))
    assert result.complete
    assert result.stats.size_reductions == 1
    fresh = engine(lambda pdu: None if len(pdu.oids) > 15 else answer(pdu))
    silent = asyncio.run(getbatch.get(fresh, oids(20)))
    assert not silent.complete
    assert silent.stats.size_reductions == 0


def test_abandon_after_two_unanswered_batches() -> None:
    """A device that stops answering costs two batches of attempts, not one per batch.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    e = engine(lambda _pdu: None)
    result = asyncio.run(getbatch.get(e, oids(100)))
    assert len(result.failed) == 100
    assert result.stats.requests == 4
    assert result.error is not None
    assert result.error.kind is SnmpErrorKind.TIMEOUT


def test_snmp_014_error_status_excludes_the_named_varbind() -> None:
    """SNMP-014: genErr on one varbind costs only that varbind, up to the exclusion bound.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    bad = (*COLUMN, 3)

    def script(pdu: Pdu) -> Reply:
        """Answer one PDU the way this test's agent would.

        Args:
            pdu: The request.

        Returns:
            Reply: What the assertions check.

        Raises:
            None
        """
        return Reply(5, pdu.oids.index(bad) + 1, ()) if bad in pdu.oids else answer(pdu)

    result = asyncio.run(getbatch.get(engine(script), oids(6)))
    assert result.failed == (bad,)
    assert len(result.varbinds) == 5
    assert result.error is not None
    assert result.error.code == "agent_error.genErr"
    always = asyncio.run(getbatch.get(engine(lambda _pdu: Reply(5, 1, ())), oids(6)))
    assert len(always.failed) == 6
    assert len(always.varbinds) == 0


@pytest.mark.parametrize(
    ("script", "kind"),
    [
        (lambda _pdu: Reply(0, 0, ()), SnmpErrorKind.MALFORMED),
        (lambda _pdu: Reply(5, 0, ()), SnmpErrorKind.AGENT_ERROR),
    ],
)
def test_unusable_answers_fail_the_batch(script: Script, kind: SnmpErrorKind) -> None:
    """An answer naming other OIDs, or an error without an index, fails its batch.

    Args:
        script: The transport's script.
        kind: The expected error kind.

    Returns:
        None

    Raises:
        None
    """
    result = asyncio.run(getbatch.get(engine(script), oids(3)))
    assert len(result.failed) == 3
    assert result.error is not None
    assert result.error.kind is kind


def test_collector_side_failure_fails_everything_and_is_not_an_answer() -> None:
    """Invariant 2: a socket failure is collector-side and never counts as an answer.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    def script(_pdu: Pdu) -> Reply:
        """Answer one PDU the way this test's agent would.

        Args:
            _pdu: The request (ignored).

        Returns:
            Reply: What the assertions check.

        Raises:
            PduError: Always.
        """
        raise PduError(SnmpError(SnmpErrorKind.TRANSPORT, "socket gone"))

    e = engine(script)
    result = asyncio.run(getbatch.get(e, oids(70)))
    assert len(result.failed) == 70
    assert result.stats.responses == 0
    assert not e.answered
    assert result.error is not None
    assert result.error.collector_side
    with pytest.raises(ValueError, match="at least one OID"):
        asyncio.run(getbatch.get(e, []))


def bulk(rows: int, step: int = 1, after: Oid | None = None) -> Script:
    """Script a GETBULK agent serving ``rows`` rows of ``COLUMN``, then another column.

    Args:
        rows: Rows in the column.
        step: OID increment; 0 makes a looping agent.
        after: First OID past the column; end of MIB when ``None``.

    Returns:
        Script: The script.

    Raises:
        None
    """

    def script(pdu: Pdu) -> Reply:
        """Answer one PDU the way this test's agent would.

        Args:
            pdu: The request.

        Returns:
            Reply: What the assertions check.

        Raises:
            None
        """
        start = pdu.oids[0][-1] if len(pdu.oids[0]) > len(COLUMN) else 0
        out = []
        for n in range(pdu.max_repetitions):
            index = start + (n + 1) * step
            if index > rows:
                tail = (
                    (after, ValueKind.INTEGER, 1)
                    if after
                    else (COLUMN, ValueKind.END_OF_MIB_VIEW, None)
                )
                out.append(VarBind(*tail))
                break
            out.append(VarBind((*COLUMN, index), ValueKind.INTEGER, index))
        return Reply(0, 0, tuple(out))

    return script


def test_walk_reads_a_column_to_its_end_in_bulk() -> None:
    """DISC-005: a column walk returns every row and stops at the next column.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    e = engine(bulk(57, after=(*COLUMN[:-1], 3, 1)))
    result = asyncio.run(walking.walk(e, COLUMN))
    assert result.complete
    assert len(result.varbinds) == 57
    assert result.stats.requests == 3


@pytest.mark.parametrize(
    ("script", "kind", "rows"),
    [
        (bulk(50, step=0), SnmpErrorKind.MALFORMED, 1),
        (lambda _pdu: Reply(0, 0, ()), SnmpErrorKind.MALFORMED, 0),
        (lambda _pdu: Reply(2, 1, ()), SnmpErrorKind.AGENT_ERROR, 0),
    ],
)
def test_walk_stops_on_broken_agents(script: Script, kind: SnmpErrorKind, rows: int) -> None:
    """A looping, empty, or erroring agent stops the walk with an explicit error.

    Args:
        script: The transport's script.
        kind: The expected error kind.
        rows: Rows expected before the stop.

    Returns:
        None

    Raises:
        None
    """
    result = asyncio.run(walking.walk(engine(script), COLUMN))
    assert not result.complete
    assert len(result.varbinds) == rows
    assert result.error is not None
    assert result.error.kind is kind


def test_walk_keeps_rows_received_before_a_timeout_or_limit() -> None:
    """DISC-008: rows that arrived before a timeout or the row limit are kept.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    serve = bulk(500)
    calls = iter(range(100))
    e = engine(lambda pdu: serve(pdu) if next(calls) < 2 else None)
    timed_out = asyncio.run(walking.walk(e, COLUMN))
    assert len(timed_out.varbinds) == 50
    assert timed_out.error is not None
    assert timed_out.error.kind is SnmpErrorKind.TIMEOUT
    limited = asyncio.run(walking.walk(engine(bulk(500), max_walk_rows=60), COLUMN))
    assert len(limited.varbinds) == 60
    assert limited.error is not None
    assert limited.error.kind is SnmpErrorKind.LIMIT


def test_walk_reduces_repetitions_after_too_big() -> None:
    """SNMP-017: a weak agent's tooBig on GETBULK reduces the repetitions.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    serve = bulk(40)
    e = engine(lambda pdu: Reply(1, 0, ()) if pdu.max_repetitions > 10 else serve(pdu))
    result = asyncio.run(walking.walk(e, COLUMN))
    assert result.complete
    assert len(result.varbinds) == 40
    assert e.max_varbinds == 7


def test_snmp_012_one_pdu_in_flight_per_device_across_sessions() -> None:
    """SNMP-012: two sessions sharing a device lock never overlap requests.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    lock = asyncio.Lock()
    transport = FakeTransport(answer, delay=0.01)
    engines = [
        RequestEngine(transport, SnmpSettings(max_varbinds=5), lock, SystemClock(), "sim")
        for _ in range(2)
    ]

    async def both() -> None:
        """Run two GETs that share one device lock at the same time.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        await asyncio.gather(*(getbatch.get(e, oids(20)) for e in engines))

    asyncio.run(both())
    assert transport.max_in_flight == 1
    assert len(transport.sent) == 8
    assert all(pdu.kind is PduKind.GET for pdu in transport.sent)
