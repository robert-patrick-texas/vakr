#!/usr/bin/env python3
# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Convert net-snmp walk output into an snmpsim ``.snmprec`` data file.

Field captures arrive as ``snmpbulkwalk -On -Ot -Oe`` text, one file per
subtree (``system.txt``, ``ifTable.txt``, ...). snmpsim serves ``.snmprec``
files: one ``OID|TAG|VALUE`` line per object, sorted by OID, where TAG is
the BER tag number (``2`` INTEGER, ``4`` OCTET STRING, ``4x`` hex octets,
``6`` OID, ``64`` IpAddress, ``65`` Counter32, ``66`` Gauge32, ``67``
TimeTicks, ``70`` Counter64). This tool merges one device's walk files,
converts every record, and writes them sorted and de-duplicated.

How walk values map (``-Ot`` prints TimeTicks as a bare number, ``-Oe``
prints enumerations as numbers):

- ``INTEGER: 1`` or ``INTEGER: up(1)`` -> ``2``
- ``STRING: text`` -> ``4``, or ``4x`` when the text cannot sit on one
  ``.snmprec`` line (newlines, ``|``, non-ASCII); ``""`` -> empty ``4``
- ``STRING: 2:5e:1:0:0:1`` under a ``--hex-hint`` OID -> ``4x`` octets,
  because a MIB DISPLAY-HINT (``ifPhysAddress``) printed the bytes as text
- ``Hex-STRING:``, ``BITS:`` -> ``4x``; ``OID:`` -> ``6``; ``IpAddress:`` -> ``64``
- ``Counter32:``, ``Gauge32:``, ``Counter64:`` -> ``65``, ``66``, ``70``
- ``Timeticks: (123) 0:00:01.23`` or a bare number -> ``67``
- ``No Such Object/Instance`` and end-of-MIB lines are dropped

Records that cannot be converted are reported with file and line; with
``--strict`` they fail the run.

Dependencies:
    argparse, pathlib, re: Standard library only (blueprint 29).
    walk2rec_values: The sibling module that converts each value.

Safety:
    Reads walk text and writes one file. Walks contain no communities; run
    it on scrubbed field bundles (FIELD-005) when the data must be shared.

Example:
    $ python tools/snmpsim/walk2rec.py inbox/rtr1/snmp/*.txt -o rtr1.snmprec
    walk2rec: 412 records from 5 files -> rtr1.snmprec (0 skipped)
"""

import argparse
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from walk2rec_values import ConversionError, convert

DEFAULT_HEX_HINTS = (".1.3.6.1.2.1.2.2.1.6",)  # ifPhysAddress (PhysAddress, hint "1x:")
_RECORD = re.compile(r"^(\.?[0-9]+(?:\.[0-9]+)+) = (?:([A-Za-z][A-Za-z0-9 -]*?): ?)?(.*)$")


def oid_key(oid: str) -> tuple[int, ...]:
    """Return an OID's numeric sort key.

    Args:
        oid: Dotted OID, with or without a leading dot.

    Returns:
        tuple[int, ...]: The sub-identifiers.

    Raises:
        ValueError: If the OID is not numeric.
    """
    return tuple(int(arc) for arc in oid.lstrip(".").split("."))


def records(text: str) -> Iterator[tuple[int, str, str, str]]:
    """Split walk output into ``(line, oid, type, value)`` records.

    Values that continue on following lines (a multi-line ``sysDescr``, a
    long ``Hex-STRING``) are joined with newlines. Lines before the first
    record, such as ``Timeout: No Response``, are ignored.

    Args:
        text: ``snmpbulkwalk -On`` output.

    Returns:
        Iterator[tuple[int, str, str, str]]: One tuple per record; the type
        is empty for untyped values (TimeTicks under ``-Ot``).

    Raises:
        None
    """
    start, oid, kind, value = 0, "", "", None
    for number, line in enumerate(text.splitlines(), start=1):
        match = _RECORD.match(line)
        if match:
            if value is not None:
                yield start, oid, kind, value
            start, oid, kind, value = number, match[1].lstrip("."), match[2] or "", match[3]
        elif value is not None:
            value += "\n" + line
    if value is not None:
        yield start, oid, kind, value


def convert_files(
    paths: Iterable[Path], hex_hints: Sequence[str]
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Convert and merge every record of several walk files.

    Args:
        paths: Walk files of one device.
        hex_hints: OID prefixes whose strings are DISPLAY-HINT octets.

    Returns:
        tuple[dict[str, tuple[str, str]], list[str]]: ``(tag, value)`` by
        OID, and one problem message per record that was skipped.

    Raises:
        OSError: If a file cannot be read.
    """
    found: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line, oid, kind, value in records(text):
            try:
                converted = convert(oid, kind, value, hex_hints)
            except ConversionError as exc:
                problems.append(f"{path}:{line}: {oid}: {exc}")
                continue
            if converted is not None:
                found[oid] = converted
    return found, problems


def render(found: dict[str, tuple[str, str]]) -> str:
    """Render converted records as a sorted ``.snmprec`` text.

    Args:
        found: ``(tag, value)`` by OID.

    Returns:
        str: One ``OID|TAG|VALUE`` line per record, in numeric OID order.

    Raises:
        None
    """
    ordered = sorted(found, key=oid_key)
    return "".join(f"{oid}|{found[oid][0]}|{found[oid][1]}\n" for oid in ordered)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the converter.

    Args:
        argv: Command-line arguments; ``None`` means ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when ``--strict`` and records were skipped.

    Raises:
        SystemExit: On usage errors.
    """
    parser = argparse.ArgumentParser(
        description="Convert snmpbulkwalk -On -Ot -Oe output to .snmprec"
    )
    parser.add_argument("walks", nargs="+", type=Path, help="walk files of one device")
    parser.add_argument("-o", "--output", type=Path, help="output .snmprec (default: stdout)")
    parser.add_argument(
        "--hex-hint", action="append", default=None, metavar="OID",
        help="OID prefix whose STRING values are colon-hex octets (default: ifPhysAddress)",
    )  # fmt: skip
    parser.add_argument("--strict", action="store_true", help="fail if any record is skipped")
    args = parser.parse_args(argv)
    hints = tuple("." + h.lstrip(".") for h in (args.hex_hint or DEFAULT_HEX_HINTS))
    found, problems = convert_files(args.walks, hints)
    for problem in problems:
        print(f"walk2rec: skipped {problem}", file=sys.stderr)
    text = render(found)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
        destination = args.output
        print(f"walk2rec: {len(found)} records from {len(args.walks)} files -> {destination} "
              f"({len(problems)} skipped)", file=sys.stderr)  # fmt: skip
    return 1 if args.strict and problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
