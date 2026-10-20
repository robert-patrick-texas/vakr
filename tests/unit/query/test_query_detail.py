"""Device detail: summary facts, interface order, rates, utilization, and missing data (TUI-T02).

Dependencies:
    pytest; tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/query/test_query_detail.py
"""

import pytest

from tests.unit.query.query_testkit import Fleet
from vakr.cli.status_json import detail_document, list_document
from vakr.domain.enums import (
    BaselineState,
    IcmpState,
    IfAdminStatus,
    IfOperStatus,
    InterestClass,
    SnmpState,
)
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.quality import Quality
from vakr.logic.capacity import IF_SPEED_MAX
from vakr.query import DeviceDetailView, InterfaceRow


def detail(fleet: Fleet, key: str = "sw1") -> DeviceDetailView:
    """Return a device's detail view, which must exist.

    Args:
        fleet: The fleet.
        key: Device name or UUID.

    Returns:
        DeviceDetailView: The view.

    Raises:
        LookupError: If the device does not exist.
    """
    view = fleet.queries().device_detail(key)
    if view is None:
        raise LookupError(key)
    return view


def by_name(view: DeviceDetailView) -> dict[str, InterfaceRow]:
    """Index interface rows by name.

    Args:
        view: The device detail.

    Returns:
        dict[str, InterfaceRow]: Rows by name.

    Raises:
        None
    """
    return {i.name: i for i in view.interfaces}


def test_interfaces_are_ordered_sticky_wan_primary_rank_then_natural_name() -> None:
    """TUI-T02, TUI-006, E.29 decision 6.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    plain = [fleet.interface(d, n) for n in ("Gi1/0/10", "Gi1/0/2")]
    del plain
    fleet.interface(d, "Te1/1/1", annotation=InterfaceAnnotation(interest=InterestClass.PRIMARY))
    fleet.interface(d, "Gi0/0/0", annotation=InterfaceAnnotation(is_wan_circuit=True))
    fleet.interface(d, "Gi1/0/9", annotation=InterfaceAnnotation(sticky=True))
    fleet.interface(d, "Gi1/0/5", annotation=InterfaceAnnotation(display_rank=1))
    fleet.poll(d)
    view = detail(fleet)
    order = [i.name for i in view.interfaces]
    assert order == ["Gi1/0/9", "Gi0/0/0", "Te1/1/1", "Gi1/0/5", "Gi1/0/2", "Gi1/0/10"]
    assert [i.markers for i in view.interfaces][:3] == [("sticky",), ("WAN",), ("primary",)]
    assert view.interfaces_total == 6
    assert not view.interfaces_truncated


def test_rates_utilization_and_capacity_label() -> None:
    """TUI-005, RATE-008: rates, utilization against the named denominator, packets, errors.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    i = fleet.interface(d, "Gi1/0/1", if_alias="uplink to core", if_high_speed=1000)
    fleet.poll(d)
    fleet.sample(i, in_bps=250_000_000.0, out_bps=0.0, in_pps=1200.0, out_pps=900.0,
                 in_error_delta=3, out_error_delta=0,
                 admin_status=IfAdminStatus.UP, oper_status=IfOperStatus.UP)  # fmt: skip
    row = by_name(detail(fleet))["Gi1/0/1"]
    assert (row.alias, row.state.text) == ("uplink to core", "up/up")
    assert (row.in_rate.text, row.out_rate.text) == ("250 Mb/s", "0 b/s")
    assert (row.in_util.text, row.in_util.value) == ("25.0%", 25.0)
    assert (row.out_util.text, row.out_util.value) == ("0.0%", 0.0)  # a measured zero is zero
    assert (row.in_pps.text, row.errors.text, row.errors.value) == ("1.20 kpps", "3/0", 3)
    assert row.capacity.text == "1.00 Gb/s (ifHighSpeed)"
    assert row.age.text == "30 s"


@pytest.mark.parametrize(
    ("speeds", "annotation", "capacity"),
    [
        ({}, InterfaceAnnotation(), None),
        ({"if_speed": IF_SPEED_MAX}, InterfaceAnnotation(), None),
        ({"if_speed": 0, "if_high_speed": 0}, InterfaceAnnotation(), None),
        (
            {},
            InterfaceAnnotation(capacity_override_bps=50_000_000),
            "50.0 Mb/s (capacity override)",
        ),
        ({"if_speed": 1_544_000, "if_high_speed": 2}, InterfaceAnnotation(), "1.54 Mb/s (ifSpeed)"),
    ],
)
def test_utilization_is_never_computed_against_an_unknown_capacity(
    speeds: dict[str, int], annotation: InterfaceAnnotation, capacity: str | None
) -> None:
    """RATE-008, RATE-010: no speed, a saturated ifSpeed, or zero gives "speed unknown".

    Args:
        speeds: ``if_speed`` / ``if_high_speed`` fields.
        annotation: Operator annotation.
        capacity: Expected capacity text, or ``None`` when unknown.

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    i = fleet.interface(d, "Gi1/0/1", annotation=annotation, **speeds)
    fleet.sample(i, in_bps=1_000_000.0)
    row = by_name(detail(fleet))["Gi1/0/1"]
    assert row.in_rate.value == 1_000_000.0
    if capacity is None:
        assert (row.in_util.value, row.in_util.reason) == (None, "speed unknown")
        assert (row.capacity.text, row.capacity.reason) == ("—", "speed unknown")
    else:
        assert row.capacity.text == capacity
        assert row.in_util.value is not None


def test_tunnels_show_traffic_without_a_percentage() -> None:
    """E.17 #15 (owner decision): a tunnel's nominal speed gives no percentage; an override does.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("rtr1")
    tunnel = fleet.interface(d, "Tunnel10", if_type=131, if_speed=100_000)
    pinned = fleet.interface(
        d,
        "Tunnel20",
        if_type=131,
        if_speed=100_000,
        annotation=InterfaceAnnotation(capacity_override_bps=10_000_000),
    )
    for i in (tunnel, pinned):
        fleet.sample(i, in_bps=2_000_000.0)
    rows = by_name(detail(fleet, "rtr1"))
    t10, t20 = rows["Tunnel10"], rows["Tunnel20"]
    assert t10.in_rate.value == 2_000_000.0  # the traffic is shown
    assert t10.in_util.value is None
    assert t10.in_util.reason is not None
    assert "logical interface" in t10.in_util.reason
    assert t10.capacity.text == "—"
    assert t20.in_util.value == pytest.approx(20.0)
    assert "capacity override" in t20.capacity.text


@pytest.mark.parametrize(
    ("quality", "reason"),
    [
        (Quality.BASELINE_UPTIME_RESET, "agent restarted; counters reset"),
        (Quality.BASELINE_DISCONTINUITY, "interface counters were reset"),
        (Quality.BASELINE_FIRST_SAMPLE, "first sample; a rate needs two"),
        (Quality.BASELINE_GAP, "too long since the previous sample"),
        (Quality.NONE, "counter not reported"),
    ],
)
def test_missing_rates_say_why_and_are_never_zero(quality: Quality, reason: str) -> None:
    """Invariant 1, RATE-004, 18.3: "—" with the reason, for every derived metric.

    Args:
        quality: Sample flags.
        reason: Expected operator reason.

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    i = fleet.interface(d, "Gi1/0/1", if_high_speed=1000)
    baseline = BaselineState.VALID if quality is Quality.NONE else BaselineState.INVALIDATED
    fleet.sample(i, in_bps=None, out_bps=None, quality=quality, baseline_state=baseline)
    row = by_name(detail(fleet))["Gi1/0/1"]
    for reading in (row.in_rate, row.out_rate, row.in_util, row.in_pps, row.errors):
        assert (reading.text, reading.value, reading.reason) == ("—", None, reason)


def test_interfaces_without_a_sample_and_stale_samples() -> None:
    """TIME-006: no sample says so; a stale sample is marked stale.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    fleet.interface(d, "Gi1/0/1")
    old = fleet.interface(d, "Gi1/0/2", if_high_speed=100)
    fleet.sample(old, ago=600)
    rows = by_name(detail(fleet))
    assert rows["Gi1/0/1"].in_rate.reason == "no sample from east"
    assert rows["Gi1/0/2"].in_rate.text == "1.50 Mb/s (stale)"
    assert rows["Gi1/0/2"].age.text == "10 min (stale)"


def test_summary_facts_cover_tui004() -> None:
    """TUI-T02, TUI-004: system, reachability, SNMP, uptime, poll, collector, site, tags.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    site = fleet.site("Dallas DC1", "DAL1")
    fleet.poll(fleet.device("sw1", site_id=site.id, comment="core switch"))
    view = detail(fleet)
    facts = dict(view.facts)
    assert facts["Site"].text == "Dallas DC1"
    assert facts["Collector"].text == "east"
    assert facts["Probes"].text == "5/5 answered, 0.0% loss"
    assert facts["Latency"].text.startswith("0.90 ms / 1.00 ms / 1.00 ms / 1.10 ms")
    assert facts["SNMP requests"].text.startswith("3 sent, 3 answered, 0 timed out")
    assert facts["sysUpTime"].text == "41 d 16 h"
    assert facts["Comment"].text == "core switch"
    assert facts["System description"].value is None
    assert facts["Last poll"].text == "30 s ago, complete"
    assert any("UPTIME-003" in n for n in view.notes)


def test_detail_of_an_unreachable_device_and_lookup_by_uuid() -> None:
    """TUI-004: ICMP-only evidence; jitter needs two replies; lookup by UUID.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    fleet.poll(d, icmp=IcmpState.UNREACHABLE, snmp=None)
    view = detail(fleet, str(d.id))
    facts = dict(view.facts)
    assert view.row.status.label == "Down"
    assert facts["Latency"].reason == "no probe answered"
    assert facts["Jitter"].reason == "needs at least two replies"
    assert facts["SNMP requests"].reason == "the latest poll made no SNMP request"
    assert fleet.queries().device_detail("no-such-device") is None


def test_detail_warnings_name_quality_flags_and_telemetry_trouble() -> None:
    """TUI-T04, 18.3: quality flags in words; missing telemetry is a warning, not a crash.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("sw1")
    fleet.poll(d, snmp=SnmpState.PARTIAL, icmp_quality=Quality.PARTIAL_LOSS)
    view = detail(fleet)
    assert "1 of 5 probes lost (routine under Control Plane Policing)" in view.warnings
    offline = fleet.queries(telemetry=False).device_detail("sw1")
    assert offline is not None
    assert offline.row.status.label == "Unknown — telemetry unavailable"
    assert "no telemetry database yet" in offline.warnings


def test_views_never_carry_credential_references() -> None:
    """TUI-010: a device's credential reference never reaches a list row, detail, or JSON.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    fleet.poll(fleet.device("sw1", credential_ref="branch-ro-ref"))
    listing = fleet.queries().device_list()
    view = detail(fleet)
    documents = (list_document(listing, 0), detail_document(view, 0))
    assert all("branch-ro-ref" not in text for text in (repr(listing), repr(view), *documents))
