"""Migration files and families: header rules, transaction control, version order (5.6).

Dependencies:
    pytest; vakr.adapters.sqlite.migrations.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_migration_files.py
"""

from pathlib import Path

import pytest

from tests.unit.adapters.sqlite.sqlite_testkit import synthetic
from vakr.adapters.sqlite.metadata import metadata_family
from vakr.adapters.sqlite.migrations import SchemaFamily, load_migrations, parse_migration
from vakr.adapters.sqlite.migrations.model import require_sequence
from vakr.domain.errors import ValidationError

HEADER = (
    "-- description: Add a probe table.\n"
    "-- destructive: false\n"
    "-- recovery: Rerun it.\n"
    "-- data-volume: Instant.\n"
    "-- test-impact: Unit tests.\n"
)
BODY = "\nCREATE TABLE probe (x INTEGER);\n"


def test_packaged_initial_migration_carries_every_5_6_note() -> None:
    """5.6: the shipped 0001 has description, recovery, volume, and test impact.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    (initial,) = metadata_family().migrations
    assert (initial.version, initial.name) == (1, "initial")
    assert (initial.destructive, initial.transactional) == (False, True)
    assert "collection policies" in initial.description  # a continuation line joined in
    assert "metadata.db" in initial.recovery
    assert initial.data_volume
    assert "MIG-001" in initial.test_impact
    assert initial.checksum.startswith("sha256:")
    assert "CREATE TRIGGER" in initial.sql  # trigger bodies are not transaction control
    assert initial.info().recovery == initial.recovery


@pytest.mark.parametrize(
    ("name", "text", "words"),
    [
        ("probe.sql", HEADER + BODY, "NNNN_name.sql"),
        ("0000_probe.sql", HEADER + BODY, "from 0001"),
        ("0002_Probe.sql", HEADER + BODY, "NNNN_name.sql"),
        ("0002_probe.sql", HEADER.replace("-- recovery: Rerun it.\n", "") + BODY, "lacks recovery"),
        ("0002_probe.sql", HEADER + "-- owner: me\n" + BODY, "unknown or repeated"),
        ("0002_probe.sql", HEADER + "-- destructive: true\n" + BODY, "unknown or repeated"),
        ("0002_probe.sql", HEADER.replace("false", "maybe") + BODY, "true or false"),
        ("0002_probe.sql", HEADER + "CREATE TABLE early (x INTEGER);\n" + BODY, "not '-- key"),
        ("0002_probe.sql", HEADER + "\n   \n", "no SQL"),
        ("0002_probe.sql", HEADER + "\nBEGIN;\nCREATE TABLE t (x INT);\n", "owns transactions"),
        ("0002_probe.sql", HEADER + "\nBEGIN IMMEDIATE TRANSACTION;\n", "owns transactions"),
        ("0002_probe.sql", HEADER + "\nCREATE TABLE t (x INT);\ncommit;\n", "owns transactions"),
        ("0002_probe.sql", HEADER + "\nSAVEPOINT s;\n", "owns transactions"),
    ],
)
def test_malformed_migration_files_are_rejected(name: str, text: str, words: str) -> None:
    """5.6: a migration that breaks a file rule never loads, so it never runs.

    Args:
        name: File name.
        text: File content.
        words: Expected phrase in the error.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError) as caught:
        parse_migration(name, text)
    assert words in str(caught.value)


def test_header_continuations_and_defaults() -> None:
    """Continuation lines join with a space; ``transactional`` defaults to true.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = HEADER.replace("Rerun it.\n", "Rerun\n--   it after fixing the cause.\n") + BODY
    migration = parse_migration("0007_probe.sql", text)
    assert migration.recovery == "Rerun it after fixing the cause."
    assert migration.transactional
    assert migration.sql == BODY.removeprefix("\n")


def test_versions_must_run_without_gaps(tmp_path: Path) -> None:
    """5.6: families need versions 1..N; directories load in order and skip other files.

    Args:
        tmp_path: Directory of migration files.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError, match="at least one"):
        require_sequence(())
    with pytest.raises(ValidationError, match="without gaps"):
        require_sequence((synthetic(2, "late", "SELECT 1;"),))
    (tmp_path / "0002_second.sql").write_text(HEADER + BODY, encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text(HEADER + BODY, encoding="utf-8")
    (tmp_path / "README.txt").write_text("not a migration", encoding="utf-8")
    assert [m.name for m in load_migrations(tmp_path)] == ["first", "second"]
    (tmp_path / "0004_fourth.sql").write_text(HEADER + BODY, encoding="utf-8")
    with pytest.raises(ValidationError, match="0004"):
        load_migrations(tmp_path)


def test_schema_family_rules() -> None:
    """A family publishes its range and refuses an application ID outside the header field.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    family = metadata_family()
    assert family.supported.oldest == 0
    assert family.supported.newest == len(family.migrations)
    assert family.migration(0) is None
    assert family.migration(family.supported.newest + 1) is None
    for bad in (0, 2**31):
        with pytest.raises(ValidationError):
            SchemaFamily("x", bad, family.migrations)
