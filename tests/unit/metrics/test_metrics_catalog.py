"""The catalog: fixed label sets equal their enums, builds refuse others, docs list every family.

E.33 #7 bounds cardinality by fixing every label value; the sets are copied
as strings so ``vakr.metrics`` imports no scheduler or domain module, and
this test keeps each copy equal to its enum. ``docs/operations/metrics.md``
must name exactly the catalogued families (OBS-T01's documentation half).

Dependencies:
    pytest; vakr.metrics.catalog.

Example:
    $ uv run pytest tests/unit/metrics/test_metrics_catalog.py
"""

import re
from pathlib import Path

import pytest

from vakr.domain.enums import AdminState, CollectorState, InterfaceLifecycle, StatusBucket
from vakr.ingest.capacity import CapacityState
from vakr.metrics import catalog_spec as spec
from vakr.metrics.catalog import CATALOG, build
from vakr.metrics.requests import route_group, status_code
from vakr.scheduler.step_stats import STEP_PHASES
from vakr.scheduler.steps import StepStatus

DOC = Path(__file__).parents[3] / "docs/operations/metrics.md"


@pytest.mark.parametrize(
    ("copy", "enum"),
    [
        (spec.STEP_STATUSES, StepStatus),
        (spec.COLLECTOR_STATES, CollectorState),
        (spec.SPOOL_STATES, CapacityState),
        (spec.ADMIN_STATES, AdminState),
        (spec.LIFECYCLES, InterfaceLifecycle),
        (spec.BUCKETS, StatusBucket),
    ],
    ids=lambda x: getattr(x, "__name__", "set"),
)
def test_e33_label_sets_equal_their_enums(copy: frozenset[str], enum: type) -> None:
    """E.33 #7: each fixed label set is exactly its enum's values.

    Args:
        copy: The set in ``catalog_spec``.
        enum: The enum it copies.

    Returns:
        None

    Raises:
        None
    """
    assert copy == {m.value for m in enum}  # ty: ignore[not-iterable]


def test_e33_step_names_are_the_scheduler_phases() -> None:
    """E.33: the step label is exactly the phases the step statistics count.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert {p.value for p in STEP_PHASES} == spec.STEPS


def test_e33_build_refuses_values_outside_the_fixed_sets() -> None:
    """E.33 #7: a device name, an unknown state, or a wrong label count is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="not allowed"):
        build("vakr_devices", [(("core1.example.net",), 1)])
    with pytest.raises(ValueError, match="not allowed"):
        build("vakr_build_info", [(("1.0 with spaces",), 1)])
    with pytest.raises(ValueError, match="not allowed"):
        build("vakr_collector_spool_bytes", [(("extra",), 1)])
    with pytest.raises(ValueError, match="not allowed"):
        build("vakr_collector_polls_started_total", [((), -1)])
    assert build("vakr_devices", [(("enabled",), None)]).samples == ()  # unknown is absent


def test_e33_paths_and_codes_map_to_fixed_values() -> None:
    """E.33 #7: no path or unexpected status becomes a label.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    paths = ["/api/v1/metrics", "/api/v1/auth/login", "/api/v1/auth/kiosk", "/api/v1/devices",
             "/api/v1/devices/x/rediscovery", "/api/v1/exports/rankings/errors",
             "/api/v1/interfaces/x/history/traffic", "/", "/api/v1/anything/../x"]  # fmt: skip
    assert {route_group(p) for p in paths} <= spec.ROUTE_GROUPS
    assert [status_code(s) for s in (200, 418, 0, 503)] == ["200", "other", "none", "503"]


def test_obs_t01_the_metrics_page_lists_exactly_the_catalog() -> None:
    """OBS-T01: every family is documented in metrics.md, and the page names no other.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    documented = set(re.findall(r"^\| `(vakr_[a-z0-9_]+)`", DOC.read_text(), re.MULTILINE))
    assert documented == set(CATALOG) | {"vakr_metrics_truncated"}
