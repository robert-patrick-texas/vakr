"""Device list freshness and collector filters; a device's interfaces by interest (12.3, UI-017).

12.3 requires the device list to filter by freshness and by collector. The
API takes ``freshness`` (repeatable: ``fresh``, ``stale``, ``never``) and
``collector`` (a collector UUID: devices that position has evidence for),
beside the bucket and grouping filters, and ``counts`` follow the filter.
UI-017 shows a device's primary-interest interfaces in full and pages
through the others: ``interest`` filters a device's interface list.

Dependencies:
    tests.unit.api.api_testkit, tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_device_filters.py
"""

from tests.unit.api.api_testkit import ApiKit
from vakr.domain.enums import InterestClass
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.users import Role
from vakr.query import DeviceListRequest, FreshnessFilter
from vakr.query.listing import freshness_of


def names(client: object, **params: object) -> list[str]:
    """List the device names one filtered request answers.

    Args:
        client: The test client.
        **params: Query parameters.

    Returns:
        list[str]: Names in the answer's order.

    Raises:
        None
    """
    answer = client.get("/api/v1/devices", params=params)  # ty: ignore[unresolved-attribute]
    assert answer.status_code == 200
    return [row["name"] for row in answer.json()["rows"]]


def test_12_3_freshness_filter_keeps_fresh_stale_or_never_observed() -> None:
    """12.3: ``freshness`` selects by the newest evidence's E.3 class, or no evidence at all.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    fleet = kit.fleet
    fleet.poll(fleet.device("fresh1"), ago=30)
    fleet.poll(fleet.device("old1"), ago=900)
    fleet.device("new1")
    client = kit.socket_client(Role.OPERATOR)
    assert names(client, freshness="fresh") == ["fresh1"]
    assert names(client, freshness="stale") == ["old1"]
    assert names(client, freshness="never") == ["new1"]
    assert sorted(names(client, freshness=["fresh", "never"])) == ["fresh1", "new1"]
    bad = client.get("/api/v1/devices", params={"freshness": "recent"})
    assert bad.status_code == 422
    assert bad.json()["fields"][0]["field"] == "freshness.0"


def test_12_3_collector_filter_keeps_devices_that_position_has_evidence_for() -> None:
    """12.3, MPOS-008: ``collector`` keeps devices with evidence from that position only.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(positions=("east", "west"))
    fleet = kit.fleet
    fleet.poll(fleet.device("both"), position="east")
    with fleet.uow() as uow:
        both = uow.devices.get_by_name("both")
    assert both is not None
    fleet.poll(both, position="west")
    fleet.poll(fleet.device("eastonly"), position="east")
    fleet.poll(fleet.device("westonly"), position="west")
    client = kit.socket_client(Role.OPERATOR)
    east, west = fleet.collectors["east"].id, fleet.collectors["west"].id
    assert sorted(names(client, collector=str(east))) == ["both", "eastonly"]
    assert sorted(names(client, collector=str(west))) == ["both", "westonly"]
    counts = client.get("/api/v1/devices", params={"collector": str(west)}).json()["counts"]
    assert sum(counts.values()) == 2


def test_freshness_of_classifies_rows_and_requests_default_to_no_filter() -> None:
    """12.3: a row without evidence is ``never``; a plain request filters nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.fleet.device("dark")
    view = kit.context.overview.device_list(DeviceListRequest())
    assert [freshness_of(r) for r in view.rows] == [FreshnessFilter.NEVER]
    assert DeviceListRequest().freshness == frozenset()
    assert DeviceListRequest().collector is None


def test_ui017_a_device_lists_its_interfaces_by_interest_in_priority_order() -> None:
    """UI-017, UI-006: ``interest`` splits primary from other interfaces; sticky stays first.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    fleet = kit.fleet
    core = fleet.device("core")
    fleet.interface(core, "Gi1", if_index=1)
    fleet.interface(core, "Gi2", if_index=2, annotation=InterfaceAnnotation(sticky=True))
    primary = InterfaceAnnotation(interest=InterestClass.PRIMARY)
    fleet.interface(core, "Te1", if_index=3, annotation=primary)
    client = kit.socket_client(Role.OPERATOR)
    url = f"/api/v1/devices/{core.id}/interfaces"

    def listed(**params: str) -> list[str]:
        """Name the interfaces one request lists.

        Args:
            **params: Query parameters.

        Returns:
            list[str]: Names in order.

        Raises:
            None
        """
        return [r["ref"]["name"] for r in client.get(url, params=params).json()["rows"]]

    assert listed(interest="primary") == ["Te1"]
    assert listed(interest="secondary") == ["Gi2", "Gi1"]
    assert listed() == ["Gi2", "Te1", "Gi1"]
    assert client.get(url, params={"interest": "tertiary"}).status_code == 422
