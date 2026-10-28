"""Turn systemd's journal of the collector unit into run-log lines (MVP-ACC-07, E.36).

A self-update restarts the collector by itself, so the harness cannot log
those runs the way it logs the stops it makes. It reads PID 1's messages
about ``vakr-collector.service`` after a cursor instead (``journalctl -o
json _PID=1 UNIT=vakr-collector.service``) and writes the lines
``accounting.py`` reads:

- a stop systemd was asked for (``Stopping ...``) ends the running run,
  ``clean``;
- the unit becoming active (``Started ...``, which a ``Type=notify`` unit
  reaches at ``READY=1``, E.36 decision 3) starts the next run;
- a main process that exited on its own or by a signal, or a unit that
  failed, is a crash. It is reported, and its run ends ``abrupt``, so the
  accounting also demands an ``unclean_stop`` incident for it.

The message ids are systemd's catalog ids (``sd-messages.h``), the same on
every systemd since the catalog was introduced.

Dependencies:
    argparse, json: The command line and the journal's export lines.

Safety:
    Reads a file the harness wrote; prints times, labels, and systemd's own
    messages about the unit, which carry no credential.

Example:
    $ uv run python -m tests.system.resilience.journal --label self-update journal.json
"""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

UNIT_STOPPING = "de5b426a63be47a7b6ac3eaac82e2f6f"
UNIT_STARTED = "39f53479d3a045ac8e11786248231fbf"
UNIT_PROCESS_EXIT = "98e322203f7a4ed290d09fe03c09fe15"
UNIT_FAILURE_RESULT = "d9b373ed55a64feb8242e02dbe79a49c"


def run_lines(records: Iterable[dict[str, Any]], label: str) -> tuple[list[str], list[str]]:
    """Translate PID 1's messages about the unit into run-log lines.

    Args:
        records: Journal entries in order, as ``journalctl -o json`` prints them.
        label: What the runs are attributed to, such as ``self-update``.

    Returns:
        tuple[list[str], list[str]]: ``events.tsv`` lines, and the crashes found.

    Raises:
        KeyError: If an entry has no ``__REALTIME_TIMESTAMP``.
    """
    lines: list[str] = []
    crashes: list[str] = []
    running = True  # the harness reads from a cursor taken while the collector ran
    for record in records:
        at, kind = record["__REALTIME_TIMESTAMP"], record.get("MESSAGE_ID", "")
        if kind == UNIT_STOPPING and running:
            lines.append(f"run_end\t{at}\tclean\t{label}")
            running = False
        elif kind == UNIT_STARTED and record.get("JOB_RESULT", "done") == "done":
            lines.append(f"run_start\t{at}\t{label}")
            running = True
        elif kind in (UNIT_PROCESS_EXIT, UNIT_FAILURE_RESULT):
            crashes.append(f"{at}: {record.get('MESSAGE', kind)}")
            if running:
                lines.append(f"run_end\t{at}\tabrupt\t{label}")
                running = False
    return lines, crashes


def main(argv: Sequence[str] | None = None) -> int:
    """Print the run-log lines of one journal export.

    Args:
        argv: ``--label LABEL FILE``; ``None`` reads ``sys.argv``.

    Returns:
        int: 0, or 1 when the collector crashed or failed in that span.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If a line is not JSON.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True, help="what the runs are attributed to")
    parser.add_argument("journal", type=Path, help="journalctl -o json output")
    args = parser.parse_args(argv)
    text = args.journal.read_text(encoding="utf-8")
    rows = (json.loads(row) for row in text.splitlines() if row.strip())
    lines, crashes = run_lines(rows, args.label)
    for line in lines:
        print(line)
    for crash in crashes:
        print(f"crash: {crash}", file=sys.stderr)
    return 1 if crashes else 0


if __name__ == "__main__":
    raise SystemExit(main())
