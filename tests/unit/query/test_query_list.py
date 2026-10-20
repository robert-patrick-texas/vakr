"""Device-list order, search, filter, paging, bounds, freshness, and health (TUI-T01, 7.7, E.29).

Dependencies:
    pytest; tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/query/test_query_list.py
"""

from typing import Any

import pytest

from tests.unit.query.query_testkit import Fleet, mixed_fleet
from vakr.domain.enums import (
    CollectorState,
    InterfaceLifecycle,
    StatusBucket,
)
from vakr.domain.errors import ValidationError
from vakr.query import DeviceListRequest, DeviceSort, QueryLimits
from vakr.query.listing import ordered


def names(fleet: Fleet, **request: Any) -> list[str]:
    """List device names in list order.

    Args:
        fleet: The fleet.
        **request: ``DeviceListRequest`` fields.

    Returns:
        list[str]: Names.

    Raises:
        None
    """
    view = fleet.queries().device_list(DeviceListRequest(**request))
    return [r.name for r in view.rows]


def test_default_order_is_attention_active_offline_then_severity_then_natural_name() -> None:
    """TUI-T01, TUI-002, 7.7: Down before Unknown before Packet loss; sw2 before sw10.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    assert names(fleet) == ["core", "old", "edge", "sw2", "sw10", "lab"]
    counts = fleet.queries().device_list().counts
    assert (counts.attention, counts.active, counts.offline) == (3, 2, 1)


def test_order_is_deterministic_whatever_the_input_order() -> None:
    """E.29 decision 1: the sort key is total, so shuffled rows sort identically.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rows = list(mixed_fleet().queries().device_list().rows)
    for sort in DeviceSort:
        once = [r.device_id for r in ordered(rows, sort)]
        assert [r.device_id for r in ordered(reversed(rows), sort)] == once


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        (DeviceSort.NAME, ["core", "edge", "lab", "old", "sw2", "sw10"]),
        (DeviceSort.SITE, ["sw2", "sw10", "core", "edge", "lab", "old"]),
        (DeviceSort.AGE, ["lab", "old", "core", "edge", "sw2", "sw10"]),
    ],
)
def test_other_sorts(sort: DeviceSort, expected: list[str]) -> None:
    """TUI-003: name, site (none last), and age (never observed, then oldest, first).

    Args:
        sort: The order.
        expected: Names in that order.

    Returns:
        None

    Raises:
        None
    """
    assert names(mixed_fleet(), sort=sort) == expected


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("dallas", ["sw10"]),
        ("austin", ["sw2"]),
        ("WAN", ["sw2"]),
        ("down", ["core"]),
        ("198.51.100", ["core"]),
        ("unknown", ["old"]),
        ("nothing-matches", []),
    ],
)
def test_search_matches_name_address_site_tag_and_status(search: str, expected: list[str]) -> None:
    """TUI-003: case-insensitive substring search over the visible fields.

    Args:
        search: Search text.
        expected: Matching names.

    Returns:
        None

    Raises:
        None
    """
    assert names(mixed_fleet(), search=search) == expected


def test_bucket_filter_and_paging() -> None:
    """TUI-003: filter by bucket; pages are slices of one ordered list.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    assert names(fleet, buckets=frozenset({StatusBucket.OFFLINE})) == ["lab"]
    assert names(fleet, buckets=frozenset({StatusBucket.ACTIVE, StatusBucket.OFFLINE})) == [
        "sw2",
        "sw10",
        "lab",
    ]
    view = fleet.queries().device_list(DeviceListRequest(offset=2, limit=2))
    assert ([r.name for r in view.rows], view.total, view.offset) == (["edge", "sw2"], 6, 2)


@pytest.mark.parametrize(
    "fields", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"search": "x" * 257}]
)
def test_requests_are_bounded(fields: dict[str, Any]) -> None:
    """E.19: page size 1-500; search at most 256 characters.

    Args:
        fields: An out-of-range request field.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        DeviceListRequest(**fields)


def test_scan_bound_reports_truncation() -> None:
    """E.29: a list reads at most ``max_devices`` and says when more exist.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    view = fleet.queries(limits=QueryLimits(max_devices=4)).device_list()
    assert view.truncated
    assert view.total == 4
    assert not fleet.queries().device_list().truncated


def test_stale_evidence_is_marked_stale_and_never_current() -> None:
    """TIME-005/006, E.3: age is shown, values say "(stale)", and the status is Unknown.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rows = {r.name: r for r in mixed_fleet().queries().device_list().rows}
    old = rows["old"]
    # The SNMP observation is 2 s after the poll's ICMP probes: age is from the newest evidence.
    assert (old.freshness.value, old.age.value, old.age.text) == ("stale", 898.0, "14 min")
    assert (old.icmp.text, old.icmp.stale) == ("reachable (stale)", True)
    assert old.snmp.text == "responding (stale)"
    fresh = rows["sw2"]
    assert (fresh.freshness.value, fresh.icmp.text, fresh.icmp.stale) == (
        "fresh",
        "reachable",
        False,
    )


def test_missing_values_are_dashes_with_reasons_never_zero() -> None:
    """Invariant 1: an unreachable device has 100% loss but no latency, uptime, or SNMP value.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rows = {r.name: r for r in mixed_fleet().queries().device_list().rows}
    core = rows["core"]
    assert (core.loss.text, core.loss.value) == ("100.0%", 100.0)
    for reading in (core.latency, core.uptime, core.snmp):
        assert (reading.text, reading.value) == ("—", None)
        assert reading.reason
    lab = rows["lab"]
    assert lab.age.value is None
    assert lab.age.reason == "never observed"
    assert lab.last_poll.reason == "no poll recorded"
    assert lab.interfaces.reason == "no interfaces in inventory"


def test_list_row_shows_site_tags_interfaces_and_interval() -> None:
    """12.3, TUI-004: the columns an operator scans.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    dal = fleet.site("Dallas DC1", "DAL1")
    d = fleet.device("sw1", site_id=dal.id, poll_interval_s=120)
    fleet.interface(d, "Gi1/0/1")
    fleet.interface(d, "Gi1/0/2", lifecycle=InterfaceLifecycle.INACTIVE)
    fleet.poll(d)
    row = fleet.queries().device_list().rows[0]
    assert (row.site.text, row.interfaces.text, row.interval.text) == ("Dallas DC1", "1/2", "60 s")
    assert row.latency.text == "1.00 ms"
    assert row.uptime.text == "41 d 16 h"
    assert row.last_poll.text == "30 s ago, complete"


def test_health_view_reports_collectors_and_spool() -> None:
    """TUI-009, OBS-002: collector health with age and spool level; stale reports are unavailable.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet(("east", "west"))
    fleet.health(position="east", spool_used_pct=2.5)
    fleet.health(position="west", ago=900, state=CollectorState.HEALTHY)
    view = fleet.queries().device_list()
    by = {c.position: c for c in view.health.collectors}
    assert (by["east"].state.text, by["east"].age.text, by["east"].spool.text) == (
        "healthy",
        "10 s",
        "2.5%",
    )
    assert by["west"].state.text == "unavailable"
    assert view.health.metadata.text == "ok"
