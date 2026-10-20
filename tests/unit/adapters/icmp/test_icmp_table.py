"""Tests for reply matching and the bounded pending/recent tables (ICMP-003, OBS-001).

A reply counts only when sequence number, payload token, and source all
match a waiting probe; late, duplicate, and foreign replies are classified
and dropped. Both tables stay within their bounds whatever arrives.

Dependencies:
    pytest, hypothesis: Cases and generated operation sequences.
    vakr.adapters.icmp.table: The tables under test.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_table.py
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vakr.adapters.icmp.table import SEQ_SPACE, ProbeTable, TableFullError, Verdict, Waiting

PROPERTY = settings(derandomize=True, database=None, max_examples=200, deadline=None)
HOST = b"\xc0\x00\x02\x01"
OTHER = b"\xc0\x00\x02\x02"


def waiting(table: ProbeTable, token: bytes = b"t" * 8, target: bytes = HOST) -> Waiting:
    """Add one waiting probe with the next free sequence number.

    Args:
        table: Table to add to.
        token: Its token.
        target: Its packed destination.

    Returns:
        Waiting: The probe.

    Raises:
        TableFullError: If the table is full.
    """
    probe = Waiting(table.next_seq(), token, target, 0.0, 1.0, None)
    table.add(probe)
    return probe


def test_reply_needs_sequence_token_and_source_to_match() -> None:
    """A stale or spoofed reply never counts as success.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(4)
    probe = waiting(table)
    assert table.match_reply(probe.seq, b"x" * 8, HOST) == (Verdict.FOREIGN, None)
    assert table.match_reply(probe.seq, probe.token, OTHER) == (Verdict.FOREIGN, None)
    assert table.match_reply(probe.seq, None, HOST) == (Verdict.FOREIGN, None)
    assert table.match_reply(probe.seq, probe.token, HOST) == (Verdict.MATCHED, probe)


def test_finished_probes_classify_later_replies_as_late_or_duplicate() -> None:
    """OBS-001: a reply after the timeout is late; a second reply is a duplicate.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(4)
    answered, timed_out = waiting(table, b"a" * 8), waiting(table, b"b" * 8)
    assert table.finish(answered.seq, answered=True) is answered
    assert table.finish(timed_out.seq, answered=False) is timed_out
    assert table.finish(timed_out.seq, answered=False) is None
    assert table.match_reply(answered.seq, answered.token, HOST)[0] is Verdict.DUPLICATE
    assert table.match_reply(timed_out.seq, timed_out.token, HOST)[0] is Verdict.LATE
    assert len(table) == 0


def test_recent_memory_is_bounded_and_forgets_oldest_first() -> None:
    """The memory of finished probes cannot grow without bound.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(2, recent_capacity=2)
    first = waiting(table, b"1" * 8)
    table.finish(first.seq, answered=True)
    for token in (b"2" * 8, b"3" * 8):
        table.finish(waiting(table, token).seq, answered=True)
    assert table.match_reply(first.seq, first.token, HOST)[0] is Verdict.FOREIGN


def test_capacity_is_enforced_and_sequence_numbers_never_collide() -> None:
    """The pending table refuses past its capacity; waiting numbers are unique.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(3)
    probes = [waiting(table, bytes([i]) * 8) for i in range(3)]
    assert len({p.seq for p in probes}) == 3
    with pytest.raises(TableFullError):
        table.next_seq()
    with pytest.raises(TableFullError):
        table.add(Waiting(99, b"z" * 8, HOST, 0.0, 1.0, None))
    table.finish(probes[1].seq, answered=False)
    with pytest.raises(ValueError, match="already waiting"):
        table.add(Waiting(probes[0].seq, b"z" * 8, HOST, 0.0, 1.0, None))
    assert table.waiting() == (probes[0], probes[2])


def test_sequence_numbers_wrap_and_skip_waiting_ones() -> None:
    """Numbers wrap at 2^16 and skip any still waiting, so 65,535 can wait at once.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(2)
    old = Waiting(0, b"o" * 8, HOST, 0.0, 1.0, None)
    table.add(old)
    table._next = SEQ_SPACE - 1  # simulate a long-running socket about to wrap
    assert table.next_seq() == SEQ_SPACE - 1
    assert table.next_seq() == 1  # 0 is still waiting


@pytest.mark.parametrize(
    ("capacity", "recent"), [(0, None), (SEQ_SPACE, None), (1, 0)], ids=["zero", "2^16", "recent"]
)
def test_capacities_are_checked(capacity: int, recent: int | None) -> None:
    """Capacities outside the sequence space are refused.

    Args:
        capacity: Pending capacity.
        recent: Recent capacity.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="capacity"):
        ProbeTable(capacity, recent)


def test_errors_match_by_sequence_and_destination_when_the_token_was_cut() -> None:
    """Routers often quote only 8 bytes; the sequence and destination then decide.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(4)
    probe = waiting(table)
    assert table.match_error(probe.seq, None, HOST) is probe
    assert table.match_error(probe.seq, None, None) is probe
    assert table.match_error(probe.seq, probe.token, HOST) is probe
    assert table.match_error(probe.seq, b"q" * 8, HOST) is None
    assert table.match_error(probe.seq, None, OTHER) is None
    assert table.match_error(probe.seq + 1, None, HOST) is None


@PROPERTY
@given(
    st.lists(
        st.tuples(st.sampled_from(["send", "reply", "expire"]), st.integers(0, 40)), max_size=200
    )
)
def test_any_sequence_of_events_keeps_tables_bounded_and_answers_once(
    events: list[tuple[str, int]],
) -> None:
    """Property: bounds hold, and each probe is answered at most once.

    Args:
        events: Sends, replies, and expiries for probe numbers.

    Returns:
        None

    Raises:
        None
    """
    table = ProbeTable(8, recent_capacity=16)
    sent: list[Waiting] = []
    answered: set[int] = set()
    for action, n in events:
        if action == "send":
            if len(table) < table.capacity:
                sent.append(waiting(table, n.to_bytes(8, "big") + b"", HOST))
            continue
        if not sent:
            continue
        probe = sent[n % len(sent)]
        if action == "reply":
            verdict, match = table.match_reply(probe.seq, probe.token, HOST)
            if verdict is Verdict.MATCHED:
                assert match is probe
                assert id(probe) not in answered
                answered.add(id(probe))
                table.finish(probe.seq, answered=True)
        else:
            table.finish(probe.seq, answered=False)
        assert len(table) <= table.capacity
        assert len(table._recent) <= table.recent_capacity
