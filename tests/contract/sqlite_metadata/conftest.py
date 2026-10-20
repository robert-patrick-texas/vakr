"""Fixtures that bind the metadata contract suites to the SQLite adapter.

Each test gets a fresh database *file* in ``tmp_path`` (not ``:memory:``),
migrated to the current schema, so WAL mode, the connection pool, and
separate connections per unit of work are real.

Dependencies:
    pytest; vakr.adapters.sqlite.metadata.

Example:
    $ uv run pytest tests/contract/sqlite_metadata
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.ports.metadata import MetadataUnitOfWorkFactory


@pytest.fixture
def metadata_uow_factory(tmp_path: Path) -> Iterator[MetadataUnitOfWorkFactory]:
    """Open units of work on a fresh, empty, migrated SQLite metadata file.

    Args:
        tmp_path: Per-test directory for the database file.

    Returns:
        Iterator[MetadataUnitOfWorkFactory]: Yields the factory, then closes the store.

    Raises:
        None
    """
    path = tmp_path / METADATA_FILE
    metadata_migrations(path).migrate()
    store = SqliteMetadataStore(path)
    yield sqlite_uow_factory(store)
    store.close()
