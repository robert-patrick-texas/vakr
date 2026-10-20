"""SQLite adapters for the Lite and Compact profiles (blueprint 5.1, 5.3).

Shared building blocks, reused by the metadata adapter now and the
telemetry adapter next:

- ``connection``: open a file with WAL, foreign keys, a busy timeout, and
  ``synchronous=NORMAL``; explicit transactions; the ``SqliteSettings`` bounds.
- ``encoding``: UUID blobs and integer-microsecond timestamps.
- ``errors``: translate ``sqlite3`` exceptions into domain errors.
- ``writer``: ``WriterGate``, the bounded single-writer path.
- ``pool``: ``ConnectionPool``, a bounded set of reusable connections.
- ``migrations``: versioned SQL migrations with a journal, crash
  detection, a destructive-step gate, and online backups (5.6).
- ``metadata``: the ``MetadataUnitOfWork`` implementation (DB-001..003).

Dependencies:
    sqlite3: The driver. Only ``vakr.adapters.*`` may import it (ARCH-001).

Safety:
    These adapters are synchronous and block on disk I/O and locks. Async
    code (M3 onwards) must never call them on the event loop: run units of
    work in a bounded worker thread, and route telemetry writes through
    one dedicated writer thread (5.3).

Example:
    >>> from vakr.adapters.sqlite.connection import DEFAULT_SETTINGS
    >>> DEFAULT_SETTINGS.busy_timeout_ms
    5000
"""
