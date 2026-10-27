"""Account for every poll the schedule expected: stored, or explained (MVP-ACC-06, E.36).

"No silent loss" is checked here, on the host, from three inputs the
harness collects:

- the run log (``events.tsv``): when each collector run became ready
  (``run_start``), when the harness stopped it and how (``run_end``,
  ``clean`` or ``abrupt``), and the accounting window;
- the stored poll records, ICMP results, and health reports
  (``probe.py polls``);
- the telemetry-loss incidents (``probe.py incidents``);
- what a simulated power cut removed from the spool, if one ran
  (``faults.py powercut``): each of its polls must be missing inside the
  E.8 window, never unexplained.

Slot ``k`` of a device is ``k * interval + offset`` (E.25 #1), so every
slot in the window is known once one record shows the device's offset.
Each expected slot is then exactly one of:

- **stored**: a poll record exists, whatever its execution state
  (``not_attempted`` and ``cancelled`` records are explicit, E.25 #8);
- **not running**: no collector run covered it, the outage the views show
  as the collector unavailable (invariant 3);
- **E.8 window**: it was due within ``fsync_interval + handoff +
  longest poll`` before an abrupt stop, and an ``unclean_stop`` incident
  was recorded when the collector came back (E.8, STORE-007);
- **unexplained**: anything else, which is silent loss and fails the run.

The run also fails on a duplicate poll for one slot (SVC-T04), a stored
complete poll without its ICMP result, an ICMP result of ``unreachable``
(the simulated devices never go down, so that would be collector trouble
reported as the target, invariant 2), an abrupt stop without an
``unclean_stop`` incident, a clean stop that recorded one, or any other
incident outside an abrupt stop's recovery.

Dependencies:
    argparse, json: The command line and the incident lines.

Safety:
    Reads files the harness wrote; prints counts, device UUIDs, and times.

Example:
    $ uv run python -m tests.system.resilience.accounting --events events.tsv
        --polls polls.tsv --incidents incidents.jsonl --interval 30
"""

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

US = 1_000_000
RECOVERY_S = 120.0
"""How long after a run's start its recovery incidents may be detected."""


@dataclass(frozen=True, slots=True)
class Run:
    """One collector run, from ready to the harness's stop. Immutable.

    Attributes:
        start_us: When it became ready (``READY=1``).
        end_us: When the harness stopped it, or ``None`` for the last run.
        end_kind: ``clean``, ``abrupt``, or ``""`` for the last run.
        label: What started it.
    """

    start_us: int
    end_us: int | None
    end_kind: str
    label: str


@dataclass(frozen=True, slots=True)
class Poll:
    """One stored poll record. Immutable.

    Attributes:
        device: Device UUID.
        scheduled_us: Its slot.
        execution: ``complete``, ``partial``, ``failed``, ``cancelled``, ...
        completed_us: When it finished, or ``None``.
        poll_id: Poll UUID.
    """

    device: str
    scheduled_us: int
    execution: str
    completed_us: int | None
    poll_id: str


@dataclass(slots=True)
class Stored:
    """What the probe read from the telemetry store. Mutable while parsing.

    Attributes:
        devices: Every device the probe was asked about, polled or not.
        polls: Poll records.
        icmp: ICMP state per poll UUID.
        health: Collector-health states, by report time.
    """

    devices: list[str] = field(default_factory=list)
    polls: list[Poll] = field(default_factory=list)
    icmp: dict[str, str] = field(default_factory=dict)
    health: list[tuple[int, str]] = field(default_factory=list)


@dataclass(slots=True)
class Report:
    """The accounting's outcome. Mutable while accounting.

    Attributes:
        slots: Expected slots per outcome (``stored``, ``not running``, ...).
        executions: Stored poll records per execution state.
        problems: Failures, one sentence each; empty means PASS.
        notes: Facts worth printing.
        missing: The outcome of every expected slot without a record, by device and slot.
    """

    slots: Counter[str] = field(default_factory=Counter)
    executions: Counter[str] = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing: dict[tuple[str, int], str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Whether nothing failed.

        Args:
            None

        Returns:
            bool: ``True`` when ``problems`` is empty.

        Raises:
            None
        """
        return not self.problems


def parse_events(lines: Sequence[str]) -> tuple[list[Run], int, int]:
    """Read the run log into runs and the accounting window.

    Args:
        lines: ``events.tsv`` lines.

    Returns:
        tuple[list[Run], int, int]: Runs in order, window start, and window end.

    Raises:
        ValueError: If the log has no window or no run, or a run ends twice.
    """
    runs: list[Run] = []
    start = end = None
    for line in lines:
        kind, at, *rest = line.rstrip("\n").split("\t")
        if kind == "run_start":
            runs.append(Run(int(at), None, "", rest[0] if rest else ""))
        elif kind == "run_end":
            if not runs or runs[-1].end_us is not None:
                raise ValueError(f"run_end at {at} without a running collector")
            last = runs[-1]
            runs[-1] = Run(last.start_us, int(at), rest[0], last.label)
        elif kind == "window_start":
            start = int(at)
        elif kind == "window_end":
            end = int(at)
    if start is None or end is None or not runs:
        raise ValueError("the run log needs window_start, window_end, and a run_start")
    return runs, start, end


def parse_stored(lines: Sequence[str]) -> Stored:
    """Read ``probe.py polls`` output.

    Args:
        lines: Its lines.

    Returns:
        Stored: The records.

    Raises:
        ValueError: If a line is malformed.
    """
    found = Stored()
    for line in lines:
        cols = line.rstrip("\n").split("\t")
        if cols[0] == "device":
            found.devices.append(cols[1])
        elif cols[0] == "poll":
            done = None if cols[5] == "-" else int(cols[5])
            found.polls.append(Poll(cols[1], int(cols[2]), cols[3], done, cols[6]))
        elif cols[0] == "icmp":
            found.icmp[cols[1]] = cols[2]
        elif cols[0] == "health":
            found.health.append((int(cols[1]), cols[2]))
    return found


def parse_incidents(lines: Sequence[str]) -> list[dict[str, Any]]:
    """Read ``probe.py incidents`` output, one JSON object per line.

    Args:
        lines: Its lines.

    Returns:
        list[dict[str, Any]]: The incidents.

    Raises:
        ValueError: If a line is not JSON.
    """
    return [json.loads(line) for line in lines if line.strip()]


@dataclass(frozen=True, slots=True)
class Limits:
    """The schedule and the E.8 window. Immutable.

    Attributes:
        interval_us: Every device's poll interval.
        fsync_us: ``spool.fsync_interval`` (E.8).
        handoff_us: Time a finished poll may wait in memory for the spool.
    """

    interval_us: int
    fsync_us: int
    handoff_us: int


def account(runs: list[Run], window: tuple[int, int], stored: Stored,
            incidents: list[dict[str, Any]], limits: Limits) -> Report:  # fmt: skip
    """Classify every expected slot and check the incidents and evidence.

    Args:
        runs: Collector runs in order.
        window: Accounting window, start and end in epoch microseconds.
        stored: What the store holds.
        incidents: Telemetry-loss incidents.
        limits: The interval and the E.8 window.

    Returns:
        Report: The outcome.

    Raises:
        None
    """
    report = Report()
    slots: dict[str, dict[int, Poll]] = {d: {} for d in stored.devices}
    for poll in stored.polls:
        mine = slots.setdefault(poll.device, {})
        if poll.scheduled_us in mine:
            report.problems.append(f"duplicate poll: {poll.device} at {poll.scheduled_us}")
        mine[poll.scheduled_us] = poll
    longest = max((p.completed_us - p.scheduled_us for p in stored.polls if p.completed_us),
                  default=0)  # fmt: skip
    reach = limits.fsync_us + limits.handoff_us + longest
    report.notes.append(f"E.8 window at an abrupt stop: {reach / US:.1f} s (fsync "
                        f"{limits.fsync_us / US:g} s, hand-off {limits.handoff_us / US:g} s, "
                        f"longest poll {longest / US:.1f} s)")  # fmt: skip
    _check_incidents(runs, incidents, report)
    for device, mine in sorted(slots.items()):
        if not mine:
            report.problems.append(f"{device} has no stored poll at all")
            continue
        _account_device(device, mine, runs, window, reach, incidents, limits, report)
    _check_evidence(stored, window, report)
    if report.slots.total() == 0:
        report.problems.append("no expected slot in the window: nothing was accounted for")
    return report


def _account_device(device: str, mine: dict[int, Poll], runs: list[Run], window: tuple[int, int],
                    reach: int, incidents: list[dict[str, Any]], limits: Limits,
                    report: Report) -> None:  # fmt: skip
    """Classify one device's expected slots.

    Args:
        device: Device UUID.
        mine: Its stored polls by slot.
        runs: Collector runs.
        window: Accounting window.
        reach: E.8 window length before an abrupt stop, microseconds.
        incidents: Telemetry-loss incidents.
        limits: The interval.
        report: Updated in place.

    Returns:
        None

    Raises:
        None
    """
    offsets = Counter(s % limits.interval_us for s in mine)
    offset, _ = offsets.most_common(1)[0]
    if len(offsets) > 1:
        report.notes.append(f"{device}: {len(offsets)} slot offsets (a clock step relabels)")
    first = window[0] - (window[0] - offset) % limits.interval_us + limits.interval_us
    for slot in range(first, window[1], limits.interval_us):
        poll = mine.get(slot)
        if poll is not None:
            report.slots["stored"] += 1
            report.executions[poll.execution] += 1
            continue
        outcome = classify(slot, runs, reach, incidents)
        report.slots[outcome] += 1
        report.missing[device, slot] = outcome
        if outcome == "unexplained":
            report.problems.append(f"silent loss: {device} slot {slot} has no record")


def classify(slot: int, runs: list[Run], reach: int, incidents: list[dict[str, Any]]) -> str:
    """Explain a slot that has no poll record.

    Args:
        slot: The slot, epoch microseconds.
        runs: Collector runs in order.
        reach: E.8 window length before an abrupt stop, microseconds.
        incidents: Telemetry-loss incidents.

    Returns:
        str: ``not running``, ``E.8 window``, or ``unexplained``.

    Raises:
        None
    """
    run = next((r for r in runs if r.start_us <= slot and (r.end_us is None or slot <= r.end_us)),
               None)  # fmt: skip
    if run is None:
        return "not running"
    end = run.end_us
    abrupt = run.end_kind == "abrupt" and end is not None and end - reach <= slot
    if abrupt and end is not None and _unclean_after(end, runs, incidents):
        return "E.8 window"
    return "unexplained"


def _unclean_after(end: int, runs: list[Run], incidents: list[dict[str, Any]]) -> bool:
    """Tell whether recovery after an abrupt stop recorded an ``unclean_stop`` incident.

    Args:
        end: The abrupt stop.
        runs: Collector runs in order.
        incidents: Telemetry-loss incidents.

    Returns:
        bool: ``True`` when one was detected after the stop and soon after the next start.

    Raises:
        None
    """
    lo, hi = recovery_span(end, runs)
    return any(i["kind"] == "unclean_stop" and lo <= i["detected_us"] <= hi for i in incidents)


def recovery_span(end: int, runs: list[Run]) -> tuple[int, int]:
    """Return when incidents about a stop may be detected: by the next run, soon after it starts.

    Args:
        end: The stop.
        runs: Collector runs in order.

    Returns:
        tuple[int, int]: From the stop to ``RECOVERY_S`` after the next start, and never
        past that next run's own stop, so one incident is never credited to two stops.

    Raises:
        None
    """
    following = [r for r in runs if r.start_us > end]
    if not following:
        return end, end + round(RECOVERY_S * US)
    after = following[0]
    until = after.start_us + round(RECOVERY_S * US)
    return end, until if after.end_us is None else min(until, after.end_us)


def _check_incidents(runs: list[Run], incidents: list[dict[str, Any]], report: Report) -> None:
    """Require an ``unclean_stop`` after each abrupt stop and no incident elsewhere.

    Args:
        runs: Collector runs in order.
        incidents: Telemetry-loss incidents.
        report: Updated in place.

    Returns:
        None

    Raises:
        None
    """
    allowed: list[tuple[int, int]] = []
    for run in runs:
        if run.end_us is None:
            continue
        lo, hi = recovery_span(run.end_us, runs)
        unclean = [i for i in incidents
                   if i["kind"] == "unclean_stop" and lo <= i["detected_us"] <= hi]  # fmt: skip
        if run.end_kind == "abrupt":
            allowed.append((lo, hi))
            if not unclean:
                report.problems.append(f"abrupt stop at {run.end_us} recorded no unclean_stop")
            for i in unclean:
                report.notes.append(f"unclean_stop after the {run.end_us} stop: estimated "
                                    f"{i['estimated']} observation(s), classes "
                                    f"{','.join(i['classes']) or 'none'}")  # fmt: skip
        elif unclean:
            report.problems.append(f"clean stop at {run.end_us} was recorded as unclean")
    for i in incidents:
        if not any(lo <= i["detected_us"] <= hi for lo, hi in allowed):
            report.problems.append(f"unexpected {i['kind']} incident at {i['detected_us']}: "
                                   f"{i['detail']}")  # fmt: skip


def _check_evidence(stored: Stored, window: tuple[int, int], report: Report) -> None:
    """Require each complete poll's ICMP result and no target called unreachable.

    Args:
        stored: What the store holds.
        window: Accounting window.
        report: Updated in place.

    Returns:
        None

    Raises:
        None
    """
    inside = [p for p in stored.polls if window[0] <= p.scheduled_us < window[1]]
    missing = [p for p in inside if p.execution == "complete" and p.poll_id not in stored.icmp]
    for poll in missing:
        report.problems.append(f"complete poll {poll.poll_id} has no stored ICMP result")
    states = Counter(stored.icmp.values())
    report.notes.append("ICMP results: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())))
    if states["unreachable"]:
        report.problems.append(f"{states['unreachable']} ICMP result(s) called a target "
                               "unreachable; the simulated devices never go down")  # fmt: skip
    health = Counter(state for at, state in stored.health if window[0] <= at < window[1])
    report.notes.append("collector health reports: " +
                        ", ".join(f"{k} {v}" for k, v in sorted(health.items())))  # fmt: skip


def check_dropped(lines: Sequence[str], report: Report) -> None:
    """Require every poll a simulated power cut removed to be missing inside the E.8 window.

    Args:
        lines: ``faults.py powercut`` output: ``dropped N``, then ``poll DEVICE SLOT`` lines.
        report: Updated in place.

    Returns:
        None

    Raises:
        ValueError: If a line is malformed.
    """
    polls = [line.split("\t") for line in lines if line.startswith("poll\t")]
    outcomes = Counter(report.missing.get((d, int(at)), "stored") for _, d, at in polls)
    report.notes.append(f"simulated page-cache loss: {lines[0] if lines else 'nothing'}; its "
                        f"{len(polls)} poll(s): " +
                        ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())))  # fmt: skip
    if outcomes["unexplained"]:
        report.problems.append("a poll the simulated power cut removed is unexplained")


def render(report: Report) -> list[str]:
    """Render the report for the harness's output.

    Args:
        report: The outcome.

    Returns:
        list[str]: Lines; the last is ``accounting: PASS`` or ``accounting: FAIL``.

    Raises:
        None
    """
    lines = [
        "expected slots: " + ", ".join(f"{k} {v}" for k, v in sorted(report.slots.items())),
        "stored polls: " + ", ".join(f"{k} {v}" for k, v in sorted(report.executions.items())),
    ]
    lines += report.notes + [f"FAIL: {p}" for p in report.problems[:50]]
    if len(report.problems) > 50:
        lines.append(f"FAIL: ... and {len(report.problems) - 50} more")
    return [*lines, f"accounting: {'PASS' if report.passed else 'FAIL'}"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the accounting and print the report.

    Args:
        argv: Arguments; ``None`` means ``sys.argv[1:]``.

    Returns:
        int: 0 on PASS, 1 on FAIL.

    Raises:
        ValueError: If an input file is malformed.
        OSError: If one cannot be read.
    """
    parser = argparse.ArgumentParser(description="Account for every expected poll (MVP-ACC-06)")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--polls", type=Path, required=True)
    parser.add_argument("--incidents", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=30.0, help="poll interval, seconds")
    parser.add_argument("--fsync", type=float, default=1.0, help="spool.fsync_interval, seconds")
    parser.add_argument("--handoff", type=float, default=1.0, help="spool hand-off, seconds")
    parser.add_argument("--dropped", type=Path, help="faults.py powercut output, if one ran")
    args = parser.parse_args(argv)
    runs, start, end = parse_events(args.events.read_text(encoding="utf-8").splitlines())
    stored = parse_stored(args.polls.read_text(encoding="utf-8").splitlines())
    incidents = parse_incidents(args.incidents.read_text(encoding="utf-8").splitlines())
    limits = Limits(round(args.interval * US), round(args.fsync * US), round(args.handoff * US))
    report = account(runs, (start, end), stored, incidents, limits)
    if args.dropped is not None:
        check_dropped(args.dropped.read_text(encoding="utf-8").splitlines(), report)
    print("\n".join(render(report)))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
