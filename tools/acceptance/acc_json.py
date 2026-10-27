# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""The JSON report: every subject, every listed window, and what was not compared.

Dependencies:
    acc_bundles, acc_config, acc_latency, acc_render, acc_traffic_run: Inputs.

Safety:
    Pure data shaping; pseudonymized bundles give pseudonymized names only.

Example:
    >>> from acc_bundles import Inbox
    >>> from acc_config import Rules
    >>> as_json(Inbox(), TrafficReport(), LatencyReport(), Rules())["criteria"]
    {'MVP-ACC-03': 'no data', 'MVP-ACC-04': 'no data'}
"""

from typing import Any

from acc_bundles import Inbox
from acc_config import Rules
from acc_format import overall
from acc_latency import LatencyReport, tolerance_ms
from acc_traffic_run import TrafficReport


def as_json(inbox: Inbox, traffic: TrafficReport, latency: LatencyReport,
            rules: Rules) -> dict[str, Any]:  # fmt: skip
    """Build the JSON report: every subject and every listed window.

    Args:
        inbox: The bundles.
        traffic: MVP-ACC-03 results.
        latency: MVP-ACC-04 results.
        rules: The rules.

    Returns:
        dict[str, Any]: JSON-ready data.

    Raises:
        None
    """
    return {
        "criteria": {"MVP-ACC-03": overall(r.verdict for r in traffic.results),
                     "MVP-ACC-04": overall(r.verdict for r in latency.results)},
        "bundles": [{"id": b, "created": c, "data": p} for b, c, p in inbox.bundles],
        "problems": inbox.problems,
        "traffic": [{"target": r.target, "interface_id": r.interface.interface_id,
                     "if_name": r.interface.if_name, "phase_s": r.phase, "windows": r.windows,
                     "compared": r.compared, "agreed": r.agreed, "excluded": dict(r.excluded),
                     "explained": dict(r.explained), "volume": r.volume, "verdict": r.verdict,
                     "rows": r.rows} for r in traffic.results],
        "traffic_not_compared": [list(x) for x in (*traffic.unmatched, *traffic.skipped)],
        "latency": [{"path": r.path, "vantage": r.vantage, "device_id": r.device.device_id,
                     "rows": r.rows, "compared": r.compared, "agreed": r.agreed,
                     "medians_ms": r.medians, "loss": r.loss, "outages": r.outages,
                     "tolerance_ms": tolerance_ms(r.medians[0] or 0.0, rules.latency),
                     "excluded": dict(r.excluded), "explained": dict(r.explained),
                     "verdict": r.verdict, "listed": r.listed} for r in latency.results],
        "latency_not_compared": [list(x) for x in (*latency.unmatched, *latency.skipped)],
    }  # fmt: skip
