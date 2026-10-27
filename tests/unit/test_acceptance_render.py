"""Text output of the acceptance reports: every section, the ``!`` marks, and the RESULT line.

Dependencies:
    tests.unit.query.acceptance_testkit; vakr.cli.acceptance_render.

Example:
    $ uv run pytest tests/unit/test_acceptance_render.py
"""

from datetime import timedelta

from tests.unit.query.acceptance_testkit import OK, T0, Fleet, SlotPlan
from vakr.cli.acceptance_render import render
from vakr.domain.quality import Quality
from vakr.query.acceptance_request import AcceptanceRequest

NOW = T0 + timedelta(hours=6, minutes=5)
SIX_HOURS = AcceptanceRequest(timedelta(hours=6), limit=1)


def fleet() -> Fleet:
    """Build a fleet with one unexplained interface gap and one discontinuity spike.

    Args:
        None

    Returns:
        Fleet: The fleet.

    Raises:
        None
    """
    f = Fleet()
    router = f.device("core1")
    for name in ("Gi1", "Gi2", "Gi3", "Gi4", "Gi5", "Gi6"):
        f.interface(router, name)
    plans = {
        100: SlotPlan(sampled=frozenset()),
        101: None,
        220: SlotPlan(sample_quality={"Gi1": Quality.BASELINE_DISCONTINUITY}),
        221: SlotPlan(sample_quality={"Gi1": Quality.RATE_EXCEEDS_CAPACITY}),
    }
    f.run(router, T0, 360, lambda k: plans.get(k, OK))
    f.health(T0, 360)
    f.rollup(T0 + timedelta(hours=4))
    return f


def test_mvp_acc10_text_marks_unexplained_rows_and_caps_the_list() -> None:
    """MVP-ACC-10: unexplained rows carry ``!``; a capped list says how many more there are.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    lines = render(fleet().queries(NOW).gaps(SIX_HOURS))
    assert any(line.startswith(" ! interface core1 Gi1, Gi2, Gi3, Gi4, Gi5 (+1)") for line in lines)
    assert any("more rows (raise --limit or use --json)" in line for line in lines)
    assert lines[-1] == "RESULT MVP-ACC-10: FAIL"


def test_mvp_acc01_and_acc02_text_sections() -> None:
    """MVP-ACC-01 and MVP-ACC-02: per-day drift lines, range counts, spikes marked ``!``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    queries = fleet().queries(NOW)
    schedule = render(queries.schedule(SIX_HOURS))
    assert any(line.startswith("  day 2026-10-01: ") for line in schedule)
    assert any("[INCOMPLETE] drift" in line for line in schedule)
    coverage = render(queries.coverage(SIX_HOURS))
    assert any(line.startswith("    1h traffic  data 6") for line in coverage)
    assert any(line.startswith(" ! discontinuity core1 Gi1") for line in coverage)
    assert coverage[-1] == "RESULT MVP-ACC-02: FAIL"
