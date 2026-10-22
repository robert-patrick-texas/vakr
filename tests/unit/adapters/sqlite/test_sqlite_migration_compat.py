"""The static check that an ``additive`` migration really is additive (E.14).

An older release keeps writing a database a newer one left behind when
the newer release's changes were additive, so ``compat: additive`` must
mean only additions older code can ignore. ``additive_problems`` reads the
SQL; every shipped telemetry migration that declares ``additive`` must
pass it, and must not be destructive (MIG-005).

Dependencies:
    pytest; vakr.adapters.sqlite.migrations, telemetry.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_migration_compat.py
"""

import pytest

from vakr.adapters.sqlite.migrations.compat import additive_problems, statements
from vakr.adapters.sqlite.migrations.model import Compat
from vakr.adapters.sqlite.telemetry import telemetry_family

ADDITIVE = [
    "CREATE TABLE repl_cursor (source BLOB NOT NULL PRIMARY KEY, seq INTEGER NOT NULL) STRICT;",
    "CREATE TABLE IF NOT EXISTS t (x INT); CREATE UNIQUE INDEX t_x ON t (x);",
    "CREATE INDEX obs_by_seq ON obs_icmp_hot (ts_us);",
    "ALTER TABLE obs_icmp_hot ADD COLUMN note TEXT;",
    "ALTER TABLE obs_icmp_hot ADD note TEXT NOT NULL DEFAULT '';",
    "CREATE VIEW v AS SELECT 1 AS one;",
    "CREATE TABLE t (x INT);\nINSERT INTO t (x) VALUES (1); -- seed; with a comment\n",
    "CREATE TABLE t (x INT);\nCREATE TRIGGER t_ai AFTER INSERT ON t BEGIN SELECT 1; SELECT 2; END;",
    "CREATE TABLE t (note TEXT DEFAULT 'a -- not a comment; nor a split');",
]
BREAKING = [
    ("DROP TABLE obs_icmp_hot;", "only CREATE"),
    ("DROP INDEX IF EXISTS some_index;", "only CREATE"),
    ("ALTER TABLE obs_icmp_hot RENAME TO obs_icmp_old;", "only CREATE"),
    ("ALTER TABLE obs_icmp_hot DROP COLUMN state;", "only CREATE"),
    ("ALTER TABLE obs_icmp_hot ADD COLUMN must INTEGER NOT NULL;", "NOT NULL column"),
    ("UPDATE telemetry_state SET value = 0;", "only CREATE"),
    ("DELETE FROM vocab_code;", "only CREATE"),
    ("PRAGMA user_version = 9;", "only CREATE"),
    ("INSERT INTO telemetry_state (name, value) VALUES ('x', 1);", "existing table"),
    ("INSERT INTO partition_template (family, version, ddl) VALUES ('obs_icmp', 2, 'x');",
     "existing table"),
    ("CREATE UNIQUE INDEX dedupe ON obs_icmp_hot (observation_uuid);", "UNIQUE index"),
    ("CREATE TRIGGER no AFTER INSERT ON obs_icmp_hot BEGIN SELECT 1; END;", "trigger"),
]  # fmt: skip


@pytest.mark.parametrize("sql", ADDITIVE)
def test_e14_additions_older_releases_can_ignore_pass(sql: str) -> None:
    """E.14: new tables, views, indexes, nullable or defaulted columns, and rows in new tables.

    Args:
        sql: An additive migration body.

    Returns:
        None

    Raises:
        None
    """
    assert additive_problems(sql) == []


@pytest.mark.parametrize(("sql", "says"), BREAKING)
def test_e14_anything_an_older_release_would_trip_on_is_named(sql: str, says: str) -> None:
    """E.14: drops, renames, rewrites, pragmas, and rows, triggers, or uniqueness on old tables.

    Args:
        sql: A migration body.
        says: Part of the expected problem.

    Returns:
        None

    Raises:
        None
    """
    problems = additive_problems(sql)
    assert len(problems) == 1
    assert says in problems[0]
    assert problems[0].startswith(sql.split()[0])  # the statement is named


def test_statements_keep_trigger_bodies_and_strings_whole() -> None:
    """The splitter respects trigger bodies, string literals, and comments.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    sql = "CREATE TABLE t (x TEXT DEFAULT 'a;b');\n-- c; d\nCREATE TRIGGER g AFTER INSERT ON t "
    sql += "BEGIN SELECT 1; END;\nCREATE INDEX i ON t (x);"
    assert len(statements(sql)) == 3


def test_shipped_additive_telemetry_migrations_are_additive() -> None:
    """E.14, MIG-005: every shipped ``compat: additive`` migration passes and is not destructive.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    family = telemetry_family()
    for m in family.migrations:
        if m.compat is Compat.ADDITIVE:
            assert additive_problems(m.sql) == [], f"{m.version:04d} {m.name} is not additive"
            assert not m.destructive, f"{m.version:04d} {m.name} is additive and destructive"
        if m.version < (family.compat_from or 0):
            assert m.compat is None  # 0001 and 0002 count as breaking without a header
