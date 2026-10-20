"""Fixtures that bind the telemetry contract suites to the SQLite adapter.

Each test gets a fresh, migrated ``telemetry.db`` *file* in ``tmp_path``
(not ``:memory:``), so WAL mode, the tuned connection pool, the writer gate,
and separate connections per view are real. The store keeps the Compact
retention policy (30 days native, 400 days rollups) the suites expect.

Dependencies:
    pytest; vakr.adapters.sqlite.telemetry.

Example:
    $ uv run pytest tests/contract/sqlite_telemetry
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
    telemetry_migrations,
)
from vakr.ports.telemetry import TelemetryBackendFactory


@pytest.fixture
def telemetry_factory(tmp_path: Path) -> Iterator[TelemetryBackendFactory]:
    """Open views of a fresh, empty, migrated SQLite telemetry file.

    Args:
        tmp_path: Per-test directory for the database file.

    Returns:
        Iterator[TelemetryBackendFactory]: Yields the factory, then closes the store.

    Raises:
        None
    """
    path = tmp_path / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    store = SqliteTelemetryStore(path)
    yield sqlite_telemetry_factory(store)
    store.close()
