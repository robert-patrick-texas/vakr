# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Write the comparison as Markdown (acceptance evidence to commit) and as JSON.

The Markdown report leads with one row per criterion and its verdict,
then the data it rests on (bundles, period, vantage points), then one
table per criterion with every compared subject, the explanation
categories with their counts, the out-of-tolerance windows (the first
``max_listed`` per subject; the JSON has every one), and what could not be
compared and why. Times are UTC.

Dependencies:
    acc_traffic_run, acc_latency, acc_explain: Results and categories.
    acc_format: Numbers, times, and tables.

Safety:
    Names from bundles are escaped for Markdown tables; pseudonymized
    bundles yield pseudonymized names only.

Example:
    >>> from acc_bundles import Inbox
    >>> from acc_config import Rules
    >>> render_markdown(Inbox(), TrafficReport(), LatencyReport(), Rules(),
    ...                 datetime(2026, 10, 27)).splitlines()[0]
    '# Parallel run: comparison with MRTG and SmokePing'
"""

from collections import Counter
from datetime import datetime

from acc_bundles import Inbox
from acc_config import Rules
from acc_explain import CATEGORIES
from acc_format import categories, cell, overall, percent, rate, summary, table, when
from acc_latency import LATENCY_CATEGORIES, LatencyReport
from acc_traffic_run import TrafficReport


def traffic_section(report: TrafficReport, rules: Rules) -> list[str]:
    """Render MVP-ACC-03.

    Args:
        report: Traffic results.
        rules: Tolerance and listing bound.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    tol = rules.traffic.tolerance
    out = [
        "## MVP-ACC-03: traffic rates against MRTG",
        "",
        f"Each MRTG {rules.traffic.step_s}-second window is compared with vakr's samples "
        "averaged the way MRTG built that window, at the poll phase estimated for the target. "
        f"Tolerance ±{tol * 100:g} %; a window vakr measured less than "
        f"{rules.traffic.min_coverage * 100:g} % of is a gap, not a comparison.",
        "",
    ]
    rows = []
    for r in report.results:
        iface = r.interface
        rows.append((r.target, iface.if_name or iface.if_descr, f"{r.phase} s", r.windows,
                     r.compared, r.agreed, sum(r.explained.values()) - r.explained["unexplained"],
                     r.explained["unexplained"], sum(r.excluded.values()),
                     percent(r.volume[0]), percent(r.volume[1]), f"**{r.verdict}**"))  # fmt: skip
    out += table(("MRTG target", "vakr interface", "Phase", "Windows", "Compared",
                   f"Within ±{tol * 100:g} %", "Explained", "Unexplained", "Excluded",
                   "Volume in", "Volume out", "Verdict"), rows)  # fmt: skip
    totals = sum((r.explained + r.excluded for r in report.results), Counter())
    out += ["### Explanation categories", "", *categories(totals, CATEGORIES)]
    out += ["### Windows outside the tolerance", ""]
    if not any(not w.get("excluded") for r in report.results for w in r.rows):
        out += ["None.", ""]
    for r in report.results:
        listed = [w for w in r.rows if not w.get("excluded")]
        if not listed:
            continue
        out += [f"#### {cell(r.target)}", ""]
        out += table(("Window end (UTC)", "MRTG in", "vakr in", "MRTG out", "vakr out",
                       "Category"),
                      [(when(w["end"]), rate(w["mrtg"][0]), rate(w["vakr"][0]),
                        rate(w["mrtg"][1]), rate(w["vakr"][1]), f"`{w['category']}`")
                       for w in listed[: rules.max_listed]])  # fmt: skip
        if len(listed) > rules.max_listed:
            out += [f"{len(listed) - rules.max_listed} more in the JSON report.", ""]
    skipped = [*report.unmatched, *report.skipped]
    out += ["### Not compared", ""]
    out += table(("MRTG target", "Why"), skipped) if skipped else ["None.", ""]
    return out


def latency_section(report: LatencyReport, rules: Rules) -> list[str]:
    """Render MVP-ACC-04.

    Args:
        report: Latency results.
        rules: Tolerances and listing bound.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    lat = rules.latency
    out = [
        "## MVP-ACC-04: latency and loss against SmokePing",
        "",
        f"Per SmokePing row, vakr's probes from the cycles that started in the row are pooled. "
        f"The medians of the row medians must agree within ±{lat.tolerance_ms:g} ms or "
        f"±{lat.tolerance_rel * 100:g} %, whichever is larger; loss rates must differ by "
        "at most "
        f"{lat.loss_abs * 100:g} percentage points or {lat.loss_z:g} standard errors.",
        "",
    ]
    rows = []
    for r in report.results:
        (s_lost, s_sent), (v_lost, v_sent) = r.loss
        s_med, v_med = r.medians
        delta = "—" if s_med is None or v_med is None else f"{v_med - s_med:+.2f} ms"
        rows.append((r.path, r.vantage, r.device.name, r.rows, r.compared, r.agreed,
                     "—" if s_med is None else f"{s_med:.2f} ms",
                     "—" if v_med is None else f"{v_med:.2f} ms", delta,
                     f"{100 * s_lost / s_sent:.2f} %" if s_sent else "—",
                     f"{100 * v_lost / v_sent:.2f} %" if v_sent else "—", r.outages,
                     f"**{r.verdict}**"))  # fmt: skip
    out += table(("SmokePing target", "Vantage", "vakr device", "Rows", "Compared",
                   "Within tolerance", "SmokePing median", "vakr median", "Difference",
                   "SmokePing loss", "vakr loss", "Outage rows", "Verdict"), rows)  # fmt: skip
    totals = sum((r.explained + r.excluded for r in report.results), Counter())
    totals["loss_outage"] = sum(r.outages for r in report.results)
    out += ["### Row categories", "", *categories(totals, LATENCY_CATEGORIES)]
    skipped = [(p, v, why) for p, v, why in [*report.unmatched, *report.skipped]]
    out += ["### Not compared", ""]
    out += table(("SmokePing target", "Vantage", "Why"), skipped) if skipped else ["None.", ""]
    return out


def vantage_section(inbox: Inbox, latency: LatencyReport) -> list[str]:
    """Render who measured from where.

    Args:
        inbox: The bundles.
        latency: Latency results (for SmokePing vantage points).

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    rows: list[tuple[str, str, str]] = []
    rows += [("MRTG", h, "") for h in sorted({t.vantage for t in inbox.mrtg.values()})]
    rows += [("SmokePing", v, "") for v in sorted({s.vantage for s in inbox.smokeping.values()})]
    rows += [("vakr", h, f"{n} ({p})") for h, n, p in sorted(inbox.export.hosts)]
    out = ["## Vantage points", "", *table(("Tool", "Host", "Collector (position)"), rows)]
    hosts = {h for h, _, _ in inbox.export.hosts}
    other = sorted({r.vantage for r in latency.results if r.vantage not in hosts})
    if other:
        out += [f"SmokePing measured from {', '.join(other)}, not from vakr's host; the RTT "
                "difference includes the path between the two hosts (MVP-ACC-04 asks for the "
                "same vantage point).", ""]  # fmt: skip
    return out


def periods(inbox: Inbox) -> list[tuple[str, str, str, str]]:
    """Say which span each tool's data covers (MVP-ACC-10 asks for 14 days).

    Args:
        inbox: The bundles.

    Returns:
        list[tuple[str, str, str, str]]: Tool, first, last, and days covered.

    Raises:
        None
    """
    spans = {
        "MRTG windows": [t for m in inbox.mrtg.values() for t in m.windows],
        "SmokePing rows": [t for s in inbox.smokeping.values() for t in s.rounds],
        "vakr samples": [t for i in inbox.export.interfaces.values() for t in i.samples],
        "vakr ICMP cycles": [t for d in inbox.export.devices.values() for t in d.cycles],
    }
    return [(name, when(min(ts)), when(max(ts)), f"{(max(ts) - min(ts)) / 86400:.1f}")
            for name, ts in spans.items() if ts]  # fmt: skip


def render_markdown(inbox: Inbox, traffic: TrafficReport, latency: LatencyReport,
                    rules: Rules, generated: datetime) -> str:  # fmt: skip
    """Render the whole Markdown report.

    Args:
        inbox: The bundles.
        traffic: MVP-ACC-03 results.
        latency: MVP-ACC-04 results.
        rules: The rules.
        generated: Report time (UTC).

    Returns:
        str: The report.

    Raises:
        None
    """
    acc03 = overall(r.verdict for r in traffic.results)
    acc04 = overall(r.verdict for r in latency.results)
    lines = [f"# {rules.title}", "",
             f"Generated {generated:%Y-%m-%d %H:%M} UTC from {len(inbox.bundles)} field bundles "
             "by `tools/acceptance/acceptance_report.py`; the rules are in `acceptance.toml` "
             "(blueprint Appendix E.34).", ""]  # fmt: skip
    lines += table(("Criterion", "Result", "Subjects"), [
        ("MVP-ACC-03: 5-minute traffic rates agree with MRTG within the tolerance",
         f"**{acc03}**", summary(r.verdict for r in traffic.results)),
        ("MVP-ACC-04: median RTT agrees with SmokePing; loss is consistent", f"**{acc04}**",
         summary(r.verdict for r in latency.results)),
    ])  # fmt: skip
    lines += ["## Bundles", ""]
    lines += table(("Bundle", "Created (UTC)", "Data"),
                    [(b, c, ", ".join(p)) for b, c, p in inbox.bundles])  # fmt: skip
    lines += ["## Period", "", *table(("Data", "From (UTC)", "To (UTC)", "Days"), periods(inbox))]
    lines += vantage_section(inbox, latency)
    lines += traffic_section(traffic, rules) + latency_section(latency, rules)
    if inbox.problems or inbox.export.truncated:
        notes = [*inbox.problems] + (
            ["a vakr export hit its row budget"] if inbox.export.truncated else []
        )
        lines += ["## Problems", "", *(f"- {cell(p)}" for p in notes), ""]
    return "\n".join(lines).rstrip() + "\n"
