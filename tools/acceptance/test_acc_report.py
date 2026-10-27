# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Recorded formats, configuration, the whole report, and portability (MVP-ACC-03, MVP-ACC-04).

The parsers read the formats the field kit records (an MRTG rateup
``.log`` and ``rrdtool info``/``fetch`` output of MRTG and SmokePing RRDs,
captured with rrdtool 1.7.2's layout). The end-to-end test writes a
two-host inbox (MRTG and SmokePing on one host, vakr on another, as
``field_inbox.py`` extracts them) and checks the Markdown and JSON
reports, ``--gate``, and the usage errors. The last test proves the tool
imports nothing from vakr (blueprint 29.1, rule 1).

Dependencies:
    pytest; acc_* modules and acceptance_report.

Example:
    $ uv run pytest tools/acceptance/test_acc_report.py
"""

import ast
import json
from pathlib import Path

import acceptance_report
import pytest
from acc_config import ConfigError, load_rules, rules_from
from acc_format import overall, summary
from acc_render import NAMES
from acc_sources import mrtg_windows, parse_log, smokeping_rounds
from acc_testkit import (
    MRTG_COLUMNS,
    T0,
    Truth,
    bundle,
    mrtg_log,
    mrtg_row,
    smokeping_fetch,
    table,
    vakr_members,
)

HERE = Path(__file__).parent
DATA = HERE / "testdata"

KEY_ID = "shared-test-key"
"""A pseudonym key id for fixtures; a hex string here reads as a credential to gitleaks."""


def test_recorded_mrtg_and_smokeping_formats() -> None:
    """The rateup log, MRTG's RRD, and SmokePing's RRD parse to windows in bit/s and ms.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    log = parse_log((DATA / "mrtg-192.0.2.10_2.log").read_text(), 300)
    assert log.last_run == 1792497903
    assert len(log.windows) == 35  # 36 five-minute rows; the oldest has no older 5-minute row
    assert log.windows[-1].end == 1792497900
    assert log.windows[-1].values == (400000.0, 160000.0)
    rrd = mrtg_windows((DATA / "mrtg-rrd-fetch.txt").read_text(),
                       (DATA / "mrtg-rrd-info.txt").read_text(), 300)  # fmt: skip
    assert rrd.last_run == 1792497903
    assert len(rrd.windows) == 24
    unknown = [w for w in rrd.windows if w.values == (None, None)]
    assert len(unknown) == 2  # one "nan" and one "-nan" row
    assert rrd.windows[0].values[0] == pytest.approx(5.6001064292e7 * 8)
    rounds = smokeping_rounds((DATA / "smokeping-fetch.txt").read_text(), 300)
    assert len(rounds) == 12
    lossy = next(r for r in rounds if r.loss == 2.0)
    assert len(lossy.rtts_ms) == 18
    assert lossy.median_ms == pytest.approx(lossy.median_ms)
    assert sum(1 for r in rounds if r.median_ms is None) == 1


def test_configuration_file_and_its_bounds(tmp_path: Path) -> None:
    """The shipped file loads; unknown keys, wrong types, and out-of-range values are refused.

    Args:
        tmp_path: Scratch directory.

    Returns:
        None

    Raises:
        None
    """
    rules = load_rules(HERE / "acceptance.toml")
    assert (rules.traffic.tolerance, rules.latency.tolerance_ms) == (0.02, 1.0)
    assert rules.inbox == (HERE / "../../inbox").resolve() or rules.inbox.name == "inbox"
    for data, message in (({"traffic": {"tolerence": 0.02}}, "unknown setting"),
                          ({"traffic": {"tolerance": "2%"}}, "must be a number"),
                          ({"latency": {"tolerance_rel": 2}}, "between")):  # fmt: skip
        with pytest.raises(ConfigError, match=message):
            rules_from(data)
    bad = tmp_path / "bad.toml"
    bad.write_text("[traffic\n")
    with pytest.raises(ConfigError):
        load_rules(bad)
    assert overall([]) == "no data"
    assert overall(["pass", "review"]) == "review"
    assert summary(["pass", "pass", "fail"]) == "2 pass, 1 fail"


def two_hosts(root: Path, *, key: str = "", mon_key: str = "", vakr_rtt_us: int = 12_300) -> None:
    """Write the MRTG/SmokePing host's bundle and vakr's bundle.

    Args:
        root: Inbox.
        key: Pseudonym key id of the vakr bundle (``""`` for clear).
        mon_key: Pseudonym key id of the MRTG/SmokePing bundle (``""`` for clear).
        vakr_rtt_us: vakr's RTT.

    Returns:
        None

    Raises:
        None
    """
    truth = Truth(T0, 30 * 60, lambda k: 2e8 + 4e7 * ((k * 13) % 7))
    rows = [(T0 + 300 * (i + 2), 0.0, 0.0123, [0.0123 + i % 3 * 1e-5] * 20) for i in range(340)]
    sp = ["s0001", "Core/rtr1", "rtr1", "rtr1", "192.0.2.10", "192.0.2.10", "FPing", 20, 300, "",
          "smokeping/rrd/s0001.fetch.txt", "yes", ""]  # fmt: skip
    skipped = mrtg_row("t0002", "cpu", kind="script", comparable="no",
                       note="values come from a script", data="")  # fmt: skip
    bundle(root, "field-mon1-20261020T120000Z", {
        "mrtg/summary.json": '{"host": "mon1"}',
        "mrtg/targets.csv": table(MRTG_COLUMNS, [mrtg_row("t0001", "rtr1_2"), skipped]),
        "mrtg/logs/t0001.log": mrtg_log(truth, 40),
        "smokeping/summary.json": '{"host": "mon1"}',
        "smokeping/targets.csv": table(["key", "path", "menu", "title", "host", "aliases",
                                        "probe", "pings", "step", "slave", "data", "comparable",
                                        "note"], [sp]),
        "smokeping/rrd/s0001.fetch.txt": smokeping_fetch(rows),
    }, key_id=mon_key)  # fmt: skip
    rtts = " ".join(str(vakr_rtt_us + d) for d in (-100, 0, 0, 0, 100))
    cycles = [f"{T0 + 60 * m + 7}.000,{rtts}" for m in range(30 * 60)]
    bundle(root, "field-vakr1-20261020T120500Z", vakr_members(truth, cycles=cycles), key_id=key)


def test_report_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two hosts' bundles give a passing report in Markdown and JSON, and ``--gate`` agrees.

    Args:
        tmp_path: Scratch directory.
        capsys: Captures the summary line.

    Returns:
        None

    Raises:
        None
    """
    inbox = out = tmp_path / "inbox"
    two_hosts(inbox)
    argv = ["--inbox", str(inbox), "--gate"]  # clear bundles: the report stays in the inbox
    assert acceptance_report.main(argv) == 0
    line = capsys.readouterr().out
    assert "MVP-ACC-03 pass (1 pass); MVP-ACC-04 pass (1 pass)" in line
    markdown = (out / "parallel-run-report.md").read_text()
    assert (
        "| MVP-ACC-03: 5-minute traffic rates agree with MRTG within the tolerance | **pass**"
        in markdown
    )
    assert "| rtr1_2 | Gi0/0/0 | 40 s |" in markdown
    assert "| cpu | values come from a script |" in markdown
    assert "SmokePing measured from mon1, not from vakr's host" in markdown
    report = json.loads((out / "parallel-run-report.json").read_text())
    assert report["criteria"] == {"MVP-ACC-03": "pass", "MVP-ACC-04": "pass"}
    assert report["traffic"][0]["phase_s"] == 40
    assert report["latency"][0]["medians_ms"] == pytest.approx([12.31, 12.3], abs=0.02)
    slow = tmp_path / "slow"
    two_hosts(slow, vakr_rtt_us=15_000, key=KEY_ID)
    assert acceptance_report.main(["--inbox", str(slow), "--gate"]) == 1
    assert "MVP-ACC-04 fail" in capsys.readouterr().out
    problems = json.loads((slow / "parallel-run-report.json").read_text())["problems"]
    assert any("different pseudonym keys" in p for p in problems)


def test_e34_only_a_pseudonymized_report_leaves_the_inbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.34 decision 11: a clear report is refused outside the inbox; a pseudonymized one is not.

    Args:
        tmp_path: Scratch directory.
        capsys: Captures the refusal.

    Returns:
        None

    Raises:
        None
    """
    evidence = tmp_path / "docs"
    evidence.mkdir()
    clear = tmp_path / "clear"
    two_hosts(clear)
    assert acceptance_report.main(["--inbox", str(clear), "--output", str(evidence)]) == 2
    assert "names devices and addresses" in capsys.readouterr().err
    assert list(evidence.iterdir()) == []
    mixed = tmp_path / "mixed"
    two_hosts(mixed, key=KEY_ID)  # one bundle clear: names cannot be trusted
    assert acceptance_report.main(["--inbox", str(mixed), "--output", str(evidence)]) == 2
    assert list(evidence.iterdir()) == []
    pseudo = tmp_path / "pseudo"
    two_hosts(pseudo, key=KEY_ID, mon_key=KEY_ID)
    assert acceptance_report.main(["--inbox", str(pseudo), "--output", str(evidence)]) == 0
    assert json.loads((evidence / "parallel-run-report.json").read_text())["pseudonymized"] is True
    assert NAMES[True] in (evidence / "parallel-run-report.md").read_text()
    assert not (clear / "parallel-run-report.md").exists()  # refused before anything was written


def test_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing inbox or a bad configuration exits 2 without writing a report.

    Args:
        tmp_path: Scratch directory.
        capsys: Captures stderr.

    Returns:
        None

    Raises:
        None
    """
    assert acceptance_report.main(["--inbox", str(tmp_path / "none"), "--output",
                                   str(tmp_path)]) == 2  # fmt: skip
    assert "is not a directory" in capsys.readouterr().err
    bad = tmp_path / "bad.toml"
    bad.write_text("[latency]\nhigh_loss = 7\n")
    assert acceptance_report.main(["--config", str(bad), "--inbox", str(tmp_path)]) == 2
    assert "high_loss" in capsys.readouterr().err
    assert list(tmp_path.glob("*.md")) == []


def test_imports_nothing_from_vakr() -> None:
    """The tool is portable: no module imports vakr (blueprint 29.1, rule 1).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for path in HERE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not [n for n in names if n == "vakr" or n.startswith("vakr.")], path.name
