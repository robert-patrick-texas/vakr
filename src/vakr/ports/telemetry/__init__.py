"""Telemetry persistence ports and the backend bundle the contract suites receive (5.2).

A ``TelemetryBackend`` groups every telemetry port of one store with its
``TelemetryCapabilities``. Adapters construct one; application code and the
contract suites (``telemetry_factory`` fixture) use only this protocol.

Dependencies:
    vakr.ports.telemetry.query, storage, results: The definitions.

Safety:
    See ``vakr.ports``: no driver types cross these interfaces.

Example:
    >>> from vakr.ports.telemetry import TelemetryBackend, TelemetryCapabilities
    >>> "capabilities" in dir(TelemetryBackend)
    True
"""

from collections.abc import Callable
from typing import Protocol

from vakr.ports.telemetry.query import CurrentStateQuery, ObservationQuery
from vakr.ports.telemetry.results import (
    RetentionPolicy,
    RetentionReport,
    TelemetryCapabilities,
    WriteResult,
)
from vakr.ports.telemetry.storage import (
    CollectorHealthRepository,
    ObservationWriter,
    RetentionManager,
    RollupRepository,
)

__all__ = [
    "CollectorHealthRepository",
    "CurrentStateQuery",
    "ObservationQuery",
    "ObservationWriter",
    "RetentionManager",
    "RetentionPolicy",
    "RetentionReport",
    "RollupRepository",
    "TelemetryBackend",
    "TelemetryBackendFactory",
    "TelemetryCapabilities",
    "WriteResult",
]


class TelemetryBackend(Protocol):
    """Every telemetry port of one store, plus what the store can do."""

    @property
    def capabilities(self) -> TelemetryCapabilities:
        """What this backend supports.

        Args:
            None

        Returns:
            TelemetryCapabilities: The capability object.

        Raises:
            None
        """
        ...

    @property
    def writer(self) -> ObservationWriter:
        """The batch writer.

        Args:
            None

        Returns:
            ObservationWriter: The port.

        Raises:
            None
        """
        ...

    @property
    def query(self) -> ObservationQuery:
        """Series and raw reads.

        Args:
            None

        Returns:
            ObservationQuery: The port.

        Raises:
            None
        """
        ...

    @property
    def current(self) -> CurrentStateQuery:
        """Current-state reads.

        Args:
            None

        Returns:
            CurrentStateQuery: The port.

        Raises:
            None
        """
        ...

    @property
    def rollups(self) -> RollupRepository:
        """Rollup storage.

        Args:
            None

        Returns:
            RollupRepository: The port.

        Raises:
            None
        """
        ...

    @property
    def collector_health(self) -> CollectorHealthRepository:
        """Latest collector health.

        Args:
            None

        Returns:
            CollectorHealthRepository: The port.

        Raises:
            None
        """
        ...

    @property
    def retention(self) -> RetentionManager:
        """Retention.

        Args:
            None

        Returns:
            RetentionManager: The port.

        Raises:
            None
        """
        ...


type TelemetryBackendFactory = Callable[[], TelemetryBackend]
"""Opens a view of one telemetry store; the contract-suite fixture type."""
