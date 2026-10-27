# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""MVP-ACC-03 comparison math with known answers: alignment, agreement, explanations.

The application's samples are the truth, and MRTG's ``.log`` is computed
from the same truth by an independent model of how rateup builds its
5-minute windows (``acc_testkit``). With bursty traffic the phase must be
found (a fixed phase of 0 disagrees), and once it is, every window agrees.
Injected faults each get their own category: a repeated MRTG value, a
32-bit wrap, MaxBytes, ``unknaszero``, a gap, a restart, an ``ifIndex``
change, rounding, and a plainly wrong MRTG value that stays unexplained.

Dependencies:
    pytest; acc_testkit, acc_bundles, acc_traffic_run, acc_config.

Example:
    $ uv run pytest tools/acceptance/test_acc_traffic.py
"""

from pathlib import Path

import pytest
from acc_bundles import load_inbox
from acc_config import TrafficRules
from acc_testkit import T0, Truth, bundle, mrtg_log, mrtg_row, table, vakr_members
from acc_traffic_run import TrafficReport, run_traffic, verdict

RULES = TrafficRules(min_windows=20)
SUMMARY = '{"host": "mon1"}'


def bursty(k: int) -> float:
    """Return a traffic rate that changes every minute, with a burst each 7 minutes.

    Args:
        k: Interval number.

    Returns:
        float: Bits per second.

    Raises:
        None
    """
    return 4e8 + 3e7 * ((k * 37) % 11) + (6e8 if k % 7 == 3 else 0.0)


def compare(tmp_path: Path, log: str, vakr: dict[str, str], rules: TrafficRules = RULES,
            **row: object) -> TrafficReport:  # fmt: skip
    """Write an MRTG bundle and a vakr bundle, then run the comparison.

    Args:
        tmp_path: Inbox.
        log: MRTG ``.log`` text.
        vakr: The application's members.
        rules: Rules.
        **row: ``targets.csv`` overrides.

    Returns:
        TrafficReport: The result.

    Raises:
        None
    """
    members = {"mrtg/summary.json": SUMMARY, "mrtg/logs/t0001.log": log,
               "mrtg/targets.csv": table(*_targets(row))}  # fmt: skip
    bundle(tmp_path, "field-mon1-20261020T120000Z", members)
    bundle(tmp_path, "field-vakr1-20261020T120500Z", vakr)
    return run_traffic(load_inbox(tmp_path, 300, 100), rules)


def _targets(row: dict[str, object]) -> tuple[list[str], list[list[object]]]:
    """Build the ``targets.csv`` header and one row.

    Args:
        row: Overrides.

    Returns:
        tuple: Header and rows.

    Raises:
        None
    """
    from acc_testkit import MRTG_COLUMNS

    return MRTG_COLUMNS, [mrtg_row("t0001", "rtr1_gi0_0_0", **row)]


def test_phase_is_found_and_every_window_agrees(tmp_path: Path) -> None:
    """With MRTG polling 45 s after each boundary, the comparison finds 45 s and agrees.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    truth = Truth(T0, 6 * 60, bursty)
    report = compare(tmp_path, mrtg_log(truth, 45), vakr_members(truth))
    (result,) = report.results
    assert result.phase == 45
    assert result.compared == result.agreed == result.windows > 60
    assert result.verdict == "pass"
    assert all(abs(v) < 1e-3 for v in result.volume if v is not None)
    misaligned = TrafficRules(min_windows=20, phase_search_s=0, phase_step_s=1, jitter_s=0)
    shifted = Truth(T0, 6 * 60, bursty)
    log = mrtg_log(shifted, 45).splitlines()
    log[0] = log[1] = f"{int(log[0].split()[0]) - 45} 1 1"  # last run reported at the boundary
    off = compare(tmp_path / "off", "\n".join(log) + "\n", vakr_members(shifted), misaligned)
    assert off.results[0].phase == 0
    assert off.results[0].agreed < off.results[0].compared


def test_every_fault_gets_its_category(tmp_path: Path) -> None:
    """Injected faults are excluded or explained by name; a wrong value stays unexplained.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    truth = Truth(T0, 6 * 60, lambda k: 4e8 * (1 + 0.1 * ((k // 5) % 3)))
    windows = sorted(int(line.split()[0]) for line in mrtg_log(truth, 0).splitlines()[2:])
    repeat, wrong = windows[10], windows[20]

    def edit(end: int, inn: int, out: int) -> tuple[int, int]:
        """Inject MRTG faults.

        Args:
            end: Window end.
            inn: Inbound bytes/s.
            out: Outbound bytes/s.

        Returns:
            tuple[int, int]: The edited values.

        Raises:
            None
        """
        if end == repeat:
            return previous[0], previous[1]
        previous[:] = [inn, out]
        return (int(inn * 1.1), out) if end == wrong else (inn, out)

    previous = [0, 0]
    log = mrtg_log(truth, 0, edit=edit)
    gap = set(range(150, 155))
    vakr = vakr_members(truth, skip=frozenset(gap),
                        flags=lambda k: "BASELINE_UPTIME_RESET" if k == 200 else "",
                        if_index=lambda k: 3 if k == 250 else 2)  # fmt: skip
    (result,) = compare(tmp_path, log, vakr).results
    assert result.explained["mrtg_repeat"] == 1
    assert result.explained["unexplained"] == 1
    assert result.excluded["vakr_gap"] >= 1
    assert result.excluded["device_restart"] >= 1
    assert result.excluded["ifindex_changed"] >= 1
    assert result.verdict == "review"
    listed = {r["end"]: r["category"] for r in result.rows if not r.get("excluded")}
    assert listed == {repeat: "mrtg_repeat", wrong: "unexplained"}


def test_wrap_maxbytes_unknaszero_and_rounding(tmp_path: Path) -> None:
    """32-bit wraps, MaxBytes discards, unknaszero zeros, and rounding are explained.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    fast = Truth(T0, 3 * 60, lambda _k: 2e8)  # above 2^32 bytes per 300 s on 32-bit counters
    log = mrtg_log(fast, 0, edit=lambda _e, i, o: (i % (2**32 // 300), o))
    (wrap,) = compare(tmp_path / "w", log, vakr_members(fast), counter_bits=32).results
    assert wrap.explained["counter_wrap_32"] == wrap.compared
    capped = mrtg_log(fast, 0, edit=lambda _e, _i, o: (1_000, o))
    (clip,) = compare(tmp_path / "c", capped, vakr_members(fast), maxbytes="12500000").results
    assert clip.explained["maxbytes_clip"] == clip.compared
    zero = mrtg_log(fast, 0, edit=lambda e, i, o: (0, 0) if e % 3600 == 0 else (i, o))
    (unk,) = compare(tmp_path / "z", zero, vakr_members(fast), options="unknaszero").results
    assert unk.explained["mrtg_unknown_as_zero"] >= 2
    slow = Truth(T0, 3 * 60, lambda k: 20.0 + (k % 3))
    (low,) = compare(tmp_path / "r", mrtg_log(slow, 0), vakr_members(slow)).results
    assert low.agreed + low.explained["mrtg_rounding"] == low.compared
    assert low.explained["mrtg_rounding"] > 0


def test_reversed_targets_and_unmatched(tmp_path: Path) -> None:
    """A reversed target swaps in and out; an unknown host or ifIndex is not compared.

    Args:
        tmp_path: Inbox.

    Returns:
        None

    Raises:
        None
    """
    truth = Truth(T0, 3 * 60, lambda _k: 3e8)
    log = mrtg_log(truth, 0, edit=lambda _e, i, o: (o, i))
    (result,) = compare(tmp_path / "a", log, vakr_members(truth), reversed="yes").results
    assert result.agreed == result.compared
    report = compare(tmp_path / "b", log, vakr_members(truth), aliases="192.0.2.99")
    assert report.unmatched == [("rtr1_gi0_0_0", "no device of the application has this "
                                                 "host's names")]  # fmt: skip
    report = compare(tmp_path / "c", log, vakr_members(truth), ifindex="9")
    assert "no exported interface" in report.unmatched[0][1]
    report = compare(tmp_path / "d", log, vakr_members(truth), comparable="no", note="script")
    assert report.skipped == [("rtr1_gi0_0_0", "script")]


@pytest.mark.parametrize(
    ("compared", "unexplained", "volume", "expected"),
    [(300, 0, (0.0, 0.0), "pass"), (300, 2, (0.01, 0.0), "review"),
     (300, 0, (0.03, 0.0), "fail"), (300, 0, (None, 0.0), "fail"),
     (5, 0, (0.0, 0.0), "insufficient")],
)  # fmt: skip
def test_verdicts(compared: int, unexplained: int, volume: tuple[float | None, float | None],
                  expected: str) -> None:  # fmt: skip
    """Verdicts follow the compared count, the volume, and unexplained windows.

    Args:
        compared: Windows compared.
        unexplained: Unexplained windows.
        volume: Volume differences.
        expected: Verdict.

    Returns:
        None

    Raises:
        None
    """
    assert verdict(compared, unexplained, volume, TrafficRules()) == expected
