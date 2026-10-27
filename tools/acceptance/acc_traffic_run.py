# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Run MVP-ACC-03 over every MRTG target: match, align, compare, explain, judge.

For each comparable MRTG target this matches the application's interface
(``acc_match``), estimates MRTG's poll phase, and walks MRTG's windows in
the period both tools have data for. A window is *excluded* when MRTG has
no value (``mrtg_unknown``, or ``maxbytes_clip`` when the traffic was above
MRTG's limit) or the application measured less than ``min_coverage`` of it
(``vakr_gap``, ``device_restart``, or ``ifindex_changed``); it *agrees*
when both directions are within the tolerance; otherwise it gets an
explanation category. Verdicts:

* ``pass``: at least ``min_windows`` compared, the volume over them within
  the tolerance in both directions, and no ``unexplained`` window;
* ``review``: the volume agrees but some windows are unexplained;
* ``fail``: the volume differs by more than the tolerance;
* ``insufficient``: too few windows compared to judge.

Dependencies:
    acc_traffic, acc_explain, acc_match, acc_bundles, acc_config.

Safety:
    Pure computation over loaded data.

Example:
    >>> verdict(300, 0, (0.001, -0.004), TrafficRules())
    'pass'
"""

from dataclasses import dataclass, field

from acc_bundles import Inbox, MrtgTarget
from acc_config import TrafficRules
from acc_explain import QUALITY_FLAGS, Evidence, explain, within
from acc_match import match_interface
from acc_series import Cumulative
from acc_traffic import TrafficResult, edge_uncertainty, estimate_phase, mrtg_view, series_for
from acc_vakr import Interface, Sample


@dataclass
class TrafficReport:
    """MVP-ACC-03 over all targets.

    Attributes:
        results: Compared targets.
        unmatched: ``(target, problem)`` for comparable targets without a match.
        skipped: ``(target, note)`` for targets MRTG graphs in a non-comparable way.
    """

    results: list[TrafficResult] = field(default_factory=list)
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def verdict(compared: int, unexplained: int, volume: tuple[float | None, float | None],
            rules: TrafficRules) -> str:  # fmt: skip
    """Judge one target.

    Args:
        compared: Windows compared.
        unexplained: Unexplained windows.
        volume: Relative volume difference in and out.
        rules: Tolerance and minimum windows.

    Returns:
        str: ``pass``, ``review``, ``fail``, or ``insufficient``.

    Raises:
        None
    """
    if compared < rules.min_windows:
        return "insufficient"
    if any(v is None or abs(v) > rules.tolerance for v in volume):
        return "fail"
    return "review" if unexplained else "pass"


def _marked(marks: list[Sample], start: float, end: float) -> tuple[frozenset[str], bool]:
    """Collect quality flags and ``ifIndex`` changes of samples overlapping a span.

    Args:
        marks: Flagged samples.
        start: Span start.
        end: Span end.

    Returns:
        tuple: Flags, and whether a sample was read from another ``ifIndex``.

    Raises:
        None
    """
    flags: set[str] = set()
    changed = False
    for s in marks:
        if s.start < end and s.end > start:
            flags |= s.flags
            changed = changed or not s.flags
    return frozenset(flags), changed


def _period(target: MrtgTarget, iface: Interface, step: int) -> tuple[float, float]:
    """Return the span both tools have data for.

    Args:
        target: The MRTG target.
        iface: The interface.
        step: Window length.

    Returns:
        tuple[float, float]: Start and end, epoch seconds (empty when disjoint).

    Raises:
        None
    """
    if not target.windows or not iface.samples:
        return 0.0, 0.0
    first = min(s.start for s in iface.samples.values())
    last = max(s.end for s in iface.samples.values())
    return max(first, min(target.windows) - step), min(last, max(target.windows))


def _evidence(target: MrtgTarget, series: tuple[Cumulative, Cumulative], end: int, phase: int,
              rules: TrafficRules) -> Evidence:  # fmt: skip
    """Gather what is known about one window at one phase.

    Args:
        target: The MRTG target.
        series: In and out integrals.
        end: Window end.
        phase: MRTG's poll phase.
        rules: The rules.

    Returns:
        Evidence: The values, coverage, and limits (flags are added by the caller).

    Raises:
        None
    """
    step = rules.step_s
    views = [mrtg_view(c, end, step, phase) for c in series]
    span = (end - 2 * step + phase % step, end + phase % step)
    window = target.windows[end]
    previous = target.windows.get(end - step)
    before = [mrtg_view(c, end - step, step, phase)[0] for c in series]
    return Evidence(
        (window.values[0], window.values[1]), (views[0][0], views[1][0]),
        None if previous is None else (previous.values[0], previous.values[1]),
        (before[0], before[1]), min(v[1] for v in views), rules.tolerance,
        peak=(series[0].maximum(*span), series[1].maximum(*span)),
        limits=(target.limit_in, target.limit_out), counter_bits=target.counter_bits,
        step=step, unknaszero=target.unknaszero,
        quantum=rules.quantum_bps if target.rateup else 0.0,
        edge=(edge_uncertainty(series[0], end, step, phase),
              edge_uncertainty(series[1], end, step, phase)),
    )  # fmt: skip


def _jitter(target: MrtgTarget, series: tuple[Cumulative, Cumulative], end: int, phase: int,
            rules: TrafficRules) -> bool:  # fmt: skip
    """Report whether the window agrees at a phase within the jitter allowance.

    Args:
        target: The MRTG target.
        series: In and out integrals.
        end: Window end.
        phase: The estimated phase.
        rules: Jitter allowance and step.

    Returns:
        bool: True when some nearby phase makes both directions agree.

    Raises:
        None
    """
    m = target.windows[end].values
    for k in range(-rules.jitter_s, rules.jitter_s + 1, rules.phase_step_s):
        values = [mrtg_view(c, end, rules.step_s, phase + k)[0] for c in series]
        if all(within(m[d], values[d], rules.tolerance) for d in (0, 1)):
            return True
    return False


def compare_traffic(target: MrtgTarget, iface: Interface, rules: TrafficRules) -> TrafficResult:
    """Compare one MRTG target with one interface.

    Args:
        target: The MRTG target.
        iface: The matched interface.
        rules: The rules.

    Returns:
        TrafficResult: Counts, volume, listed windows, verdict.

    Raises:
        None
    """
    cin, cout, marks = series_for(iface, target)
    series = (cin, cout)
    phase = estimate_phase(series, target, rules)
    result = TrafficResult(target.name, iface, phase)
    start, end = _period(target, iface, rules.step_s)
    sums = [[0.0, 0.0], [0.0, 0.0]]
    for t in sorted(w for w in target.windows if start <= w - rules.step_s and w <= end):
        result.windows += 1
        e = _evidence(target, series, t, phase, rules)
        span = (t - 2 * rules.step_s + phase % rules.step_s, t + phase % rules.step_s)
        e.flags, e.index_changed = _marked(marks, *span)
        row = {"end": t, "mrtg": e.mrtg, "vakr": e.vakr, "coverage": round(e.coverage, 3)}
        if any(m is None for m in e.mrtg):
            gone = e.flags or e.index_changed
            category = "maxbytes_clip" if not gone and _limit_peak(e) else "mrtg_unknown"
        elif e.coverage < rules.min_coverage or any(v is None for v in e.vakr):
            restart = bool(e.flags & QUALITY_FLAGS)
            category = "ifindex_changed" if e.index_changed else (
                "device_restart" if restart else "vakr_gap")  # fmt: skip
        else:
            category = ""
        if category:
            result.excluded[category] += 1
            result.rows.append({**row, "category": category, "excluded": True})
            continue
        result.compared += 1
        for d in (0, 1):
            sums[d][0] += e.vakr[d] or 0.0
            sums[d][1] += e.mrtg[d] or 0.0
        if all(within(e.mrtg[d], e.vakr[d], rules.tolerance) for d in (0, 1)):
            result.agreed += 1
            continue
        e.jitter_agrees = lambda t=t: _jitter(target, series, t, phase, rules)
        category = explain(e)
        result.explained[category] += 1
        result.rows.append({**row, "category": category})
    (vin, min_), (vout, mout) = sums
    result.volume = ((vin - min_) / min_ if min_ else None, (vout - mout) / mout if mout else None)
    result.verdict = verdict(result.compared, result.explained["unexplained"], result.volume,
                             rules)  # fmt: skip
    return result


def _limit_peak(e: Evidence) -> bool:
    """Report whether the application saw traffic above MRTG's discard limit.

    Args:
        e: The evidence.

    Returns:
        bool: True when a peak exceeds a limit.

    Raises:
        None
    """
    return any(e.limits[d] is not None and (e.peak[d] or 0) > (e.limits[d] or 0) for d in (0, 1))


def run_traffic(inbox: Inbox, rules: TrafficRules) -> TrafficReport:
    """Compare every MRTG target in the inbox.

    Args:
        inbox: The merged bundles.
        rules: The rules.

    Returns:
        TrafficReport: Results, unmatched targets, and skipped targets.

    Raises:
        None
    """
    report = TrafficReport()
    for name, target in sorted(inbox.mrtg.items()):
        if not target.comparable:
            report.skipped.append((name, target.note))
            continue
        match = match_interface(inbox.export, target.aliases, target.kind, target.key)
        if match.interface is None:
            report.unmatched.append((name, match.problem))
            continue
        report.results.append(compare_traffic(target, match.interface, rules))
    return report
