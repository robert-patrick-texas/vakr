"""MVP-ACC-10: every period without data, classified by the evidence vakr holds (E.35).

One constructed six-hour fleet holds every gap class, one device or
interface each, with a known answer slot by slot: collector stopped, busy,
or faulty; a full spool; a telemetry-loss incident; an overrun; an
unreachable device; SNMP timeout and error; admin state and maintenance;
an interface not yet discovered or not up/up; inventory changes; and the
unexplained gaps that must make the report fail. The same answer must come
from rollups plus native samples and from native samples alone.

Dependencies:
    pytest; tests.unit.query.acceptance_testkit; vakr.query.acceptance_*.

Example:
    $ uv run pytest tests/unit/query/test_acceptance_gaps.py
"""

from datetime import timedelta

import pytest

from tests.contract import contract_samples as s
from tests.unit.query.acceptance_testkit import MINUTE, NOT_ATTEMPTED, OK, T0, Fleet, SlotPlan
from vakr.domain.enums import AdminState, InterfaceLifecycle, PollExecution, SnmpState
from vakr.domain.paging import PageRequest
from vakr.domain.quality import Quality
from vakr.domain.timestamps import to_epoch_us
from vakr.query.acceptance_evidence import LossRange
from vakr.query.acceptance_request import AcceptanceRequest
from vakr.query.acceptance_views import CollectorGaps, Verdict

NOW = T0 + timedelta(hours=6, minutes=5)
SIX_HOURS = AcceptanceRequest(timedelta(hours=6))
GI1 = frozenset({"Gi1"})
NOTHING = frozenset[str]()
FAILED = SlotPlan(PollExecution.FAILED, icmp=False, snmp=None, sampled=NOTHING)


def only(slots: dict[int, SlotPlan | None]):
    """Return a plan that is ``OK`` except at the given slots.

    Args:
        slots: Slot number to plan (``None``: no record).

    Returns:
        Callable: The plan.

    Raises:
        None
    """
    return lambda k: slots.get(k, OK)


def build(*, rollups: bool, unexplained: bool = True) -> Fleet:
    """Construct the fleet: one device or interface per gap class.

    Args:
        rollups: Whether the store keeps rollups (built up to 4 h).
        unexplained: Include the unexplained device, interface, and ICMP gaps.

    Returns:
        Fleet: The fleet.

    Raises:
        None
    """
    f = Fleet(rollups=rollups)
    plans = {
        "a-stopped": only(dict.fromkeys(range(30, 35))),
        "b-busy": only(dict.fromkeys(range(40, 43), NOT_ATTEMPTED)),
        "c-spool": only(dict.fromkeys((60, 61), NOT_ATTEMPTED)),
        "d-loss": only(dict.fromkeys(range(80, 83))),
        "e-overrun": only({100: SlotPlan(PollExecution.MISSED_OVERRUN, start_s=None, icmp=False,
                                         snmp=None, sampled=NOTHING)}),
        "f-fault": only({110: SlotPlan(PollExecution.FAILED, Quality.COLLECTOR_DEGRADED,
                                       icmp=False, snmp=None, sampled=NOTHING)}),
        "m-audit": only(dict.fromkeys(range(200, 205))),
    }  # fmt: skip
    for name, plan in plans.items():
        f.run(f.device(name), T0, 360, plan)
    unreach = f.device("g-unreach")
    f.interface(unreach, "Gi1")
    skipped = SlotPlan(snmp=SnmpState.SKIPPED, sampled=NOTHING)
    f.run(unreach, T0, 360, only({120: FAILED, 121: FAILED, 130: skipped, 131: skipped}))
    snmp = f.device("h-snmp")
    f.interface(snmp, "Gi1")
    f.run(snmp, T0, 360, only({
        140: SlotPlan(snmp=SnmpState.TIMEOUT, sampled=NOTHING),
        150: SlotPlan(snmp=SnmpState.ERROR, sampled=NOTHING),
        160: SlotPlan(PollExecution.PARTIAL, sampled=NOTHING)}))  # fmt: skip
    off = f.device("i-disabled", admin_state=AdminState.DISABLED,
                   updated_at=T0 + timedelta(hours=3))  # fmt: skip
    f.run(off, T0, 180)
    grown = f.device("j-new-iface")
    f.interface(grown, "Gi1")
    f.interface(
        grown, "Gi9", created_at=T0 + timedelta(hours=2), updated_at=T0 + timedelta(hours=2)
    )
    f.run(grown, T0, 360, lambda k: SlotPlan(sampled=GI1) if k < 120 else OK)
    down = f.device("k-down")
    f.interface(down, "Gi1")
    f.interface(down, "Gi2", lifecycle=InterfaceLifecycle.INACTIVE,
                updated_at=T0 + timedelta(hours=5))  # fmt: skip
    f.run(down, T0, 360, lambda k: SlotPlan(sampled=GI1) if k >= 300 else OK)
    added = f.device(
        "l-added", created_at=T0 + timedelta(hours=1), updated_at=T0 + timedelta(hours=1)
    )
    f.run(added, T0 + timedelta(hours=1), 300)
    maint = f.device("p-maint")
    f.interface(maint, "Gi1")
    f.run(maint, T0, 360, only({290: SlotPlan(quality=Quality.MAINTENANCE, sampled=NOTHING)}))
    cancel = f.device("q-cancel")
    f.interface(cancel, "Gi1")
    f.run(cancel, T0, 360, only({310: SlotPlan(PollExecution.CANCELLED, sampled=NOTHING)}))
    if unexplained:
        lost = f.device("n-unexplained")
        f.interface(lost, "Gi1")
        f.run(lost, T0, 360, only({250: None, 251: None, 260: SlotPlan(sampled=NOTHING)}))
        ping = f.device("o-icmp")
        f.run(ping, T0, 360, only({270: SlotPlan(quality=Quality.COLLECTOR_DEGRADED, icmp=False),
                                   280: SlotPlan(icmp=False)}))  # fmt: skip
    audited = next(d for d in _devices(f) if d.name == "m-audit")
    with f.uow() as uow:
        uow.audit.append(s.audit(T0 + 200 * MINUTE + timedelta(seconds=30),
                                 object_id=audited.id, object_type="device"))  # fmt: skip
        uow.commit()
    f.health(T0, 360, gaps=((30, 36),), spool_pct=lambda k: 96.0 if 59 <= k <= 62 else 1.0)
    f.losses.append(
        LossRange(
            "unclean_stop",
            to_epoch_us(T0 + 80 * MINUTE),
            to_epoch_us(T0 + 83 * MINUTE),
            ("device_poll",),
            "power cut",
        )
    )
    if rollups:
        f.rollup(T0 + timedelta(hours=4))
    return f


def _devices(f: Fleet):
    """List the fleet's devices.

    Args:
        f: The fleet.

    Returns:
        list: Devices.

    Raises:
        None
    """
    with f.uow() as uow:
        return list(uow.devices.list_page(PageRequest(500)).items)


def rows_of(section: CollectorGaps) -> set[tuple[str, str, tuple[str, ...], str, int, int]]:
    """Reduce gap rows to ``(kind, device, subjects, cause, slots, start minute)``.

    Args:
        section: The collector section.

    Returns:
        set: The rows.

    Raises:
        None
    """
    return {(r.kind, r.device, r.subjects, r.cause, r.slots, int((r.start - T0) / MINUTE))
            for r in section.rows}  # fmt: skip


EXPLAINED = {
    ("device", "a-stopped", (), "collector_stopped", 5, 30),
    ("device", "b-busy", (), "collector_busy", 3, 40),
    ("device", "c-spool", (), "storage", 2, 60),
    ("device", "d-loss", (), "telemetry_loss", 3, 80),
    ("device", "e-overrun", (), "poll_overrun", 1, 100),
    ("device", "f-fault", (), "collector_fault", 1, 110),
    ("device", "g-unreach", (), "device_unreachable", 2, 120),
    ("interface", "g-unreach", ("Gi1",), "device_unreachable", 2, 130),
    ("interface", "h-snmp", ("Gi1",), "snmp_timeout", 1, 140),
    ("interface", "h-snmp", ("Gi1",), "snmp_error", 1, 150),
    ("interface", "h-snmp", ("Gi1",), "snmp_error", 1, 160),
    ("device", "i-disabled", (), "admin_state", 180, 180),
    ("interface", "j-new-iface", ("Gi9",), "not_yet_discovered", 120, 0),
    ("interface", "k-down", ("Gi2",), "interface_not_up", 60, 300),
    ("device", "l-added", (), "inventory_change", 60, 0),
    ("device", "m-audit", (), "inventory_change", 5, 200),
    ("interface", "p-maint", ("Gi1",), "admin_state", 1, 290),
    ("interface", "q-cancel", ("Gi1",), "collector_stopped", 1, 310),
}
UNEXPLAINED = {
    ("device", "n-unexplained", (), "unexplained", 2, 250),
    ("interface", "n-unexplained", ("Gi1",), "unexplained", 1, 260),
    ("icmp", "o-icmp", (), "collector_fault", 1, 270),
    ("icmp", "o-icmp", (), "unexplained", 1, 280),
}


@pytest.mark.parametrize("rollups", [True, False], ids=["rollups", "native-only"])
def test_mvp_acc10_every_gap_class_has_its_known_cause(rollups: bool) -> None:
    """MVP-ACC-10, invariants 1-2: each constructed gap is found once, with its cause.

    Args:
        rollups: Whether the rollup path or the native path counts samples.

    Returns:
        None

    Raises:
        None
    """
    report = build(rollups=rollups).queries(NOW).gaps(SIX_HOURS)
    (section,) = report.collectors
    assert isinstance(section, CollectorGaps)
    assert rows_of(section) == EXPLAINED | UNEXPLAINED
    assert section.unexplained == 3
    assert [r.explained for r in section.rows[:3]] == [False] * 3  # unexplained first
    assert section.by_cause["admin_state"] == 181
    assert next(c for c in section.checks if c.name == "gaps").verdict is Verdict.FAIL
    assert next(c for c in section.checks if c.name == "evidence").verdict is Verdict.PASS
    assert report.verdict is Verdict.FAIL


def test_mvp_acc10_passes_when_every_gap_is_explained() -> None:
    """MVP-ACC-10: with the unexplained gaps removed, the same fleet passes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    report = build(rollups=True, unexplained=False).queries(NOW).gaps(SIX_HOURS)
    (section,) = report.collectors
    assert isinstance(section, CollectorGaps)
    assert rows_of(section) == EXPLAINED
    assert (section.unexplained, report.verdict) == (0, Verdict.PASS)


def test_mvp_acc10_peer_silence_at_the_end_is_replication_not_target_down() -> None:
    """MPOS-006, invariant 2: a peer's trailing silence is storage/replication trouble.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet(rollups=False)
    f.run(f.device("core1"), T0, 300)
    f.health(T0, 300)
    (section,) = f.queries(NOW, local="west").gaps(SIX_HOURS).collectors
    assert isinstance(section, CollectorGaps)
    assert rows_of(section) == {("device", "core1", (), "storage", 60, 300)}
