"""Builders for the M4 history, ranking, catalog, and overview tests (no secrets, no devices).

A ``Fleet`` from ``query_testkit`` holds memory stores and a manual clock at
``NOW``. These helpers add the interfaces and minute-by-minute samples the
M4 services read, for any position, and build each service over the fleet
with the display timezone fixed to UTC.

Dependencies:
    tests.unit.query.query_testkit, tests.contract.contract_samples.
    vakr.query: The services under test.

Example:
    >>> fleet, device, iface = physical_fleet()
    >>> iface.if_high_speed
    1000
"""

from datetime import timedelta
from typing import Any

from tests.contract import contract_samples as samples
from tests.unit.query.query_testkit import NOW, Fleet
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.query import QuerySettings
from vakr.query.catalog import CatalogQueries
from vakr.query.history import HistoryQueries
from vakr.query.overview import OverviewQueries
from vakr.query.rankings import RankingQueries

UTC_SETTINGS = QuerySettings(timezone="UTC")
ETHERNET = 6
TUNNEL = 131


def physical_fleet(positions: tuple[str, ...] = ("east",)) -> tuple[Fleet, Device, Interface]:
    """Build a fleet with one device and one 1 Gb/s Ethernet port.

    Args:
        positions: Collector positions.

    Returns:
        tuple: The fleet, the device ``core1``, and its port ``Gi0/0/1``.

    Raises:
        None
    """
    fleet = Fleet(positions)
    device = fleet.device("core1")
    iface = fleet.interface(device, "Gi0/0/1", if_type=ETHERNET, if_high_speed=1000)
    return fleet, device, iface


def minutes(
    fleet: Fleet,
    iface: Interface,
    ago_minutes: range,
    *,
    position: str = "east",
    skip: frozenset[int] = frozenset(),
    **fields: Any,
) -> None:
    """Write one interface sample per minute, ``ago`` minutes before ``NOW``.

    Args:
        fleet: The fleet.
        iface: The interface.
        ago_minutes: Minutes before ``NOW`` to write a sample at.
        position: Which collector wrote them.
        skip: Minutes to leave out, making an outage.
        **fields: Sample fields; callables get the minute and return the value.

    Returns:
        None

    Raises:
        None
    """
    col = fleet.collectors[position].id
    rows = []
    for m in ago_minutes:
        if m in skip:
            continue
        values = {k: v(m) if callable(v) else v for k, v in fields.items()}
        rows.append(
            samples.sample(iface.device_id, iface.id, col, NOW - timedelta(minutes=m), **values)
        )
    fleet.write(*rows)


def history(fleet: Fleet, *, telemetry: bool = True, **settings: Any) -> HistoryQueries:
    """Return ``HistoryQueries`` over the fleet.

    Args:
        fleet: The fleet.
        telemetry: False to simulate a node without a telemetry store.
        **settings: ``QuerySettings`` overrides.

    Returns:
        HistoryQueries: The service.

    Raises:
        None
    """
    chosen = QuerySettings(timezone="UTC", **settings)
    return HistoryQueries(fleet.uow, fleet.telemetry if telemetry else None, chosen, fleet.clock)


def rankings(fleet: Fleet, **settings: Any) -> RankingQueries:
    """Return ``RankingQueries`` over the fleet.

    Args:
        fleet: The fleet.
        **settings: ``QuerySettings`` overrides.

    Returns:
        RankingQueries: The service.

    Raises:
        None
    """
    chosen = QuerySettings(timezone="UTC", **settings)
    return RankingQueries(fleet.uow, fleet.telemetry, chosen, fleet.clock)


def catalog(fleet: Fleet, *, telemetry: bool = True) -> CatalogQueries:
    """Return ``CatalogQueries`` over the fleet.

    Args:
        fleet: The fleet.
        telemetry: False to simulate a node without a telemetry store.

    Returns:
        CatalogQueries: The service.

    Raises:
        None
    """
    return CatalogQueries(
        fleet.uow, fleet.telemetry if telemetry else None, UTC_SETTINGS, fleet.clock
    )


def overview(fleet: Fleet, **kwargs: Any) -> OverviewQueries:
    """Return ``OverviewQueries`` over the fleet.

    Args:
        fleet: The fleet.
        **kwargs: ``incidents`` or ``index``.

    Returns:
        OverviewQueries: The service.

    Raises:
        None
    """
    return OverviewQueries(fleet.uow, fleet.telemetry, UTC_SETTINGS, fleet.clock, **kwargs)
