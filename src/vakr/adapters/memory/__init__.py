"""The in-memory reference adapter for every persistence port (5.2 rule 6).

It is deliberately simple: dictionaries, a lock, and full scans. It is the
executable specification the SQLite, PostgreSQL, TimescaleDB, and
ClickHouse adapters are measured against: it passes every contract suite in
``tests/contract``, and application tests use it as their storage double.
It is not durable and not fast; never use it in a service.

Dependencies:
    vakr.adapters.memory.*: The implementation modules.
    vakr.ports: The protocols it satisfies.

Safety:
    Nothing is persisted; data disappears with the process.

Example:
    >>> new_uow, telemetry = memory_uow_factory(), memory_telemetry_factory()
    >>> with new_uow() as uow:
    ...     uow.devices.get_by_name("core1") is None
    True
    >>> telemetry().capabilities.max_batch_rows
    50000
"""

from vakr.adapters.memory.metadata_store import MemoryMetadataStore
from vakr.adapters.memory.metadata_uow import MemoryUnitOfWork
from vakr.adapters.memory.telemetry import MemoryRetentionManager, MemoryTelemetry
from vakr.adapters.memory.telemetry_store import COMPACT_RETENTION, MemoryTelemetryStore
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend, TelemetryBackendFactory

__all__ = [
    "COMPACT_RETENTION",
    "MemoryMetadataStore",
    "MemoryRetentionManager",
    "MemoryTelemetry",
    "MemoryTelemetryStore",
    "MemoryUnitOfWork",
    "memory_telemetry_factory",
    "memory_uow_factory",
]


def memory_uow_factory(store: MemoryMetadataStore | None = None) -> MetadataUnitOfWorkFactory:
    """Return a factory of units of work that all share one store.

    Args:
        store: The store to share, or ``None`` for a new empty one.

    Returns:
        MetadataUnitOfWorkFactory: Each call opens a new unit of work.

    Raises:
        None
    """
    shared = store if store is not None else MemoryMetadataStore()

    def open_uow() -> MetadataUnitOfWork:
        """Open a unit of work on the shared store.

        Args:
            None

        Returns:
            MetadataUnitOfWork: A new unit of work.

        Raises:
            None
        """
        return MemoryUnitOfWork(shared)

    return open_uow


def memory_telemetry_factory(store: MemoryTelemetryStore | None = None) -> TelemetryBackendFactory:
    """Return a factory of backend views that all share one store.

    Args:
        store: The store to share, or ``None`` for a new Compact-retention store.

    Returns:
        TelemetryBackendFactory: Each call returns a new view of the same data.

    Raises:
        None
    """
    shared = store if store is not None else MemoryTelemetryStore()

    def open_backend() -> TelemetryBackend:
        """Open a view of the shared store.

        Args:
            None

        Returns:
            TelemetryBackend: The ports.

        Raises:
            None
        """
        return MemoryTelemetry(shared)

    return open_backend
