# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""MVP-ACC-04 comparison math with known answers: medians, tolerance, loss consistency.

SmokePing rows and the application's ICMP cycles are written with known
RTTs and losses: equal medians pass; a 2 ms difference at 12 ms fails
(the tolerance is the larger of 1 ms and 5 %, here 1 ms); a 1 % loss
difference over thousands of probes is inconsistent while sampling noise
is not; a row where SmokePing saw total loss and the application saw none
is an outage row that sends the series to review.

Dependencies:
    pytest; acc_testkit, acc_bundles, acc_latency, acc_latency_run, acc_config.

Example:
    $ uv run pytest tools/acceptance/test_acc_latency.py
"""

from pathlib import Path

import pytest
from acc_bundles import load_inbox
from acc_config import LatencyRules
from acc_latency import loss_consistent, tolerance_ms
from acc_latency_run import LatencyReport, judge, run_latency
from acc_testkit import T0, Truth, bundle, smokeping_fetch, table, vakr_members

RULES = LatencyRules(min_windows=20)
COLUMNS = ["key", "path", "menu", "title", "host", "aliases", "probe", "pings", "step", "slave",
           "data", "comparable", "note"]  # fmt: skip


def cycles(start: int, minutes: int, rtt_us: int, lost: int = 0,
           skip: frozenset[int] = frozenset()) -> list[str]:  # fmt: skip
    """Write one ICMP cycle per minute with five probes.

    Args:
        start: First cycle, epoch seconds.
        minutes: Cycles.
        rtt_us: RTT of every answered probe, around which probes spread ±200 µs.
        lost: Probes lost per cycle.
        skip: Minutes without a cycle.

    Returns:
        list[str]: ``start,rtts_us`` rows.

    Raises:
        None
    """
    rtts = [rtt_us - 200, rtt_us - 100, rtt_us, rtt_us + 100, rtt_us + 200]
    rtts = [-1] * lost + rtts[lost:]
    text = " ".join(map(str, rtts))
    return [f"{start + 60 * m + 7}.000,{text}" for m in range(minutes) if m not in skip]


def compare(tmp_path: Path, rows: list, vakr_rtt_us: int, lost: int = 0, slave: str = "",
            minutes: int = 6 * 60,
            skip: frozenset[int] = frozenset()) -> LatencyReport:  # fmt: skip
    """Write a SmokePing bundle and a vakr bundle, then run the comparison.

    Args:
        tmp_path: Inbox.
        rows: SmokePing ``(end, loss, median_s, rtts_s)`` rows.
        vakr_rtt_us: The application's RTT.
        lost: The application's lost probes per cycle.
        slave: Slave name for the series, or ``""``.
        minutes: The application's cycles.
        skip: Minutes without a cycle.

    Returns:
        LatencyReport: The result.

    Raises:
        None
    """
    target = ["s0001", "Core/rtr1", "rtr1", "rtr1", "192.0.2.10", "192.0.2.10", "FPing", 20,
              300, slave, "smokeping/rrd/s0001.fetch.txt", "yes", ""]  # fmt: skip
    bundle(tmp_path, "field-mon1-20261020T120000Z", {
        "smokeping/summary.json": '{"host": "vakr1"}',
        "smokeping/targets.csv": table(COLUMNS, [target]),
        "smokeping/rrd/s0001.fetch.txt": smokeping_fetch(rows),
    })  # fmt: skip
    truth = Truth(T0, 1, lambda _k: 1.0)
    bundle(tmp_path, "field-vakr1-20261020T120500Z",
           vakr_members(truth, cycles=cycles(T0, minutes, vakr_rtt_us, lost, skip)))  # fmt: skip
    return run_latency(load_inbox(tmp_path, 300, 100), RULES)


def rounds(median_ms: float, loss: float = 0.0, spread_ms: float = 0.4, count: int = 70) -> list:
    """Write SmokePing rows around a median.

    Args:
        median_ms: Median RTT.
        loss: Lost pings per row.
        spread_ms: Spread of the 20 pings.
        count: Rows.

    Returns:
        list: Rows for ``smokeping_fetch``.

    Raises:
        None
    """
    pings = [(median_ms - spread_ms / 2 + spread_ms * i / 19) / 1000 for i in range(20)]
    kept = pings[: 20 - int(loss)]
    return [(T0 + 300 * (i + 2), loss, median_ms / 1000, kept) for i in range(count)]


def test_equal_medians_pass_and_a_two_ms_difference_fails(tmp_path: Path) -> None:
    """12.3 ms on both sides passes; 14.3 ms against 12.3 ms is outside ±1 ms.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    (same,) = compare(tmp_path / "a", rounds(12.3), 12_300).results
    assert same.compared == same.agreed == 70
    assert same.medians == pytest.approx((12.3, 12.3))
    assert same.verdict == "pass"
    assert same.vantage == "vakr1"
    (far,) = compare(tmp_path / "b", rounds(12.3), 14_300).results
    assert far.verdict == "fail"
    assert far.explained["unexplained"] == 70
    (wide,) = compare(tmp_path / "c", rounds(12.3, spread_ms=8.0), 14_300).results
    assert wide.explained["high_variance"] == 70


def test_loss_consistency_and_outage_rows(tmp_path: Path) -> None:
    """Loss rates are tested as two samples; total loss on one side only is an outage row.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    (matching,) = compare(tmp_path / "a", rounds(12.3, loss=4), 12_300, lost=1).results
    assert matching.loss == ((280.0, 1400), (350, 1750))
    assert matching.verdict == "pass"
    (diverging,) = compare(tmp_path / "b", rounds(12.3, loss=4), 12_300).results
    assert diverging.verdict == "fail"
    dark = rounds(12.3, count=600)
    dark[10] = (dark[10][0], 20.0, None, [])
    (outage,) = compare(tmp_path / "c", dark, 12_300, minutes=601 * 5).results
    assert outage.outages == 1
    assert outage.verdict == "review"
    assert outage.listed[0]["category"] == "loss_outage"
    assert loss_consistent(10, 2000, 3, 1500, LatencyRules())
    assert not loss_consistent(100, 2000, 3, 1500, LatencyRules())
    assert not loss_consistent(1, 0, 1, 10, LatencyRules())


def test_gaps_bursts_and_tolerance(tmp_path: Path) -> None:
    """Rows without application cycles are gaps; heavy loss explains a weak median.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    late = [(T0 + 300 * (i + 2), 12.0, 0.0123, [0.0123] * 8) for i in range(70)]
    (burst,) = compare(tmp_path / "a", late, 16_000).results
    assert burst.explained["loss_burst"] == 70
    hole = frozenset(range(100, 111))  # 11 minutes without a cycle: two rows are empty
    (gap,) = compare(tmp_path / "b", rounds(12.3), 12_300, skip=hole).results
    assert gap.rows == 70
    assert gap.excluded["vakr_gap"] == 2
    assert tolerance_ms(12.0, LatencyRules()) == 1.0
    assert tolerance_ms(100.0, LatencyRules()) == 5.0
    assert judge(10, (12.0, 12.0), True, 0, LatencyRules()) == "insufficient"
    assert judge(300, (None, 12.0), True, 0, LatencyRules()) == "fail"


def test_unmatched_and_skipped_series(tmp_path: Path) -> None:
    """A host vakr does not know, and a series SmokePing measures another way, are listed.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    report = compare(tmp_path, rounds(12.3), 12_300, slave="boomer")
    assert report.results[0].vantage == "boomer"
    inbox = tmp_path / "x"
    bundle(inbox, "field-mon1-20261020T120000Z", {
        "smokeping/summary.json": '{"host": "mon1"}',
        "smokeping/targets.csv": table(COLUMNS, [
            ["s0001", "Far/x", "", "", "192.0.2.99", "192.0.2.99", "FPing", 20, 300, "", "",
             "yes", ""],
            ["s0002", "Tel/y", "", "", "192.0.2.98", "192.0.2.98", "TelnetIOSPing", 5, 300, "",
             "", "no", "not an FPing probe from this host"]]),
    })  # fmt: skip
    found = run_latency(load_inbox(inbox, 300, 10), RULES)
    assert found.unmatched == [("Far/x", "mon1", "no device of the application has this "
                                                "host's names")]  # fmt: skip
    assert found.skipped == [("Tel/y", "mon1", "not an FPing probe from this host")]
