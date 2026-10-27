"""Requests and shared evidence of the acceptance reports (E.35 windows and bounds).

``AcceptanceRequest`` carries a report's window and row limit and refuses
anything outside the E.35 bounds. ``evidence_of`` reads one collector's
health reports (its activity and spool fill level) and combines them with
its telemetry-loss incidents; ``notes_of`` states the limits of the
evidence that apply to a whole report.

Dependencies:
    vakr.domain, vakr.ports.telemetry: Observations and the acceptance port.
    vakr.query.acceptance_evidence, acceptance_limits, snapshot.

Safety:
    Read-only and bounded.

Example:
    >>> AcceptanceRequest().window.days
    7
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.domain.catalog import Collector
from vakr.domain.enums import ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.observations import CollectorHealthObservation
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.acceptance import AcceptanceBackend
from vakr.query.acceptance_evidence import CollectorEvidence, LossRange
from vakr.query.acceptance_limits import (
    DEFAULT_ROWS,
    MAX_HEALTH_REPORTS,
    MAX_LOSS_INCIDENTS,
    MAX_ROWS,
    MAX_WINDOW,
    MIN_WINDOW,
    SCHEDULE_DEFAULT,
    SPOOL_FULL_PCT,
)
from vakr.query.acceptance_subjects import as_time
from vakr.query.snapshot import pages

type LossReader = Callable[[CollectorId], Sequence[LossRange]]
"""Returns one collector's telemetry-loss incidents (the CLI reads its spool's log)."""


@dataclass(frozen=True, slots=True)
class AcceptanceRequest:
    """What to judge.

    Attributes:
        window: How far back from ``now - SETTLE`` to look.
        limit: Rows to list per collector.
        collector: Only this collector (by name), or every collector.
    """

    window: timedelta = SCHEDULE_DEFAULT
    limit: int = DEFAULT_ROWS
    collector: str | None = None

    def __post_init__(self) -> None:
        """Validate the bounds (E.35).

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the window or limit is out of range.
        """
        if not MIN_WINDOW <= self.window <= MAX_WINDOW:
            raise ValidationError(f"the window must be from {MIN_WINDOW} to {MAX_WINDOW.days} "
                                  "days", field="window")  # fmt: skip
        if not 1 <= self.limit <= MAX_ROWS:
            raise ValidationError(f"the row limit must be from 1 to {MAX_ROWS}", field="limit")


def evidence_of(
    backend: AcceptanceBackend, collector: Collector, window: tuple[int, int],
    losses: Sequence[LossRange], *, local: bool,
) -> CollectorEvidence:  # fmt: skip
    """Read one collector's health reports and loss incidents.

    Args:
        backend: The telemetry backend.
        collector: The collector.
        window: ``(start_us, end_us)``.
        losses: The collector's telemetry-loss incidents.
        local: Whether the collector is this node's.

    Returns:
        CollectorEvidence: The evidence.

    Raises:
        StorageError: If the store is unavailable.
    """
    tw = TimeWindow(as_time(window[0]), as_time(window[1]))
    reports, _cut = pages(lambda p: backend.query.read_raw(
        ObservationType.COLLECTOR_HEALTH, collector.id, tw, p, collector_id=collector.id),
        MAX_HEALTH_REPORTS)  # fmt: skip
    health = [r for r in reports if isinstance(r, CollectorHealthObservation)]
    times = [to_epoch_us(r.timestamp) for r in health]
    full = [
        to_epoch_us(r.timestamp)
        for r in health
        if r.spool_used_pct is not None and r.spool_used_pct >= SPOOL_FULL_PCT
    ]
    kept = list(losses)[-MAX_LOSS_INCIDENTS:]
    return CollectorEvidence.build(times, full, kept, window=window, local=local)


def notes_of(truncated: Sequence[str], frontier: datetime | None, end_us: int) -> tuple[str, ...]:
    """Describe the limits of the evidence that apply to every collector.

    Args:
        truncated: Inventory lists cut short by a bound.
        frontier: The rollup frontier, or ``None``.
        end_us: Window end.

    Returns:
        tuple[str, ...]: Operator sentences.

    Raises:
        None
    """
    notes = [f"inventory read cut short: {t}" for t in truncated]
    if frontier is None:
        notes.append("no rollups yet: every sample in the window was read natively")
    elif to_epoch_us(frontier) < end_us - 6 * 3_600_000_000:
        notes.append(f"rollups end at {frontier.isoformat()}; later samples were read natively")
    return tuple(notes)
