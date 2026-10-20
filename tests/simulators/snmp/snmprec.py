"""Simulator records and the snmpsim ``.snmprec`` format.

A ``.snmprec`` file has one ``OID|TAG|VALUE`` line per object, sorted by
OID. ``TAG`` is the BER tag number of the SNMP type (2 INTEGER, 4 OCTET
STRING, 6 OBJECT IDENTIFIER, 64 IpAddress, 65 Counter32, 66 Gauge32, 67
TimeTicks, 70 Counter64); a trailing ``x`` means the value is hex. A value
that should advance with time, such as a counter or ``sysUpTime``, uses
snmpsim's ``numeric`` variation: ``65:numeric|rate=125000,initial=7``.

``Record.rate`` keeps that intent, so the in-process agent can advance the
same records from its own clock.

Dependencies:
    dataclasses, pathlib: Records and files.

Example:
    >>> r = Record((1, 3, 6, 1, 2, 1, 1, 3, 0), "67", "4200", rate=100)
    >>> r.line()
    '1.3.6.1.2.1.1.3.0|67:numeric|rate=100,initial=4200'
    >>> parse_line(r.line()) == r
    True
"""

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

type Oid = tuple[int, ...]

TAGS = {
    "2": "Integer32",
    "4": "OctetString",
    "5": "Null",
    "6": "ObjectIdentifier",
    "64": "IpAddress",
    "65": "Counter32",
    "66": "Gauge32",
    "67": "TimeTicks",
    "68": "Opaque",
    "70": "Counter64",
}
"""snmprec tag numbers and the SMIv2 type each one means."""
WRAP = {"65": 2**32, "67": 2**32, "70": 2**64}
"""Where numeric values wrap, by tag."""


@dataclass(frozen=True, slots=True)
class Record:
    """One simulated object.

    Attributes:
        oid: The object identifier.
        tag: snmprec tag, such as ``"70"`` or ``"4x"`` (hex octets).
        value: The value text: decimal, text, dotted OID, IPv4, or hex.
        rate: Units per second for values that advance with time.
    """

    oid: Oid
    tag: str
    value: str
    rate: int | None = None

    def line(self) -> str:
        """Render the record as one ``.snmprec`` line.

        Args:
            None

        Returns:
            str: ``OID|TAG|VALUE``, using the numeric variation for rates.

        Raises:
            None
        """
        oid = ".".join(map(str, self.oid))
        if self.rate is None:
            return f"{oid}|{self.tag}|{self.value}"
        return f"{oid}|{self.tag}:numeric|rate={self.rate},initial={self.value}"


def parse_line(line: str) -> Record:
    """Parse one ``.snmprec`` line written by ``Record.line``.

    Args:
        line: The line, without its newline.

    Returns:
        Record: The record.

    Raises:
        ValueError: If the line is not ``OID|TAG|VALUE``.
    """
    oid_text, tag, value = line.split("|", 2)
    oid = tuple(int(arc) for arc in oid_text.split("."))
    if tag.endswith(":numeric"):
        settings = dict(item.split("=", 1) for item in value.split(","))
        return Record(oid, tag.removesuffix(":numeric"), settings["initial"], int(settings["rate"]))
    return Record(oid, tag, value)


def sort_records(records: Iterable[Record]) -> list[Record]:
    """Sort records in OID order, the order snmpsim and agents walk them.

    Args:
        records: Records in any order.

    Returns:
        list[Record]: Sorted by OID.

    Raises:
        ValueError: If two records share an OID.
    """
    ordered = sorted(records, key=lambda record: record.oid)
    for before, after in itertools.pairwise(ordered):
        if before.oid == after.oid:
            raise ValueError(f"duplicate OID {before.oid}")
    return ordered


def write_snmprec(path: Path, records: Sequence[Record]) -> None:
    """Write records as a sorted ``.snmprec`` file.

    Args:
        path: Destination file.
        records: The records.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written.
    """
    text = "".join(record.line() + "\n" for record in sort_records(records))
    path.write_text(text, encoding="utf-8")


def read_snmprec(path: Path) -> list[Record]:
    """Read a ``.snmprec`` file written by ``write_snmprec``.

    Args:
        path: The file.

    Returns:
        list[Record]: Its records, in file order.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If a line is malformed.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return [parse_line(line) for line in lines if line and not line.startswith("#")]
