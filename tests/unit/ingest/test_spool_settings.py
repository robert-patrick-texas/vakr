"""Spool settings: catalog defaults from E.8, E.21, E.19, and E.24, and cross-option rules.

Dependencies:
    pytest; vakr.ingest.settings; vakr.config.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_settings.py
"""

from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import COLLECTOR
from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.domain.errors import ValidationError
from vakr.ingest.settings import MIB, SpoolSettings, spool_problems
from vakr.paths import InstallPaths

STATE = InstallPaths.under(Path("/srv/node"))


def test_e8_e21_defaults_come_from_the_catalog() -> None:
    """E.8/E.21/E.19: packaged defaults are 1 s / 1,000 records, 64 MiB segments, 10 s, 50,000.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    s = SpoolSettings.from_config(EffectiveConfig({}), STATE)
    assert s.root == Path("/srv/node/var/lib/vakr/spool")
    assert (s.fsync_interval_s, s.fsync_records, s.segment_bytes) == (1.0, 1000, 64 * MIB)
    assert (s.ingest_interval_s, s.ingest_max_rows) == (10.0, 50_000)
    assert (s.warning_pct, s.critical_pct, s.exhausted_pct) == (70, 85, 95)
    assert (s.max_bytes, s.queue_capacity) == (4096 * MIB, 10_000)
    keys = [o.key for o in project.catalog().options if o.key.startswith("spool.")]
    assert len(keys) == 11


def test_configured_values_are_read() -> None:
    """Operator values override defaults, with durations and MiB converted.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    config = EffectiveConfig({"spool": {
        "directory": "/data/spool", "fsync_interval": "250ms", "max_size_mib": 512,
        "segment_size_mib": 8, "ingest_interval": "5s", "ingest_max_rows": 2000,
    }})  # fmt: skip
    s = SpoolSettings.from_config(config, STATE)
    assert (s.root, s.fsync_interval_s, s.max_bytes) == (Path("/data/spool"), 0.25, 512 * MIB)
    assert (s.segment_bytes, s.ingest_interval_s, s.ingest_max_rows) == (8 * MIB, 5.0, 2000)
    assert s.collector_dir(COLLECTOR) == Path(f"/data/spool/{COLLECTOR}")


@pytest.mark.parametrize(
    ("spool", "message"),
    [
        ({"warning_pct": 90}, "spool.warning_pct < spool.critical_pct"),
        ({"max_size_mib": 100, "segment_size_mib": 64}, "at least 4 segments"),
        ({"directory": "/dev/shm/vakr"}, "tmpfs"),  # noqa: S108 - STORE-005: must be refused
        ({"directory": "/run/vakr/spool"}, "tmpfs"),
        ({"directory": "relative/spool"}, "absolute"),
        ({"fsync_interval": "soon"}, "duration"),
        ({"fsync_records": "many"}, "must be an integer"),
    ],
)
def test_cross_option_rules_are_enforced(spool: dict[str, object], message: str) -> None:
    """Combinations that cannot work are refused with the option keys named.

    Args:
        spool: The ``[spool]`` table.
        message: Expected text.

    Returns:
        None

    Raises:
        None
    """
    config = EffectiveConfig({"spool": spool})
    with pytest.raises(ValidationError, match=message):
        SpoolSettings.from_config(config, STATE)
    (problem,) = spool_problems(config)
    assert problem.startswith("spool: ")


def test_packaged_defaults_are_valid_and_positive_values_required(tmp_path: Path) -> None:
    """The defaults pass ``spool_problems``; zero or negative values are refused.

    Args:
        tmp_path: A spool root.

    Returns:
        None

    Raises:
        None
    """
    assert spool_problems(EffectiveConfig({})) == []
    base = SpoolSettings.defaults(tmp_path)
    with pytest.raises(ValidationError, match="positive"):
        base.with_values(fsync_records=0)
    with pytest.raises(ValidationError, match="positive"):
        base.with_values(ingest_interval_s=0.0)


def test_catalog_bounds_reject_operator_typos() -> None:
    """The option catalog bounds each value alone (`vakr config validate`).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    catalog = project.catalog()
    interval = catalog.option("spool.fsync_interval")
    rows = catalog.option("spool.ingest_max_rows")
    assert interval is not None
    assert rows is not None
    assert (interval.spec.minimum, interval.spec.maximum) == (0.1, 10)
    assert rows.spec.maximum == 50_000  # E.19: observations per write batch
