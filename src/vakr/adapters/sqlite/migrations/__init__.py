"""Versioned SQL migrations for vakr's SQLite databases (blueprint 5.6, MIG-001..005).

One framework serves every SQLite database vakr owns: metadata now,
telemetry next. A ``SchemaFamily`` names the kind of database, the
``PRAGMA application_id`` that marks its files, and its ordered migration
files, which ship as package data beside the adapter that uses them. A
``SqliteMigrationManager`` implements the ``MigrationManager`` port for one
file: status, startup preflight, ``quick_check``, online backups, and
``migrate``.

- ``model``, ``parse``: migrations, families, and the file format.
- ``journal``: the ``schema_migrations`` table.
- ``status``: deciding the schema state.
- ``backup``: online backups and their verification.
- ``runner``: applying migrations crash-safely.
- ``manager``: the port implementation.

Dependencies:
    The modules listed above.

Safety:
    Migrations run only through ``migrate``; nothing here runs at import time.

Example:
    >>> from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
    >>> SqliteMigrationManager.__name__
    'SqliteMigrationManager'
"""

from vakr.adapters.sqlite.migrations.backup import online_backup, verify_backup
from vakr.adapters.sqlite.migrations.manager import SqliteMigrationManager, refusal
from vakr.adapters.sqlite.migrations.model import Migration, SchemaFamily
from vakr.adapters.sqlite.migrations.parse import load_migrations, parse_migration

__all__ = [
    "Migration",
    "SchemaFamily",
    "SqliteMigrationManager",
    "load_migrations",
    "online_backup",
    "parse_migration",
    "refusal",
    "verify_backup",
]
