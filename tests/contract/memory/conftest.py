"""Fixtures that bind the contract suites to the in-memory adapter.

A new adapter copies this file into its own directory and returns its own
factories. The telemetry fixture runs every suite twice here, once with
rollups and once without, so both branches of the capability-driven tests
execute against the reference implementation.

Dependencies:
    pytest; vakr.adapters.memory.

Example:
    $ uv run pytest tests/contract/memory -k rollups-off
"""

import pytest

from vakr.adapters.memory import (
    MemoryTelemetryStore,
    memory_telemetry_factory,
    memory_uow_factory,
)
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackendFactory


@pytest.fixture
def metadata_uow_factory() -> MetadataUnitOfWorkFactory:
    """Open units of work on a fresh, empty in-memory metadata store.

    Args:
        None

    Returns:
        MetadataUnitOfWorkFactory: The factory.

    Raises:
        None
    """
    return memory_uow_factory()


@pytest.fixture(params=[True, False], ids=["rollups-on", "rollups-off"])
def telemetry_factory(request: pytest.FixtureRequest) -> TelemetryBackendFactory:
    """Open views of a fresh, empty in-memory telemetry store.

    Args:
        request: Carries whether this run enables rollups.

    Returns:
        TelemetryBackendFactory: The factory.

    Raises:
        None
    """
    return memory_telemetry_factory(MemoryTelemetryStore(rollups=request.param))
