"""Text output of ``vakr acceptance schedule|gaps|coverage`` (MVP-ACC-01, -02, -10).

Each report prints its window, one block per collector with the measured
figures and every check, and ends with one line an operator can grep:
``RESULT MVP-ACC-01: PASS`` (or ``FAIL`` or ``INCOMPLETE``). Unexplained gaps
are listed first and marked ``!`` so they stand out.

Dependencies:
    vakr.query.acceptance_views, acceptance_coverage_views, jsonable: Views and times.

Safety:
    Prints names, counts, and times only.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.query.acceptance_views import AcceptanceReport, Verdict
    >>> t = datetime(2026, 10, 1, tzinfo=UTC)
    >>> render(AcceptanceReport("gaps", "MVP-ACC-10", t, t, t, (), Verdict.INCOMPLETE))[-1]
    'RESULT MVP-ACC-10: INCOMPLETE'
"""

from vakr.query.acceptance_coverage_views import CollectorCoverage
from vakr.query.acceptance_views import (
    AcceptanceReport,
    Check,
    CollectorGaps,
    CollectorSchedule,
)
from vakr.query.jsonable import iso_utc


def _checks(checks: tuple[Check, ...]) -> list[str]:
    """Render checks, one per line.

    Args:
        checks: The checks.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    return [f"  [{c.verdict.value.upper():10}] {c.name}: {c.detail}" for c in checks]


def _schedule(c: CollectorSchedule) -> list[str]:
    """Render one collector's MVP-ACC-01 section.

    Args:
        c: The section.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    share = "—" if c.on_time_share is None else f"{c.on_time_share:.4%}"
    lines = [
        f"collector {c.collector}: {c.devices} devices, {c.records} poll records, "
        f"{c.healthy} healthy-device slots, on time {share}",
        f"  start offset p50 {c.offset_p50_ms} ms, |p99| {c.offset_p99_ms} ms, "
        f"max {c.offset_max_ms} ms; excluded {c.excluded or 'none'}; "
        f"slots without a record {c.slots_without_record}",
        f"  intervals {c.intervals}; drift slopes: offset {c.offset_slope_ms_per_day} "
        f"ms/day, phase {c.phase_slope_ms_per_day} ms/day",
    ]  # fmt: skip
    lines += [f"  day {d.day}: {d.polls} polls, median offset {d.median_offset_ms} ms, "
              f"median phase {d.median_phase_ms} ms" for d in c.days]  # fmt: skip
    lines += [f"  overlap {o.device} at {iso_utc(o.scheduled_at)}: {o.overlap_ms} ms"
              for o in c.overlap_rows]  # fmt: skip
    return lines + _checks(c.checks)


def _gaps(c: CollectorGaps) -> list[str]:
    """Render one collector's MVP-ACC-10 section.

    Args:
        c: The section.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    lines = [f"collector {c.collector}: {c.devices} devices, {c.interfaces} interfaces, "
             f"{c.rows_total} gap rows, {c.unexplained} unexplained"]  # fmt: skip
    lines += [f"  subject-slots by cause: {c.by_cause or 'none'}"]
    lines += [f"  note: {n}" for n in c.notes]
    for r in c.rows:
        mark = " " if r.explained else "!"
        who = r.device if not r.subjects else f"{r.device} {', '.join(r.subjects[:5])}" + (
            f" (+{len(r.subjects) - 5})" if len(r.subjects) > 5 else "")  # fmt: skip
        lines.append(f" {mark} {r.kind:9} {who}: {iso_utc(r.start)} → {iso_utc(r.end)} "
                     f"({r.slots} slots) {r.cause}: {r.detail}")  # fmt: skip
    if len(c.rows) < c.rows_total:
        lines.append(f"  … {c.rows_total - len(c.rows)} more rows (raise --limit or use --json)")
    return lines + _checks(c.checks)


def _coverage(c: CollectorCoverage) -> list[str]:
    """Render one collector's MVP-ACC-02 section.

    Args:
        c: The section.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    lines = [f"collector {c.collector}: {c.interfaces} monitored interfaces; "
             f"{c.reloads} reloads, {c.discontinuities} discontinuities, {c.spikes} spikes, "
             f"{c.over_capacity} other over-capacity samples"]  # fmt: skip
    lines += [f"  note: {n}" for n in c.notes]
    lines += [f"  {r.range:>4} {r.family:8} data {r.data}, gap {r.gap}, pending {r.pending}, "
              f"missing {r.missing}" for r in c.ranges]  # fmt: skip
    for row in c.rows:
        parts = [f"missing {' '.join(row.missing)}" if row.missing else "",
                 f"gap {' '.join(row.gaps)}" if row.gaps else "",
                 f"pending {' '.join(row.pending)}" if row.pending else ""]  # fmt: skip
        mark = "!" if row.missing else " "
        lines.append(f" {mark} {row.device} {row.interface}: {'; '.join(p for p in parts if p)}")
    for e in c.events:
        mark = "!" if e.spikes else " "
        lines.append(f" {mark} {e.kind} {e.device} {e.interface} at {iso_utc(e.at)}: "
                     f"{e.resets} reset, {e.spikes} spikes; {e.detail}")  # fmt: skip
    return lines + _checks(c.checks)


def render(report: AcceptanceReport) -> list[str]:
    """Render a report as text lines, ending with the ``RESULT`` line.

    Args:
        report: The report.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    lines = [f"vakr acceptance {report.report} ({report.criterion}): "
             f"{iso_utc(report.window_start)} → {iso_utc(report.window_end)}"]  # fmt: skip
    for section in report.collectors:
        if isinstance(section, CollectorSchedule):
            lines += _schedule(section)
        elif isinstance(section, CollectorGaps):
            lines += _gaps(section)
        elif isinstance(section, CollectorCoverage):
            lines += _coverage(section)
    if not report.collectors:
        lines.append("no collectors to judge")
    lines.append(f"RESULT {report.criterion}: {report.verdict.value.upper()}")
    return lines
