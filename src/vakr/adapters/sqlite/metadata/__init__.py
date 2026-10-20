"""SQLite metadata adapter for the Lite and Compact profiles (5.2, 5.3, DB-001..003, DB-007).

It implements ``MetadataUnitOfWork`` and every metadata repository on one
SQLite file, ``/var/lib/vakr/metadata.db``, and passes the shared contract
suites unchanged. Its schema is created and upgraded only by versioned
migrations shipped as package data in ``sql/`` (5.6).

- ``store``: ``SqliteMetadataStore`` (startup preflight, pool, writer gate),
  ``sqlite_uow_factory``, ``metadata_family``, ``metadata_migrations``.
- ``uow``, ``transaction``: the unit of work and its transaction.
- ``reader``, ``table``, ``interfaces``, ``audit``, ``reviews``: the
  repositories; ``reviews`` also holds the metadata change counter.
- ``specs``, ``specs_inventory``, ``specs_interface``, ``paging``: row mapping
  and keyset pages.
- ``health``: the ``metadata_db`` self-health check.

Dependencies:
    The modules listed above; ``vakr.adapters.sqlite`` for the shared layer.

Safety:
    Synchronous: every call blocks on disk I/O and locks. Async callers (M3)
    must run units of work in a bounded worker thread, never on the event
    loop (5.3). The store refuses any schema but the current one.

Example:
    >>> from vakr.adapters.sqlite.metadata import METADATA_FILE, metadata_family
    >>> METADATA_FILE, metadata_family().supported.oldest
    ('metadata.db', 0)
"""

from vakr.adapters.sqlite.metadata.store import (
    METADATA_APPLICATION_ID,
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_family,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.metadata.uow import SqliteUnitOfWork

__all__ = [
    "METADATA_APPLICATION_ID",
    "METADATA_FILE",
    "SqliteMetadataStore",
    "SqliteUnitOfWork",
    "metadata_family",
    "metadata_migrations",
    "sqlite_uow_factory",
]
