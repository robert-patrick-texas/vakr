"""E.35 bounds of the acceptance reports: windows, listed rows, and reads cut short.

Every report refuses a window outside 1 hour to 30 days and a row limit
outside 1 to 10,000; lists at most ``limit`` rows while its summary still
counts every one; and calls itself incomplete, never passed, when a read
was cut short by a bound.

Dependencies:
    pytest; tests.unit.query.acceptance_testkit; vakr.query.acceptance_*.

Example:
    $ uv run pytest tests/unit/query/test_acceptance_bounds.py
"""

from datetime import timedelta
from typing import cast

import pytest

from tests.unit.query.acceptance_testkit import NOT_ATTEMPTED, OK, T0, Fleet
from vakr.domain.errors import ValidationError
from vakr.ports.telemetry.acceptance import AcceptanceBackend, Marks
from vakr.query.acceptance_limits import MAX_ROWS, MAX_WINDOW, MIN_WINDOW
from vakr.query.acceptance_request import AcceptanceRequest
from vakr.query.acceptance_views import CollectorGaps, Verdict

NOW = T0 + timedelta(hours=6, minutes=5)


@pytest.mark.parametrize(
    ("window", "limit"),
    [(MIN_WINDOW - timedelta(seconds=1), 10), (MAX_WINDOW + timedelta(seconds=1), 10),
     (timedelta(days=7), 0), (timedelta(days=7), MAX_ROWS + 1)],
)  # fmt: skip
def test_e35_requests_outside_the_bounds_are_refused(window: timedelta, limit: int) -> None:
    """E.35 bounds: windows from 1 h to 30 d, rows from 1 to 10,000.

    Args:
        window: Requested window.
        limit: Requested row limit.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        AcceptanceRequest(window, limit)


def test_e35_rows_are_capped_but_every_gap_is_counted() -> None:
    """E.35: ``limit`` caps listed rows; ``rows_total`` and ``by_cause`` count all of them.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet(rollups=False)
    device = f.device("core1")
    f.run(device, T0, 360, lambda k: NOT_ATTEMPTED if k % 10 == 0 else OK)
    f.health(T0, 360)
    report = f.queries(NOW).gaps(AcceptanceRequest(timedelta(hours=6), limit=5))
    (section,) = report.collectors
    assert isinstance(section, CollectorGaps)
    assert (len(section.rows), section.rows_total) == (5, 36)
    assert section.by_cause == {"collector_busy": 36}
    assert report.verdict is Verdict.PASS


def test_e35_a_read_cut_short_makes_the_report_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    """E.35: a truncated read never passes; the evidence check says incomplete.

    Args:
        monkeypatch: Truncates the poll reads.

    Returns:
        None

    Raises:
        None
    """
    f = Fleet(rollups=False)
    f.run(f.device("core1"), T0, 360)
    f.health(T0, 360)
    queries = f.queries(NOW)
    real = f.telemetry

    def truncated():
        """Open a backend whose poll reads report truncation.

        Args:
            None

        Returns:
            object: The backend.

        Raises:
            None
        """
        backend = cast(AcceptanceBackend, real())
        inner = backend.acceptance.poll_marks
        monkeypatch.setattr(backend.acceptance, "poll_marks",
                            lambda *a, **k: Marks(inner(*a, **k).items, True))  # fmt: skip
        return backend

    queries._telemetry = truncated
    report = queries.gaps(AcceptanceRequest(timedelta(hours=6)))
    (section,) = report.collectors
    assert isinstance(section, CollectorGaps)
    evidence = next(c for c in section.checks if c.name == "evidence")
    assert evidence.verdict is Verdict.INCOMPLETE
    assert report.verdict is Verdict.INCOMPLETE


def test_e35_unknown_collector_is_refused() -> None:
    """E.35: ``--collector`` must name a collector.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    f = Fleet(rollups=False)
    with pytest.raises(ValidationError):
        f.queries(NOW).schedule(AcceptanceRequest(timedelta(hours=6), collector="west"))
