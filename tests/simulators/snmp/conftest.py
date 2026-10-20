"""Fixtures for the SNMP simulator tests: one snmpsim responder for the session.

The responder serves eight routers, two switches, and a router variant with
only the legacy ``ipAddrTable``, each under a community generated for this
test run. Starting it once keeps the default test run fast.

Dependencies:
    pytest: Session-scoped fixtures.
    tests.simulators.snmp: The dataset and the snmpsim runner.

Example:
    $ uv run pytest tests/simulators/snmp
"""

from collections.abc import Iterator

import pytest

from tests.simulators.snmp.iosxe_dataset import device_records, router, switch
from tests.simulators.snmp.simkit import fake_community
from tests.simulators.snmp.snmpsim_server import SnmpsimFleet, snmpsim


@pytest.fixture(scope="session")
def snmpsim_fleet(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SnmpsimFleet]:
    """Run snmpsim over a small fleet for the whole test session.

    Args:
        tmp_path_factory: Session temporary directories.

    Returns:
        Iterator[SnmpsimFleet]: Yields the running fleet.

    Raises:
        RuntimeError: If snmpsim does not start.
    """
    specs = (
        *(router(n) for n in range(1, 9)),
        switch(1),
        switch(2),
        router(9, ip_mib="legacy"),
    )
    communities = {spec.name: fake_community() for spec in specs}
    devices = {communities[spec.name]: device_records(spec) for spec in specs}
    with snmpsim(tmp_path_factory.mktemp("snmpsim"), devices) as port:
        yield SnmpsimFleet(port, communities, specs)
