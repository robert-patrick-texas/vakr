"""MVP-ACC-02: graphs at every range, and resets, not spikes, across reloads and discontinuities.

Known answers from constructed telemetry: a device reload whose samples
store no rate passes with every interface reset; a sample with a rate
across a reload, and an over-capacity sample right after a counter
discontinuity, are spikes and fail; an interface whose packet rates are
never available has empty packet graphs and fails; an interface that
stopped an hour and a half ago shows a gap at 1 h and data elsewhere; and
data newer than the rollup frontier is pending at 400 d.

Dependencies:
    pytest; tests.unit.query.acceptance_testkit; vakr.query.acceptance_*.

Example:
    $ uv run pytest tests/unit/query/test_acceptance_coverage.py
"""

from datetime import timedelta

from tests.unit.query.acceptance_testkit import OK, T0, Fleet, SlotPlan
from vakr.domain.enums import UptimeContinuity
from vakr.domain.quality import Quality
from vakr.query.acceptance_coverage_views import CollectorCoverage, RangeCount
from vakr.query.acceptance_request import AcceptanceRequest
from vakr.query.acceptance_views import Verdict

NOW = T0 + timedelta(hours=6, minutes=5)
SIX_HOURS = AcceptanceRequest(timedelta(hours=6))
RESET = UptimeContinuity.RESET_DETECTED


def section_of(f: Fleet, now=NOW) -> CollectorCoverage:
    """Run the coverage report and return its one collector section.

    Args:
        f: The fleet.
        now: Report time.

    Returns:
        CollectorCoverage: The section.

    Raises:
        None
    """
    (section,) = f.queries(now).coverage(SIX_HOURS).collectors
    assert isinstance(section, CollectorCoverage)
    return section


def count(section: CollectorCoverage, label: str, family: str) -> RangeCount:
    """Return one range and family's counts.

    Args:
        section: The section.
        label: Range label.
        family: Family.

    Returns:
        RangeCount: The counts.

    Raises:
        None
    """
    return next(r for r in section.ranges if (r.range, r.family) == (label, family))


def verdict(section: CollectorCoverage, name: str) -> Verdict:
    """Return one check's verdict.

    Args:
        section: The section.
        name: Check name.

    Returns:
        Verdict: The verdict.

    Raises:
        None
    """
    return next(c.verdict for c in section.checks if c.name == name)


def test_mvp_acc02_reload_resets_every_interface_without_a_spike() -> None:
    """MVP-ACC-02, RATE-004: a reload stores no rate (a gap) on each interface, no spike.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    router = f.device("r-reload")
    f.interface(router, "Gi1")
    f.interface(router, "Gi2")
    flags = {"Gi1": Quality.BASELINE_UPTIME_RESET, "Gi2": Quality.BASELINE_UPTIME_RESET}
    f.run(router, T0, 360,
          lambda k: SlotPlan(uptime=RESET, sample_quality=flags) if k == 200 else OK)  # fmt: skip
    f.rollup(T0 + timedelta(hours=4))
    section = section_of(f)
    (reload,) = [e for e in section.events if e.kind == "reload"]
    assert (reload.device, reload.resets, reload.spikes) == ("r-reload", 2, 0)
    assert int((reload.at - T0).total_seconds()) == 200 * 60 + 9
    assert (section.reloads, section.discontinuities, section.spikes) == (1, 2, 0)
    for label in ("1h", "24h", "7d", "30d", "400d"):
        for family in ("traffic", "packets", "errors"):
            assert count(section, label, family).data == 2
    assert section.rows == ()
    assert section.verdict is Verdict.PASS


def test_mvp_acc02_rate_across_a_reload_or_after_a_discontinuity_is_a_spike() -> None:
    """MVP-ACC-02 "no spikes": both kinds are counted and fail; a stray over-capacity is noted.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    router = f.device("s-spike")
    f.interface(router, "Gi1")
    plans = {
        210: SlotPlan(uptime=RESET),  # the sample keeps its rate across the reload
        220: SlotPlan(sample_quality={"Gi1": Quality.BASELINE_DISCONTINUITY}),
        221: SlotPlan(sample_quality={"Gi1": Quality.RATE_EXCEEDS_CAPACITY}),
        300: SlotPlan(sample_quality={"Gi1": Quality.RATE_EXCEEDS_CAPACITY}),
    }
    f.run(router, T0, 360, lambda k: plans.get(k, OK))
    f.rollup(T0 + timedelta(hours=4))
    section = section_of(f)
    assert (section.reloads, section.discontinuities, section.spikes) == (1, 1, 2)
    assert section.over_capacity == 1
    assert [e.kind for e in section.events] == ["reload", "discontinuity"]
    assert all(e.spikes == 1 for e in section.events)
    assert verdict(section, "spikes") is Verdict.FAIL
    assert section.verdict is Verdict.FAIL


def test_mvp_acc02_empty_family_fails_and_a_recent_outage_is_a_gap() -> None:
    """MVP-ACC-02: no packet rate ever is an empty graph; a 90-minute outage is a 1 h gap.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    router = f.device("t-ranges")
    f.interface(router, "Gi1")
    f.interface(router, "Gi2")
    no_packets = {"Gi1": {"in_pps": None, "out_pps": None}}
    f.run(router, T0, 360, lambda k: SlotPlan(
        fields=no_packets, sampled=frozenset({"Gi1"}) if k >= 270 else None))  # fmt: skip
    f.rollup(T0 + timedelta(hours=4))
    section = section_of(f)
    rows = {r.interface: r for r in section.rows}
    assert rows["Gi1"].missing == tuple(f"{r}/packets" for r in ("1h", "24h", "7d", "30d", "400d"))
    assert rows["Gi2"].missing == ()
    assert rows["Gi2"].gaps == ("1h/traffic", "1h/packets", "1h/errors")
    assert count(section, "1h", "traffic").gap == 1
    assert count(section, "24h", "packets").missing == 1
    assert verdict(section, "graphs") is Verdict.FAIL


def test_mvp_acc02_data_newer_than_rollups_is_pending_at_400_days() -> None:
    """E.10, E.28: before the first rollup, the 400 d graph is pending, not empty.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet()
    router = f.device("u-fresh")
    f.interface(router, "Gi1")
    f.run(router, T0, 360)
    section = section_of(f)
    assert count(section, "400d", "traffic").pending == 1
    assert count(section, "30d", "traffic").data == 1
    assert section.rows[0].pending == ("400d/traffic", "400d/packets", "400d/errors")
    assert section.verdict is Verdict.PASS
