"""Tests of the resilience harness's accounting: what counts as silent loss (MVP-ACC-06, E.36).

The harness itself needs privileged containers and runs only by hand
(``make resilience-test``); its pass condition is pure, so it is checked
here on every ``make check`` with synthetic runs, polls, and incidents.

Dependencies:
    pytest: The test runner.
    tests.system.resilience.accounting: The module under test.

Example:
    $ uv run pytest tests/system/resilience/test_accounting.py
"""

import json
from pathlib import Path

import pytest

from tests.system.resilience.accounting import (
    Limits,
    Poll,
    Run,
    Stored,
    account,
    check_dropped,
    classify,
    main,
    parse_events,
    parse_stored,
    render,
)

S = 1_000_000
I = 30 * S  # noqa: E741 - the interval, as in E.25's formula
T0 = 1_800_000_000 * S  # a slot boundary: T0 % I == 0
OFFSET = 7 * S
LIMITS = Limits(I, 1 * S, 1 * S)
DEV = "01a17d46-0000-7000-8000-000000000001"


def polls_for(slots: list[int], execution: str = "complete") -> Stored:
    """Build stored polls, each with its ICMP result, for one device.

    Args:
        slots: Slot times.
        execution: Every poll's execution state.

    Returns:
        Stored: The records.

    Raises:
        None
    """
    found = Stored(devices=[DEV])
    for n, slot in enumerate(slots):
        found.polls.append(Poll(DEV, slot, execution, slot + S // 2, f"poll-{n}"))
        found.icmp[f"poll-{n}"] = "reachable"
    return found


def slot(k: int) -> int:
    """Return the device's slot ``k`` after ``T0``.

    Args:
        k: Slot number.

    Returns:
        int: Epoch microseconds.

    Raises:
        None
    """
    return T0 + OFFSET + k * I


def unclean(detected: int) -> dict:
    """Return an ``unclean_stop`` incident as ``probe.py incidents`` prints it.

    Args:
        detected: Detection time.

    Returns:
        dict: The incident.

    Raises:
        None
    """
    return {"kind": "unclean_stop", "detected_us": detected, "estimated": 3,
            "classes": ["device_poll"], "detail": "unclean"}  # fmt: skip


WINDOW = (T0, T0 + 10 * I)
ONE_RUN = [Run(T0 - I, None, "", "install")]


def test_every_slot_stored_passes() -> None:
    """MVP-ACC-06: a run with every slot stored passes, and each slot is counted once.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    report = account(ONE_RUN, WINDOW, polls_for([slot(k) for k in range(10)]), [], LIMITS)
    assert report.passed, report.problems
    assert report.slots == {"stored": 10}
    assert render(report)[-1] == "accounting: PASS"


def test_missing_slot_while_running_is_silent_loss() -> None:
    """MVP-ACC-06: a slot the running collector left without a record fails the run.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    stored = polls_for([slot(k) for k in range(10) if k != 4])
    report = account(ONE_RUN, WINDOW, stored, [], LIMITS)
    assert not report.passed
    assert report.slots["unexplained"] == 1
    assert any(str(slot(4)) in p for p in report.problems)


def test_slots_while_not_running_are_explained() -> None:
    """MVP-ACC-06, invariant 3: slots between a clean stop and the next start are the outage.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    runs = [Run(T0 - I, slot(3) + S, "clean", "install"), Run(slot(6) - S, None, "", "restart")]
    stored = polls_for([slot(k) for k in range(10) if k not in (4, 5)])
    report = account(runs, WINDOW, stored, [], LIMITS)
    assert report.passed, report.problems
    assert report.slots == {"stored": 8, "not running": 2}


def test_e8_window_needs_an_unclean_incident() -> None:
    """E.8, STORE-007: a slot lost just before an abrupt stop passes only with the incident.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    kill = slot(4) + S
    runs = [Run(T0 - I, kill, "abrupt", "install"), Run(kill + 15 * S, None, "", "restart")]
    stored = polls_for([slot(k) for k in range(10) if k != 4])
    with_incident = account(runs, WINDOW, stored, [unclean(kill + 16 * S)], LIMITS)
    assert with_incident.passed, with_incident.problems
    assert with_incident.slots["E.8 window"] == 1
    without = account(runs, WINDOW, stored, [], LIMITS)
    assert not without.passed
    assert any("recorded no unclean_stop" in p for p in without.problems)


def test_polls_a_simulated_cut_removed_must_be_in_the_e8_window() -> None:
    """E.8: what the simulated page-cache loss removed is accounted as the E.8 window.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    kill = slot(4) + S
    runs = [Run(T0 - I, kill, "abrupt", "install"), Run(kill + 15 * S, None, "", "restart")]
    stored = polls_for([slot(k) for k in range(10) if k != 4])
    report = account(runs, WINDOW, stored, [unclean(kill + 16 * S)], LIMITS)
    ranged = {**unclean(kill + 16 * S), "start_us": slot(4) - S, "end_us": kill + 16 * S}
    lost = ["dropped 7", f"poll\t{DEV}\t{slot(4)}\t{slot(4) + S // 2}"]
    check_dropped(lost, [ranged], report)
    assert report.passed, report.problems
    assert any("E.8 window 1" in n for n in report.notes)
    late = account([Run(T0 - I, None, "", "install")], WINDOW, stored, [], LIMITS)
    check_dropped(lost, [{**ranged, "end_us": slot(4)}], late)
    assert any("simulated power cut" in p for p in late.problems)
    assert any("range holds the lost poll" in p for p in late.problems)


def test_loss_before_the_e8_window_is_silent() -> None:
    """E.8: an abrupt stop explains only its last seconds, not an earlier missing slot.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    kill = slot(4) + 20 * S
    runs = [Run(T0 - I, kill, "abrupt", "install"), Run(kill + 15 * S, None, "", "restart")]
    stored = polls_for([slot(k) for k in range(10) if k != 4])
    report = account(runs, WINDOW, stored, [unclean(kill + 16 * S)], LIMITS)
    assert report.slots["unexplained"] == 1
    assert classify(slot(4), runs, 3 * S, [unclean(kill + 16 * S)]) == "unexplained"


@pytest.mark.parametrize(
    ("incident", "expected"),
    [
        (unclean(slot(5)), "clean stop at"),
        ({**unclean(slot(5)), "kind": "rejected_full"}, "unexpected rejected_full"),
    ],
)
def test_incidents_outside_abrupt_stops_fail(incident: dict, expected: str) -> None:
    """STORE-007: a clean stop must not be recorded as unclean, and no other loss may occur.

    Args:
        incident: The stray incident.
        expected: Text the problem must contain.

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    runs = [Run(T0 - I, slot(4) + S, "clean", "install"), Run(slot(4) + 9 * S, None, "", "r")]
    stored = polls_for([slot(k) for k in range(10)])
    report = account(runs, WINDOW, stored, [incident], LIMITS)
    assert any(expected in p for p in report.problems), report.problems


def test_duplicates_unreachable_and_missing_icmp_fail() -> None:
    """SVC-T04 and invariant 2: a duplicate slot, an unreachable target, a lost ICMP result.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    stored = polls_for([slot(k) for k in range(10)])
    stored.polls.append(Poll(DEV, slot(2), "complete", slot(2) + S, "poll-dup"))
    stored.icmp["poll-3"] = "unreachable"
    del stored.icmp["poll-5"]
    problems = account(ONE_RUN, WINDOW, stored, [], LIMITS).problems
    assert any("duplicate poll" in p for p in problems)
    assert any("unreachable" in p for p in problems)
    assert any("poll-5 has no stored ICMP" in p for p in problems)


def test_a_device_without_polls_fails() -> None:
    """MVP-ACC-06: a device that never got a stored poll cannot be accounted, so it fails.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the accounting disagrees.
    """
    stored = polls_for([slot(k) for k in range(10)])
    stored.devices.append("01a17d46-0000-7000-8000-000000000002")
    report = account(ONE_RUN, WINDOW, stored, [], LIMITS)
    assert any("no stored poll at all" in p for p in report.problems)


def test_parsers_and_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """MVP-ACC-06: the command reads the harness's files and exits 0 on PASS, 1 on FAIL.

    Args:
        tmp_path: Scratch directory.
        capsys: Captured output.

    Returns:
        None

    Raises:
        AssertionError: If parsing or the exit status is wrong.
    """
    events = tmp_path / "events.tsv"
    events.write_text(f"run_start\t{T0 - I}\tinstall\nwindow_start\t{T0}\tw\n"
                      f"window_end\t{T0 + 3 * I}\tacc\n", encoding="utf-8")  # fmt: skip
    lines = (
        [f"device\t{DEV}"]
        + [f"poll\t{DEV}\t{slot(k)}\tcomplete\t-\t{slot(k) + S}\tp{k}" for k in range(3)]
        + [f"icmp\tp{k}\treachable" for k in range(3)]
    )
    lines.append(f"health\t{T0 + S}\thealthy")
    polls = tmp_path / "polls.tsv"
    polls.write_text("\n".join(lines) + "\n", encoding="utf-8")
    incidents = tmp_path / "incidents.jsonl"
    incidents.write_text("", encoding="utf-8")
    argv = ["--events", str(events), "--polls", str(polls), "--incidents", str(incidents)]
    assert main(argv) == 0
    assert "accounting: PASS" in capsys.readouterr().out
    assert parse_stored(lines).health == [(T0 + S, "healthy")]
    incidents.write_text(json.dumps({**unclean(T0 + I), "kind": "torn_tail"}) + "\n",
                         encoding="utf-8")  # fmt: skip
    assert main(argv) == 1
    with pytest.raises(ValueError, match="window_start"):
        parse_events([f"run_start\t{T0}\tx"])
    with pytest.raises(ValueError, match="without a running collector"):
        parse_events([f"run_end\t{T0}\tclean\tx"])
