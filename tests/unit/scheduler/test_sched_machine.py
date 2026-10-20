"""The poll state machine: gating per strategy and reply count, and Stage 2 dependency (SCHED-T08).

``test_gating_table`` covers every strategy with 0 to 5 ICMP replies
(ICMP-006, ICMP-007, ICMP-013, ICMP-014). The ``run_poll`` tests drive real
step sequences through scripted steps and check which steps ran, with which
arguments, and what the poll kept when a step failed (invariant 4).

Dependencies:
    pytest: Parametrisation.
    vakr.scheduler.machine, runner: The code under test.
    tests.unit.scheduler: Virtual time and scripted steps.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_machine.py
"""

import pytest

from tests.unit.scheduler.sched_fakes import IDS, Behaviour, ScriptedSteps, device_id, make_target
from tests.unit.scheduler.sched_simloop import START, new_clock, run_virtual
from vakr.domain.enums import ReachabilityStrategy
from vakr.domain.ids import PollId, new_id
from vakr.domain.quality import Quality
from vakr.scheduler.machine import TRANSITIONS, Event, Phase, icmp_event, stage1_event, transition
from vakr.scheduler.runner import PollProgress, run_poll
from vakr.scheduler.steps import IcmpResult, PollContext, PollSteps, SnmpResult, StepStatus

G, F, IO, SO = (
    ReachabilityStrategy.ICMP_GATED, ReachabilityStrategy.SNMP_FALLBACK,
    ReachabilityStrategy.ICMP_ONLY, ReachabilityStrategy.SNMP_ONLY,
)  # fmt: skip
EXPECTED_AFTER_ICMP = {
    G: [Phase.DONE] * 2 + [Phase.STAGE1] * 4,  # ICMP-006/007: 2+ replies proceed
    F: [Phase.FALLBACK] * 2 + [Phase.STAGE1] * 4,  # ICMP-013: one low-cost attempt
    IO: [Phase.DONE] * 6,  # ICMP-014: never SNMP
}


@pytest.mark.parametrize("strategy", [G, F, IO])
@pytest.mark.parametrize("replies", range(6))
def test_gating_table(strategy: ReachabilityStrategy, replies: int) -> None:
    """Every strategy x reply count leads to the documented next phase.

    Args:
        strategy: Reachability strategy.
        replies: ICMP replies out of 5.

    Returns:
        None

    Raises:
        None
    """
    after = transition(strategy, Phase.ICMP, icmp_event(strategy, replies))
    assert after is EXPECTED_AFTER_ICMP[strategy][replies]


def test_snmp_only_never_probes_and_every_strategy_reaches_done() -> None:
    """ICMP-014: snmp_only starts at Stage 1; every table path terminates.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert transition(SO, Phase.START, Event.BEGIN) is Phase.STAGE1
    assert {transition(s, Phase.START, Event.BEGIN) for s in (G, F, IO)} == {Phase.ICMP}
    for (strategy, phase, _), target in TRANSITIONS.items():
        assert target is not Phase.START, strategy
        assert phase is not Phase.DONE, strategy
    assert transition(G, Phase.ICMP, icmp_event(G, None)) is Phase.STAGE1  # E.25 #5
    assert transition(F, Phase.ICMP, icmp_event(F, None)) is Phase.FALLBACK
    with pytest.raises(ValueError, match="no transition"):
        transition(IO, Phase.STAGE1, Event.UP_UP)


@pytest.mark.parametrize(
    ("answered", "up_up", "event"),
    [(True, 3, Event.UP_UP), (True, 0, Event.NO_UP_UP), (False, 0, Event.NO_ANSWER)],
)
def test_stage2_depends_on_current_stage1(answered: bool, up_up: int, event: Event) -> None:
    """SCHED-T08, ADR-005: Stage 2 runs only on a current answer with up/up interfaces.

    Args:
        answered: Stage 1 answered.
        up_up: Up/up interfaces in this Stage 1.
        event: Expected event.

    Returns:
        None

    Raises:
        None
    """
    assert stage1_event(answered, up_up) is event
    expected = Phase.STAGE2 if event is Event.UP_UP else Phase.DONE
    assert transition(G, Phase.STAGE1, event) is expected


def _run(
    strategy: ReachabilityStrategy, behaviour: Behaviour
) -> tuple[PollProgress, ScriptedSteps]:
    """Run one poll of device 1 through ``run_poll`` in virtual time.

    Args:
        strategy: Reachability strategy.
        behaviour: How the device answers.

    Returns:
        tuple[PollProgress, ScriptedSteps]: What the poll kept and the step log.

    Raises:
        None
    """
    clock = new_clock()
    steps = ScriptedSteps(clock, default=behaviour)
    ctx = PollContext(new_id(PollId), IDS.collector_id, make_target(1, strategy=strategy), START)
    progress = PollProgress()
    run_virtual(clock, run_poll(ctx, PollSteps(steps, steps), progress, lambda: 0))
    return progress, steps


def _shape(progress: PollProgress) -> list[tuple[str, str]]:
    """Return ``(phase, status)`` pairs of a poll's steps.

    Args:
        progress: The poll's progress.

    Returns:
        list[tuple[str, str]]: Step names and statuses.

    Raises:
        None
    """
    return [(s.phase.value, s.status.value) for s in progress.steps]


@pytest.mark.parametrize(
    ("strategy", "behaviour", "shape"),
    [
        (G, Behaviour(), [("icmp", "ok"), ("snmp_stage1", "ok"), ("snmp_stage2", "ok")]),
        (G, Behaviour(replies=1), [("icmp", "ok"), ("snmp_stage1", "skipped")]),
        (G, Behaviour(up_up=0),
         [("icmp", "ok"), ("snmp_stage1", "ok"), ("snmp_stage2", "skipped")]),
        (G, Behaviour(stage1=StepStatus.NO_RESPONSE),
         [("icmp", "ok"), ("snmp_stage1", "no_response"), ("snmp_stage2", "skipped")]),
        (G, Behaviour(stage2=StepStatus.PARTIAL),
         [("icmp", "ok"), ("snmp_stage1", "ok"), ("snmp_stage2", "partial")]),
        (F, Behaviour(replies=0), [("icmp", "ok"), ("snmp_fallback_stage1", "ok")]),
        (IO, Behaviour(), [("icmp", "ok")]),
        (SO, Behaviour(), [("snmp_stage1", "ok"), ("snmp_stage2", "ok")]),
        (G, Behaviour(raise_in="icmp"),
         [("icmp", "error"), ("snmp_stage1", "ok"), ("snmp_stage2", "ok")]),
        (F, Behaviour(raise_in="icmp"), [("icmp", "error"), ("snmp_fallback_stage1", "ok")]),
        (G, Behaviour(raise_in="stage1"),
         [("icmp", "ok"), ("snmp_stage1", "error"), ("snmp_stage2", "skipped")]),
        (SO, Behaviour(raise_in="stage2"), [("snmp_stage1", "ok"), ("snmp_stage2", "error")]),
    ],
)  # fmt: skip
def test_run_poll_walks_the_table(
    strategy: ReachabilityStrategy, behaviour: Behaviour, shape: list[tuple[str, str]]
) -> None:
    """9.2: each strategy and outcome runs exactly the documented steps.

    Args:
        strategy: Reachability strategy.
        behaviour: How the device answers.
        shape: Expected ``(phase, status)`` sequence.

    Returns:
        None

    Raises:
        None
    """
    progress, _ = _run(strategy, behaviour)
    assert _shape(progress) == shape
    errored = any(s.status is StepStatus.ERROR for s in progress.steps)
    assert bool(progress.quality & Quality.COLLECTOR_DEGRADED) is errored  # invariant 2
    assert all(s.error in ("", "OSError") for s in progress.steps)


def test_fallback_is_one_low_cost_attempt_and_icmp_evidence_is_kept() -> None:
    """ICMP-013: the fallback Stage 1 is flagged; SNMP never rewrites the ICMP result.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    progress, steps = _run(F, Behaviour(replies=1))
    stage1 = [c for c in steps.calls if c.step == "stage1"]
    assert len(stage1) == 1
    assert stage1[0].fallback is True
    assert progress.observations[0][0] == "icmp"  # ty: ignore[not-subscriptable]
    assert not steps.stage2_inputs
    normal, normal_steps = _run(F, Behaviour(replies=2))
    assert [c.fallback for c in normal_steps.calls if c.step == "stage1"] == [False]
    assert len(normal.steps) == 3


def test_stage2_receives_this_polls_stage1_result() -> None:
    """SCHED-T08: Stage 2 is built from the same poll's Stage 1, never stale state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    _, steps = _run(G, Behaviour(up_up=7))
    ((poll_id, stage1),) = steps.stage2_inputs
    assert stage1.detail == poll_id
    assert stage1.up_up == 7
    assert {c.poll for c in steps.calls} == {poll_id}
    assert steps.calls[0].device == device_id(1)


def test_step_results_validate_their_contract() -> None:
    """Results refuse impossible values instead of passing them on.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="replies"):
        IcmpResult(replies=6, sent=5)
    with pytest.raises(ValueError, match="status"):
        SnmpResult(StepStatus.SKIPPED)
    with pytest.raises(ValueError, match="up_up"):
        SnmpResult(StepStatus.OK, up_up=-1)
    assert SnmpResult(StepStatus.PARTIAL).answered
