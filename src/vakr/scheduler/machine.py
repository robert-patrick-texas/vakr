"""The device-poll state machine as an explicit transition table (9.2, SCHED-009, ADR-005, ADR-017).

One poll walks ``START -> ICMP -> gate -> SNMP Stage 1 -> conditional
Stage 2 -> DONE``. Which arrows exist depends on the reachability strategy,
and every arrow is a row of ``TRANSITIONS``, so the whole behaviour can be
read, and tested, as a table:

* ``icmp_gated`` (default): 2+ replies continue to Stage 1 (ICMP-006);
  0-1 replies end the poll with SNMP skipped (ICMP-007). An ICMP step that
  failed on the collector side gives no reply count, so SNMP runs normally.
* ``snmp_fallback``: 2+ replies continue normally; 0-1 replies, or an ICMP
  step that failed on the collector side, make one low-cost Stage 1 attempt
  and stop there (ICMP-013). SNMP success proves management reachability
  but never rewrites the ICMP result.
* ``icmp_only``: ICMP, then done (ICMP-014).
* ``snmp_only``: Stage 1 without ICMP (ICMP-014, ADR-017).

The ICMP gate is ``vakr.logic.icmp.snmp_plan``, the single implementation
of ICMP-006..014. Stage 2 runs only when the *current* Stage 1 answered and
found at least one interface admin-up and oper-up; its requests are built
from that Stage 1 result, never from stale state (ADR-005, SCHED-T08).

Dependencies:
    vakr.logic.icmp: The SNMP gate per strategy.
    vakr.domain.enums: ``ReachabilityStrategy``.

Safety:
    Pure. A missing table row is a programming error and raises, rather
    than guessing a next step.

Example:
    >>> s = ReachabilityStrategy.ICMP_GATED
    >>> transition(s, Phase.ICMP, icmp_event(s, replies=1)).value
    'done'
"""

from collections.abc import Mapping
from enum import StrEnum

from vakr.domain.enums import ReachabilityStrategy
from vakr.logic.icmp import SnmpPlan, snmp_plan


class Phase(StrEnum):
    """Where a poll is in the state machine."""

    START = "start"
    ICMP = "icmp"
    STAGE1 = "snmp_stage1"
    FALLBACK = "snmp_fallback_stage1"  # the single low-cost attempt (ICMP-013)
    STAGE2 = "snmp_stage2"
    DONE = "done"


class Event(StrEnum):
    """What a finished phase reports to the table."""

    BEGIN = "begin"
    GATE_COLLECT = "gate_collect"  # snmp_plan: COLLECT
    GATE_FALLBACK = "gate_fallback"  # snmp_plan: STAGE1_FALLBACK
    GATE_SKIP = "gate_skip"  # snmp_plan: SKIP
    ICMP_FAILED = "icmp_failed"  # collector-side ICMP failure: reply count unknown
    UP_UP = "up_up"  # Stage 1 answered; at least one interface up/up
    NO_UP_UP = "no_up_up"  # Stage 1 answered; nothing needs counters
    NO_ANSWER = "no_answer"  # Stage 1 got no usable answer
    FINISHED = "finished"  # Stage 2 ended, however it went


_G, _F, _O, _S = (
    ReachabilityStrategy.ICMP_GATED,
    ReachabilityStrategy.SNMP_FALLBACK,
    ReachabilityStrategy.ICMP_ONLY,
    ReachabilityStrategy.SNMP_ONLY,
)
_STAGES = {
    (Phase.STAGE1, Event.UP_UP): Phase.STAGE2,
    (Phase.STAGE1, Event.NO_UP_UP): Phase.DONE,
    (Phase.STAGE1, Event.NO_ANSWER): Phase.DONE,
    (Phase.STAGE2, Event.FINISHED): Phase.DONE,
}

TRANSITIONS: Mapping[tuple[ReachabilityStrategy, Phase, Event], Phase] = {
    (_G, Phase.START, Event.BEGIN): Phase.ICMP,
    (_G, Phase.ICMP, Event.GATE_COLLECT): Phase.STAGE1,
    (_G, Phase.ICMP, Event.GATE_SKIP): Phase.DONE,
    # No evidence either way: the gate exists to spare unreachable devices, not to
    # stop collection when vakr itself cannot ping (E.25 #5).
    (_G, Phase.ICMP, Event.ICMP_FAILED): Phase.STAGE1,
    **{(_G, *key): to for key, to in _STAGES.items()},
    (_F, Phase.START, Event.BEGIN): Phase.ICMP,
    (_F, Phase.ICMP, Event.GATE_COLLECT): Phase.STAGE1,
    (_F, Phase.ICMP, Event.GATE_FALLBACK): Phase.FALLBACK,
    (_F, Phase.ICMP, Event.ICMP_FAILED): Phase.FALLBACK,
    (_F, Phase.FALLBACK, Event.UP_UP): Phase.DONE,
    (_F, Phase.FALLBACK, Event.NO_UP_UP): Phase.DONE,
    (_F, Phase.FALLBACK, Event.NO_ANSWER): Phase.DONE,
    **{(_F, *key): to for key, to in _STAGES.items()},
    (_O, Phase.START, Event.BEGIN): Phase.ICMP,
    (_O, Phase.ICMP, Event.GATE_SKIP): Phase.DONE,
    (_O, Phase.ICMP, Event.ICMP_FAILED): Phase.DONE,
    (_S, Phase.START, Event.BEGIN): Phase.STAGE1,
    **{(_S, *key): to for key, to in _STAGES.items()},
}
"""Every allowed arrow: ``(strategy, phase, event) -> next phase``."""

SKIPPED_BY: Mapping[tuple[ReachabilityStrategy, Phase, Event], Phase] = {
    (_G, Phase.ICMP, Event.GATE_SKIP): Phase.STAGE1,  # 9.2 "SNMP SKIPPED" (ICMP-007)
    **{
        (strategy, Phase.STAGE1, event): Phase.STAGE2
        for strategy in (_G, _F, _S)
        for event in (Event.NO_UP_UP, Event.NO_ANSWER)
    },
}
"""Arrows that leave a configured step out; the poll record lists it as skipped."""

_GATE_EVENTS = {
    SnmpPlan.COLLECT: Event.GATE_COLLECT,
    SnmpPlan.STAGE1_FALLBACK: Event.GATE_FALLBACK,
    SnmpPlan.SKIP: Event.GATE_SKIP,
}


def transition(strategy: ReachabilityStrategy, phase: Phase, event: Event) -> Phase:
    """Return the next phase for an event.

    Args:
        strategy: The device's reachability strategy.
        phase: The phase that just finished.
        event: What it reported.

    Returns:
        Phase: The next phase.

    Raises:
        ValueError: If the table has no such arrow.
    """
    try:
        return TRANSITIONS[strategy, phase, event]
    except KeyError:
        raise ValueError(f"no transition from {phase} on {event} under {strategy}") from None


def icmp_event(strategy: ReachabilityStrategy, replies: int | None) -> Event:
    """Turn an ICMP outcome into the gating event (ICMP-006, ICMP-007, ICMP-013).

    Args:
        strategy: The device's reachability strategy.
        replies: Probes answered, or ``None`` when the ICMP step failed on
            the collector side and the count is unknown.

    Returns:
        Event: A ``GATE_*`` event from ``snmp_plan``, or ``ICMP_FAILED``.

    Raises:
        None
    """
    if replies is None:
        return Event.ICMP_FAILED
    return _GATE_EVENTS[snmp_plan(replies, strategy)]


def stage1_event(answered: bool, up_up: int) -> Event:
    """Turn a Stage 1 outcome into the Stage 2 decision event (ADR-005).

    Args:
        answered: True when Stage 1 got a full or partial answer.
        up_up: Interfaces admin-up and oper-up in *this* Stage 1 result.

    Returns:
        Event: ``UP_UP``, ``NO_UP_UP``, or ``NO_ANSWER``.

    Raises:
        None
    """
    if not answered:
        return Event.NO_ANSWER
    return Event.UP_UP if up_up > 0 else Event.NO_UP_UP
