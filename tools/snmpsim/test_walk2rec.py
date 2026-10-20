# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for walk2rec: net-snmp walk text to snmpsim ``.snmprec``.

The fixture in ``testdata/rtr-sample`` is a small capture in the field-kit
format (``snmpbulkwalk -On -Ot -Oe``, one file per subtree) using only
documentation addresses. It includes the awkward cases: a multi-line
``sysDescr``, a ``|`` inside a string, a DISPLAY-HINT MAC, empty strings,
bare TimeTicks, enumerations with labels, a multi-line Hex-STRING, a
``No Such Instance`` row, an unsupported Opaque float, and a leading
``Timeout`` line.

Dependencies:
    pytest: Fixtures and parametrisation.
    walk2rec, walk2rec_values: The tool under test.

Example:
    $ uv run pytest tools/snmpsim/test_walk2rec.py
"""

from pathlib import Path

import pytest
import walk2rec
from walk2rec_values import ConversionError, convert

SAMPLE = Path(__file__).parent / "testdata" / "rtr-sample"
HINTS = walk2rec.DEFAULT_HEX_HINTS


def sample_records() -> dict[str, tuple[str, str]]:
    """Convert the whole sample capture.

    Args:
        None

    Returns:
        dict[str, tuple[str, str]]: ``(tag, value)`` by OID.

    Raises:
        None
    """
    found, problems = walk2rec.convert_files(sorted(SAMPLE.glob("*.txt")), HINTS)
    assert len(problems) == 1
    assert "unsupported type 'Opaque'" in problems[0]
    return found


@pytest.mark.parametrize(
    ("oid", "tag", "value"),
    [
        ("1.3.6.1.2.1.1.2.0", "6", "1.3.6.1.4.1.9.1.2734"),
        ("1.3.6.1.2.1.1.3.0", "67", "123456789"),
        ("1.3.6.1.2.1.1.8.0", "67", "0"),
        ("1.3.6.1.2.1.1.6.0", "4x", b"Lab | rack 4".hex()),
        ("1.3.6.1.2.1.2.2.1.3.2", "2", "24"),
        ("1.3.6.1.2.1.2.2.1.6.1", "4x", "025e01000001"),
        ("1.3.6.1.2.1.2.2.1.6.2", "4", ""),
        ("1.3.6.1.2.1.2.2.1.5.2", "66", "4294967295"),
        ("1.3.6.1.2.1.2.2.1.10.1", "65", "3735928559"),
        ("1.3.6.1.2.1.31.1.1.1.6.1", "70", "18446744073709551615"),
        ("1.3.6.1.2.1.31.1.1.1.21.1", "4x", "0001feff"),
        ("1.3.6.1.2.1.4.20.1.3.192.0.2.1", "64", "255.255.255.254"),
        ("1.3.6.1.2.1.4.34.1.5.2.16.32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.1", "6", "0.0"),
    ],
)
def test_sample_values_convert_to_snmprec_tags(oid: str, tag: str, value: str) -> None:
    """Every walk type in the sample maps to its BER tag and value.

    Args:
        oid: The record.
        tag: Expected tag.
        value: Expected value.

    Returns:
        None

    Raises:
        None
    """
    assert sample_records()[oid] == (tag, value)


def test_multi_line_values_and_dropped_rows() -> None:
    """Continuation lines join; ``No Such`` and end-of-MIB rows are dropped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    found = sample_records()
    descr = bytes.fromhex(found["1.3.6.1.2.1.1.1.0"][1]).decode()
    assert descr.splitlines() == [
        "Cisco IOS XE Software, Version 17.12.04",
        "Cisco IOS Software [Dublin], c8000be Software (X86_64_LINUX_IOSD-UNIVERSALK9-M), "
        "Version 17.12.4, RELEASE SOFTWARE (fc1)",
        "walk2rec sample capture",
    ]
    assert "1.3.6.1.2.1.31.1.1.1.19.1" not in found
    assert "1.3.6.1.2.1.4.34.1.11.1.4.192.0.2.1" not in found


def test_render_sorts_numerically(tmp_path: Path) -> None:
    """Output is in numeric OID order, as snmpsim requires, and round-trips.

    Args:
        tmp_path: Output directory.

    Returns:
        None

    Raises:
        None
    """
    out = tmp_path / "sample.snmprec"
    assert walk2rec.main([*map(str, sorted(SAMPLE.glob("*.txt"))), "-o", str(out)]) == 0
    lines = out.read_text().splitlines()
    oids = [walk2rec.oid_key(line.split("|", 1)[0]) for line in lines]
    assert oids == sorted(oids)
    assert len(lines) == len(sample_records())
    assert walk2rec.main([str(SAMPLE / "ifXTable.txt"), "-o", str(out), "--strict"]) == 1


@pytest.mark.parametrize(
    ("kind", "value"),
    [("INTEGER", "up"), ("", "0:00:01.23"), ("Hex-STRING", "0G"), ("Weird", "x")],
)
def test_unconvertible_values_raise(kind: str, value: str) -> None:
    """A value with no ``.snmprec`` form is an explicit error, never a guess.

    Args:
        kind: Type label.
        value: Value text.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ConversionError):
        convert("1.3.6.1", kind, value, HINTS)


def test_custom_hex_hint_and_bits(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """``--hex-hint`` selects other DISPLAY-HINT columns; BITS keeps only its octets.

    Args:
        capsys: Output capture.
        tmp_path: Walk directory.

    Returns:
        None

    Raises:
        None
    """
    walk = tmp_path / "x.txt"
    walk.write_text(
        ".1.3.6.1.4.1.9.9.1.1 = STRING: aa:bb\n.1.3.6.1.4.1.9.9.2.1 = BITS: 40 00 rx(1)\n"
    )
    assert walk2rec.main([str(walk), "--hex-hint", "1.3.6.1.4.1.9.9.1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["1.3.6.1.4.1.9.9.1.1|4x|aabb", "1.3.6.1.4.1.9.9.2.1|4x|4000"]
