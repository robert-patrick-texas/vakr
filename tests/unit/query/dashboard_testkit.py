"""A fleet and a dashboard for the custom-dashboard tests (UI-004, DASH-006, DASH-T04).

``dashboard_fleet`` builds two sites, a tag, and a group around three
devices, with WAN, primary, ordinary, and retired interfaces and current
samples; ``every_widget`` returns one widget of every kind over it.

Dependencies:
    tests.unit.query.query_testkit, tests.contract.contract_samples.

Example:
    >>> kit = dashboard_fleet()
    >>> sorted(kit.interfaces)[:2]
    ['core1 Gi0/0/1', 'core1 Gi0/0/2']
"""

from dataclasses import dataclass, field

from tests.contract import contract_samples as samples
from tests.unit.query.query_testkit import Fleet
from vakr.domain.catalog import Group, Tag
from vakr.domain.dashboard_widgets import (
    DeviceCriteria,
    DeviceSelection,
    InterfaceCriteria,
    InterfaceSelection,
    PinnedInterfaces,
    RankingKind,
    RankingWidget,
    Widget,
)
from vakr.domain.enums import InterestClass, InterfaceLifecycle
from vakr.domain.ids import InterfaceId, new_id
from vakr.domain.interfaces import Interface, InterfaceAnnotation
from vakr.domain.inventory import Site


@dataclass
class DashboardFleet:
    """The fleet and the records the tests refer to.

    Attributes:
        fleet: Stores and clock.
        dallas: Site of ``core1``.
        austin: Site of ``edge1`` and ``edge2``.
        core_tag: Tag on ``edge1``.
        backbone: Group of ``core1`` and ``edge1``.
        interfaces: ``"<device> <ifName>"`` to the interface.
    """

    fleet: Fleet
    dallas: Site
    austin: Site
    core_tag: Tag
    backbone: Group
    interfaces: dict[str, Interface] = field(default_factory=dict)

    def iid(self, key: str) -> InterfaceId:
        """Return an interface's ID by ``"<device> <ifName>"``.

        Args:
            key: Such as ``"core1 Gi0/1/0"``.

        Returns:
            InterfaceId: Its UUID.

        Raises:
            KeyError: If no such interface was built.
        """
        return self.interfaces[key].id


def dashboard_fleet() -> DashboardFleet:
    """Build the fleet.

    Args:
        None

    Returns:
        DashboardFleet: Three devices; ``core1 Gi0/0/9`` is retired; everything else has a sample.

    Raises:
        None
    """
    fleet = Fleet()
    dal, aus = fleet.site("Dallas DC1", "DAL1"), fleet.site("Austin", "AUS1")
    tag = fleet.add("tags", samples.tag("core"))
    group = fleet.add("groups", samples.group("backbone"))
    core = fleet.device("core1", site_id=dal.id, groups=frozenset({group.id}))
    edge = fleet.device("edge1", site_id=aus.id, tags=frozenset({tag.id}),
                        groups=frozenset({group.id}))  # fmt: skip
    other = fleet.device("edge2", site_id=aus.id, poll_address="192.0.2.30")
    kit = DashboardFleet(fleet, dal, aus, tag, group)
    wan = InterfaceAnnotation(is_wan_circuit=True, provider="ACME", circuit_id="CKT-77",
                              contracted_bps=50_000_000)  # fmt: skip
    specs = (
        (core, "Gi0/0/1", {}), (core, "Gi0/0/2", {}), (core, "Gi0/1/0", {"annotation": wan}),
        (edge, "Gi0/0/3", {"annotation": InterfaceAnnotation(interest=InterestClass.PRIMARY)}),
        (other, "Gi0/0/4", {}),
        (core, "Gi0/0/9", {"lifecycle": InterfaceLifecycle.RETIRED}),
    )  # fmt: skip
    for device, name, extra in specs:
        iface = fleet.interface(device, name, if_type=6, if_high_speed=1000, **extra)
        kit.interfaces[f"{device.name} {name}"] = iface
    for device in (core, edge, other):
        fleet.poll(device)
    for key, iface in kit.interfaces.items():
        if not key.endswith("Gi0/0/9"):
            fleet.sample(iface, in_bps=10_000_000.0, out_bps=2_000_000.0)
    return kit


def every_widget(kit: DashboardFleet) -> tuple[Widget, ...]:
    """Return one widget of every kind over the fleet.

    Args:
        kit: The fleet.

    Returns:
        tuple[Widget, ...]: Pinned (with a retired and a missing interface), an
        Austin selection, a backbone device selection, and a WAN error ranking.

    Raises:
        None
    """
    pinned = (kit.iid("edge1 Gi0/0/3"), kit.iid("core1 Gi0/0/9"), new_id(InterfaceId),
              kit.iid("core1 Gi0/1/0"))  # fmt: skip
    return (
        PinnedInterfaces("Uplinks", pinned),
        InterfaceSelection("Austin", InterfaceCriteria(site_id=kit.austin.id), limit=1),
        DeviceSelection("Backbone", DeviceCriteria(group_id=kit.backbone.id)),
        RankingWidget("WAN errors", RankingKind.ERRORS, InterfaceCriteria(wan_only=True)),
    )
