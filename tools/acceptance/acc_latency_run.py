# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Run MVP-ACC-04 over every SmokePing series: match, pool, compare, judge.

A series is one SmokePing target as measured from one vantage point (the
master, or a slave). Its device is matched by alias. Rows where the
application has no cycle, or SmokePing has no value, are excluded; rows
where one side lost every probe count towards loss but not RTT. Verdicts:

* ``pass``: at least ``min_windows`` rows compared, the medians of the row
  medians agree, the loss rates are consistent, and no ``loss_outage`` row;
* ``review``: medians and loss agree but some rows are ``loss_outage``;
* ``fail``: the medians or the loss rates disagree;
* ``insufficient``: too few rows compared.

Dependencies:
    acc_latency, acc_match, acc_bundles, acc_config.

Safety:
    Pure computation.

Example:
    >>> judge(300, (12.0, 12.4), True, 0, LatencyRules())
    'pass'
"""

import statistics
from collections.abc import Mapping

from acc_bundles import Inbox, SmokeSeries
from acc_config import LatencyRules
from acc_latency import (
    LatencyReport,
    LatencyResult,
    category_of,
    loss_consistent,
    pool,
    tolerance_ms,
)
from acc_match import match_device
from acc_sources import Round
from acc_vakr import Device


def judge(compared: int, medians: tuple[float | None, float | None], loss_ok: bool,
          outages: int, rules: LatencyRules) -> str:  # fmt: skip
    """Judge one series.

    Args:
        compared: Rows compared.
        medians: SmokePing's and the application's median of row medians.
        loss_ok: Whether the loss rates are consistent.
        outages: ``loss_outage`` rows.
        rules: The rules.

    Returns:
        str: ``pass``, ``review``, ``fail``, or ``insufficient``.

    Raises:
        None
    """
    if compared < rules.min_windows:
        return "insufficient"
    s, v = medians
    if s is None or v is None or abs(s - v) > tolerance_ms(s, rules) or not loss_ok:
        return "fail"
    return "review" if outages else "pass"


def _row(result: LatencyResult, r: Round, category: str, values: Mapping[str, object]) -> None:
    """List one excluded or disagreeing row.

    Args:
        result: The result being built.
        r: The SmokePing round.
        category: Its category.
        values: Medians and loss shares.

    Returns:
        None

    Raises:
        None
    """
    result.listed.append({"end": r.end, "category": category, **values})


def compare_latency(series: SmokeSeries, device: Device, rules: LatencyRules) -> LatencyResult:
    """Compare one SmokePing series with one device's ICMP cycles.

    Args:
        series: The SmokePing series.
        device: The matched device.
        rules: The rules.

    Returns:
        LatencyResult: Counts, medians, loss, listed rows, verdict.

    Raises:
        None
    """
    result = LatencyResult(series.path, series.vantage, device)
    starts = sorted(device.cycles)
    if not starts:
        return result
    meds: tuple[list[float], list[float]] = ([], [])
    loss = [0.0, 0, 0.0, 0]
    for r in sorted(series.rounds.values(), key=lambda x: x.end):
        if not starts[0] <= r.end - r.step or r.end > starts[-1] + r.step:
            continue
        result.rows += 1
        rtts, sent = pool(device, starts, r.end - r.step, r.end)
        if not sent:
            result.excluded["vakr_gap"] += 1
            _row(result, r, "vakr_gap", {"smokeping_ms": r.median_ms})
            continue
        if r.median_ms is None and r.loss is None:
            result.excluded["smokeping_unknown"] += 1
            continue
        s_share = (r.loss or 0.0) / r.pings if r.pings else 0.0
        v_share = (sent - len(rtts)) / sent
        loss = [loss[0] + (r.loss or 0.0), loss[1] + r.pings, loss[2] + sent - len(rtts),
                loss[3] + sent]  # fmt: skip
        values = {"smokeping_ms": r.median_ms, "vakr_ms": statistics.median(rtts) if rtts else None,
                  "smokeping_loss": round(s_share, 3), "vakr_loss": round(v_share, 3)}  # fmt: skip
        dark = (s_share >= 1.0 and v_share < rules.high_loss) or (
            v_share >= 1.0 and s_share < rules.high_loss
        )
        if dark:
            result.outages += 1
            _row(result, r, "loss_outage", values)
        if r.median_ms is None or not rtts:
            continue
        result.compared += 1
        meds[0].append(r.median_ms)
        meds[1].append(statistics.median(rtts))
        if abs(meds[1][-1] - r.median_ms) <= tolerance_ms(r.median_ms, rules):
            result.agreed += 1
            continue
        wide, _ = pool(device, starts, r.end - 2 * r.step, r.end + r.step)
        category = category_of(r, rtts, sent, wide, rules)
        result.explained[category] += 1
        _row(result, r, category, values)
    result.medians = (statistics.median(meds[0]) if meds[0] else None,
                      statistics.median(meds[1]) if meds[1] else None)  # fmt: skip
    result.loss = ((loss[0], int(loss[1])), (loss[2], int(loss[3])))
    ok = loss_consistent(loss[0], int(loss[1]), loss[2], int(loss[3]), rules)
    result.verdict = judge(result.compared, result.medians, ok, result.outages, rules)
    return result


def run_latency(inbox: Inbox, rules: LatencyRules) -> LatencyReport:
    """Compare every SmokePing series in the inbox.

    Args:
        inbox: The merged bundles.
        rules: The rules.

    Returns:
        LatencyReport: Results, unmatched series, and skipped series.

    Raises:
        None
    """
    report = LatencyReport()
    for (path, vantage), series in sorted(inbox.smokeping.items()):
        if not series.comparable:
            report.skipped.append((path, vantage, series.note))
            continue
        match = match_device(inbox.export, series.aliases)
        if match.device is None:
            report.unmatched.append((path, vantage, match.problem))
            continue
        report.results.append(compare_latency(series, match.device, rules))
    return report
