"""Tests of the journal reader that logs the collector's runs in a self-update (MVP-ACC-07).

The entries are the ones systemd 259 wrote in the harness's container for a
``Type=notify`` unit that was restarted and then killed (2026-10-27), with
the fields the reader uses.

Dependencies:
    pytest: The test runner.
    tests.system.resilience.journal: The module under test.

Example:
    $ uv run pytest tests/system/resilience/test_journal.py
"""

import json
from pathlib import Path

import pytest

from tests.system.resilience.accounting import parse_events
from tests.system.resilience.journal import (
    UNIT_FAILURE_RESULT,
    UNIT_PROCESS_EXIT,
    UNIT_STARTED,
    UNIT_STOPPING,
    main,
    run_lines,
)

UNIT_SUCCESS = "7ad2d189f7e94e70a38c781354912448"
UNIT_STOPPED = "9d1aaa27d60140bd96365438aad20286"
UNIT_STARTING = "7d4958e842da4a758f6c1cdc7b36dcc5"


def entry(at: int, kind: str = "", **fields: str) -> dict[str, str]:
    """Build one journal entry as ``journalctl -o json`` prints it.

    Args:
        at: ``__REALTIME_TIMESTAMP``, epoch microseconds.
        kind: ``MESSAGE_ID``, or empty for an entry without one.
        **fields: Further fields.

    Returns:
        dict[str, str]: The entry; journald prints every value as a string.

    Raises:
        None
    """
    return {"__REALTIME_TIMESTAMP": str(at), **({"MESSAGE_ID": kind} if kind else {}), **fields}


def restart(at: int) -> list[dict[str, str]]:
    """Return what one ``systemctl try-restart`` of a healthy unit logs.

    Args:
        at: When the stop began.

    Returns:
        list[dict[str, str]]: Stopping, deactivated, stopped, starting, started.

    Raises:
        None
    """
    return [
        entry(at, UNIT_STOPPING, JOB_TYPE="stop", MESSAGE="Stopping vakr-collector.service..."),
        entry(at + 2_500, UNIT_SUCCESS, MESSAGE="vakr-collector.service: Deactivated."),
        entry(at + 2_800, UNIT_STOPPED, JOB_TYPE="restart", JOB_RESULT="done"),
        entry(at + 6_600, UNIT_STARTING, JOB_TYPE="start"),
        entry(at + 900_000, UNIT_STARTED, JOB_TYPE="start", JOB_RESULT="done",
              MESSAGE="Started vakr-collector.service."),
    ]  # fmt: skip


def test_two_restarts_are_two_clean_stops_and_two_starts() -> None:
    """The package's restart and self-update's restart each end a run cleanly and start one.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    lines, crashes = run_lines([*restart(1_000), *restart(5_000_000)], "self-update")
    assert crashes == []
    assert lines == [
        "run_end\t1000\tclean\tself-update",
        "run_start\t901000\tself-update",
        "run_end\t5000000\tclean\tself-update",
        "run_start\t5900000\tself-update",
    ]


def test_a_crash_ends_the_run_abruptly_and_is_reported() -> None:
    """A main process that died on its own fails the scenario; its run ends ``abrupt``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    records = [
        entry(10, MESSAGE="vakr-collector.service: Sent signal SIGKILL to main process 109"),
        entry(20, UNIT_PROCESS_EXIT, EXIT_STATUS="9",
              MESSAGE="vakr-collector.service: Main process exited, code=killed, status=9/KILL"),
        entry(30, UNIT_FAILURE_RESULT, UNIT_RESULT="signal",
              MESSAGE="vakr-collector.service: Failed with result 'signal'."),
        entry(2_000_000, UNIT_STARTED, JOB_RESULT="done"),
    ]  # fmt: skip
    lines, crashes = run_lines(records, "self-update")
    assert lines == ["run_end\t20\tabrupt\tself-update", "run_start\t2000000\tself-update"]
    assert [c.split(": ", 1)[0] for c in crashes] == ["20", "30"]
    assert "status=9/KILL" in crashes[0]


def test_the_lines_extend_the_run_log_the_accounting_reads() -> None:
    """Appended after an install's run, the lines parse into alternating runs.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    lines, _ = run_lines([*restart(10_000_000), *restart(20_000_000)], "self-update")
    log = ["run_start\t1000000\tinstall", "window_start\t2000000\tafter warm-up", *lines,
           "window_end\t40000000\taccounting"]  # fmt: skip
    runs, start, end = parse_events(log)
    assert [(r.start_us, r.end_us, r.end_kind) for r in runs] == [
        (1_000_000, 10_000_000, "clean"),
        (10_900_000, 20_000_000, "clean"),
        (20_900_000, None, ""),
    ]
    assert (start, end) == (2_000_000, 40_000_000)


def test_main_prints_lines_and_fails_on_a_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line prints the run-log lines and exits 1 when the collector crashed.

    Args:
        tmp_path: Scratch directory.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    clean, crashed = tmp_path / "clean.json", tmp_path / "crashed.json"
    clean.write_text("\n".join(json.dumps(e) for e in restart(1_000)) + "\n", encoding="utf-8")
    crashed.write_text(json.dumps(entry(5, UNIT_PROCESS_EXIT, MESSAGE="exited")) + "\n",
                       encoding="utf-8")  # fmt: skip
    assert main(["--label", "self-update", str(clean)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "run_end\t1000\tclean\tself-update"
    assert main(["--label", "self-update", str(crashed)]) == 1
    out = capsys.readouterr()
    assert out.out == "run_end\t5\tabrupt\tself-update\n"
    assert out.err == "crash: 5: exited\n"
