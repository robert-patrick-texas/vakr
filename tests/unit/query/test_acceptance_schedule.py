"""MVP-ACC-01: on-time starts, same-device overlap, drift, and the 60 s workload (E.35).

Known answers from constructed poll records: an on-time week passes; 2%
late fails the 99% share; an overlap and a slot recorded twice fail;
start offsets that grow 200 ms a day fail drift while still inside ±5 s;
and the healthy-device definition leaves out maintenance and device
trouble but never the collector's own misses. One test runs the whole
service over the reference adapters.

Dependencies:
    pytest; tests.unit.query.acceptance_testkit; vakr.query.acceptance_*.

Example:
    $ uv run pytest tests/unit/query/test_acceptance_schedule.py
"""

from collections.abc import Callable
from datetime import timedelta

from tests.unit.query.acceptance_testkit import T0, Fleet, SlotPlan
from vakr.domain.enums import PollExecution
from vakr.domain.quality import Quality
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.acceptance import PollMark
from vakr.query.acceptance_request import AcceptanceRequest
from vakr.query.acceptance_schedule import ScheduleTally
from vakr.query.acceptance_schedule_judge import judge
from vakr.query.acceptance_views import CollectorSchedule, Verdict

US = 1_000_000
START = to_epoch_us(T0)
DAY_SLOTS = 1_440


def marks(days: float, offset_us: Callable[[int], int] = lambda _k: 200_000, *,
          execution: Callable[[int], PollExecution] = lambda _k: PollExecution.COMPLETE,
          quality: Callable[[int], Quality] = lambda _k: Quality.NONE,
          duration_us: Callable[[int], int] = lambda _k: 3 * US,
          interval_s: int = 60) -> list[PollMark]:  # fmt: skip
    """Build one device's poll marks, one a minute at a 7 s phase.

    Args:
        days: How many days of slots.
        offset_us: Slot number to start offset.
        execution: Slot number to execution state.
        quality: Slot number to flags.
        duration_us: Slot number to run time.
        interval_s: The effective interval.

    Returns:
        list[PollMark]: The marks.

    Raises:
        None
    """
    out = []
    for k in range(int(days * DAY_SLOTS)):
        at = START + 7 * US + k * interval_s * US
        state = execution(k)
        ran = state not in (PollExecution.NOT_ATTEMPTED, PollExecution.MISSED_OVERRUN)
        start = at + offset_us(k) if ran else None
        out.append(
            PollMark(
                at,
                interval_s,
                state,
                quality(k),
                start,
                None if start is None else start + duration_us(k),
            )
        )
    return out


def check(section: CollectorSchedule, name: str) -> Verdict:
    """Return one check's verdict.

    Args:
        section: The collector section.
        name: Check name.

    Returns:
        Verdict: Its verdict.

    Raises:
        None
    """
    return next(c.verdict for c in section.checks if c.name == name)


def tally_of(*devices: list[PollMark], days: float = 8) -> CollectorSchedule:
    """Tally devices over ``days`` and judge them.

    Args:
        *devices: Each device's marks.
        days: Window length.

    Returns:
        CollectorSchedule: The section.

    Raises:
        None
    """
    tally = ScheduleTally()
    for i, m in enumerate(devices):
        tally.add_device(f"r{i}", m, (START, START + int(days * 86_400 * US)), 60)
    return judge(tally, "east", 1_600)


def test_mvp_acc01_on_time_week_passes() -> None:
    """MVP-ACC-01: every healthy-device poll on time over 8 days, no drift: pass.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    section = tally_of(marks(8), marks(8, lambda k: 150_000 + (k % 7) * 1_000))
    assert section.healthy == section.on_time == 2 * 8 * DAY_SLOTS
    assert section.on_time_share == 1.0
    assert section.offset_p50_ms == 156.0  # nearest rank: the slower device's largest offset
    assert section.slots_without_record == 0
    assert section.intervals == {60: 2 * 8 * DAY_SLOTS}
    assert section.offset_slope_ms_per_day == 0.0
    assert section.verdict is Verdict.PASS


def test_mvp_acc01_two_percent_late_fails_the_share() -> None:
    """MVP-ACC-01: 2% of polls 6 s late is 98% on time, below 99%: fail.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    late = marks(8, lambda k: 6 * US if k % 50 == 0 else 200_000)
    section = tally_of(late)
    assert section.late == 8 * DAY_SLOTS // 50 + 1
    assert section.on_time_share == section.on_time / section.healthy < 0.99
    assert check(section, "on_time") is Verdict.FAIL
    assert section.offset_max_ms == 6_000.0
    assert section.verdict is Verdict.FAIL


def test_mvp_acc01_overlap_and_double_record_fail() -> None:
    """MVP-ACC-01 "no same-device overlap": a poll started before the last ended; a slot twice.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    device = marks(8)
    long = device[100]
    device[100] = PollMark(long.scheduled_us, 60, PollExecution.COMPLETE, Quality.NONE,
                           long.started_us, long.started_us + 61 * US)  # fmt: skip
    device.insert(500, device[500])
    section = tally_of(device)
    # The slot recorded twice also ran twice at once, so it is an overlap as well.
    assert (section.overlaps, section.duplicates) == (2, 1)
    assert section.overlap_rows[0].device == "r0"
    assert section.overlap_rows[0].overlap_ms == 1_000.0
    assert check(section, "overlap") is Verdict.FAIL
    assert section.excluded == {"device_trouble": 1, "previous_poll_trouble": 1}


def test_mvp_acc01_slow_climb_is_drift_although_on_time() -> None:
    """MVP-ACC-01 "no systematic drift": +200 ms a day stays within ±5 s but fails drift.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    section = tally_of(marks(8, lambda k: 100_000 + (k // DAY_SLOTS) * 200_000))
    assert check(section, "on_time") is Verdict.PASS
    assert section.offset_slope_ms_per_day == 200.0
    assert [d.median_offset_ms for d in section.days][:3] == [100.0, 300.0, 500.0]
    assert check(section, "drift") is Verdict.FAIL


def test_mvp_acc01_slot_phase_drift_is_drift() -> None:
    """MVP-ACC-01: slots that move off their grid by 0.5 s a day fail drift.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    device = [PollMark(m.scheduled_us + (i // DAY_SLOTS) * 500_000, 60, m.execution, m.quality,
                       m.started_us + (i // DAY_SLOTS) * 500_000, m.completed_us)
              for i, m in enumerate(marks(8))]  # fmt: skip
    section = tally_of(device)
    assert section.phase_slope_ms_per_day == 500.0
    assert check(section, "drift") is Verdict.FAIL


def test_mvp_acc01_healthy_devices_exclude_device_trouble_not_collector_misses() -> None:
    """E.35 healthy device: maintenance and device trouble are left out; capacity misses count.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    states = {10: PollExecution.FAILED, 20: PollExecution.PARTIAL, 30: PollExecution.NOT_ATTEMPTED,
              40: PollExecution.FAILED}  # fmt: skip
    flags = {30: Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED,
             40: Quality.COLLECTOR_DEGRADED, 50: Quality.MAINTENANCE}  # fmt: skip
    device = marks(1, execution=lambda k: states.get(k, PollExecution.COMPLETE),
                   quality=lambda k: flags.get(k, Quality.NONE))  # fmt: skip
    section = tally_of(device, days=1)
    assert section.excluded == {"device_trouble": 2, "maintenance": 1, "previous_poll_trouble": 2}
    assert section.missed == 1
    assert section.healthy == DAY_SLOTS - 5
    assert check(section, "drift") is Verdict.INCOMPLETE
    assert section.verdict is Verdict.INCOMPLETE


def test_mvp_acc01_other_interval_fails_the_workload() -> None:
    """MVP-ACC-01 "at 60 s": a device polled every 300 s is not the reference workload.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    section = tally_of(marks(8), marks(1.6, interval_s=300))
    assert section.intervals == {60: 8 * DAY_SLOTS, 300: int(1.6 * DAY_SLOTS)}
    assert check(section, "workload") is Verdict.FAIL
    assert "1600 interfaces" in next(c.detail for c in section.checks if c.name == "workload")


def test_mvp_acc01_service_reads_the_records_of_each_collector() -> None:
    """MVP-ACC-01 end to end over the reference adapters: one late and one missed slot of 120.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    device = fleet.device("core1")
    plans = {5: SlotPlan(start_s=9.0, icmp=False, snmp=None),
             6: SlotPlan(PollExecution.NOT_ATTEMPTED, Quality.NOT_ATTEMPTED, None, icmp=False,
                         snmp=None)}  # fmt: skip
    fleet.run(device, T0, 120, lambda k: plans.get(k, SlotPlan(icmp=False, snmp=None)))
    fleet.health(T0, 120)
    report = fleet.queries(T0 + timedelta(hours=2, minutes=5)).schedule(
        AcceptanceRequest(timedelta(hours=2))
    )
    (section,) = report.collectors
    assert isinstance(section, CollectorSchedule)
    assert (section.records, section.late, section.missed, section.interfaces) == (120, 1, 1, 1600)
    assert section.on_time == 118
    assert report.criterion == "MVP-ACC-01"
    assert check(section, "on_time") is Verdict.FAIL  # 118 of 120 is 98.3%
    assert check(section, "drift") is Verdict.INCOMPLETE  # under 7 days
    assert report.verdict is Verdict.FAIL
