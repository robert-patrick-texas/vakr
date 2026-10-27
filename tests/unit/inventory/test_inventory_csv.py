"""META-010, E.38: CSV import files: encoding, header, cells, bounds, and located errors.

The CSV reader turns rows into the same entries as ``inventory.toml``
tables, so these tests pin the CSV-specific rules (UTF-8 and BOM, header
names, cell counts, list, boolean, and number cells, the formula guard,
empty versus absent) and that a CSV row fails exactly where the same
TOML table fails, reported as ``row N, column C``.

Dependencies:
    pytest; vakr.inventory.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_csv.py
"""

import tomllib
from pathlib import Path

import pytest

from vakr.domain.enums import InterestClass
from vakr.inventory.csv_codec import guard, read_rows, unguard, write_rows
from vakr.inventory.errors import InventoryError
from vakr.inventory.files import MAX_FILE_BYTES
from vakr.inventory.import_file import parse_import, read_bounded
from vakr.inventory.parse import parse_document

DEVICES = b"name,address,tags,interval,credential\n"


def problems(raw: bytes, name: str = "devices.csv", kind: str | None = None) -> list[str]:
    """Parse a file that must fail and return its problem lines.

    Args:
        raw: File content.
        name: File name.
        kind: ``--kind``, if any.

    Returns:
        list[str]: ``file: place: reason`` lines.

    Raises:
        None
    """
    with pytest.raises(InventoryError) as caught:
        parse_import(raw, name, None, kind)
    return caught.value.lines


def test_meta010_rows_become_entries_with_given_fields() -> None:
    """META-010, E.38: cells are typed like TOML; given names columns; empty clears.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    raw = (
        "\ufeffName, Address ,tags,interval,credential\r\n"
        "edge1,2001:DB8::1, wan ; edge;;,2m,network-v2c\r\n"
        "sw1,192.0.2.20,,,\r\n,,,,\r\n"
    )
    inv = parse_import(raw.encode(), "devices.csv", None, None)
    edge, sw1 = inv.devices
    assert (edge.at, edge.address, edge.interval_s) == ("row 2", "2001:db8::1", 120)
    assert edge.tags == ("edge", "wan")
    assert edge.given == {"name", "address", "tags", "interval_s", "credential"}
    assert (sw1.tags, sw1.interval_s, sw1.credential, sw1.given) == ((), None, None, edge.given)
    assert not inv.sites
    assert not inv.annotations


def test_meta011_annotation_rows_and_high_priority_alias() -> None:
    """META-007, META-011, META-013: annotation cells; ``high_priority`` aliases interest.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    raw = (
        b"device,ifname,high_priority,wan_circuit,contracted_bps,display_rank,tags,alias,"
        b"managed_by\nedge1,Gi0/0/0,TRUE,False,100000000,0,transit;isp,anything,api:api\n"
    )
    (note,) = parse_import(raw, "notes.csv", None, None).annotations
    assert (note.interest, note.wan_circuit, note.contracted_bps, note.display_rank) == (
        InterestClass.PRIMARY, False, 100_000_000, 0)  # fmt: skip
    assert note.tags == ("isp", "transit")
    assert "interest" in note.given
    assert "alias" not in note.given  # export-only: never written
    assert str(note.managed_by) == "api:api"


def test_every_cell_error_is_located_by_row_and_column() -> None:
    """11.4, E.38: every bad cell in one run, as ``row N, column C``, in row order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    raw = (b"device,ifname,interest,high_priority,sticky,contracted_bps,capacity_override_bps,"
           b"display_rank,managed_by,uuid,tags\n"
           b"e1,Gi1,primary,false,maybe,1E+11,12345678901234567890,-1,owner,not-a-uuid,bad tag\n"
           b",Gi2,,,,,,,,,\n")  # fmt: skip
    assert problems(raw, "notes.csv") == [
        "notes.csv: row 2, column sticky: expected true or false",
        "notes.csv: row 2, column contracted_bps: is in scientific notation; format the column as "
        "text so every digit is kept",
        "notes.csv: row 2, column capacity_override_bps: at most 18 digits",
        "notes.csv: row 2, column display_rank: expected a whole number, such as 100000000",
        "notes.csv: row 2, column managed_by: expected an owner such as api:api or "
        "file:inventory.toml",
        "notes.csv: row 2, column tags: 'bad tag': use 1-64 letters, digits, '.', '_', ':' or '-'",
        "notes.csv: row 2, column high_priority: contradicts interest = 'primary'",
        "notes.csv: row 2, column uuid: uuid must be a hyphenated UUID string, got 'not-a-uuid'",
        "notes.csv: row 3, column device: required",
    ]


def test_csv_rows_fail_exactly_where_toml_tables_fail() -> None:
    """META-010: the same validation as TOML; only the location differs.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    toml = 'name = "r1"\naddress = "bad_host!"\ninterval = "7s"\nadmin_state = "gone"\n'
    with pytest.raises(InventoryError) as from_toml:
        parse_document(tomllib.loads("[[devices]]\n" + toml), "inventory.toml")
    csv = b"name,address,interval,admin_state\nr1,bad_host!,7s,gone\n"
    found = problems(csv)
    expected = [i.reason for i in from_toml.value.issues]
    assert [line.split(": ", 2)[2] for line in found] == expected
    assert found[0].startswith("devices.csv: row 2, column address: ")


def test_header_rules() -> None:
    """E.38: unknown, repeated, and unnamed columns; cell counts; kind detection.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert problems(b"name,address,colour\nr1,192.0.2.1,red\n") == [
        "devices.csv: row 1, column colour: unknown column; expected one of: address, "
        "address_family, admin_state, comment, credential, discovery_interval, groups, interval, "
        "managed_by, name, reachability, site, tags, uuid",
    ]
    assert problems(b"name,NAME,,address\nr1,r1,,x\nr2\n") == [
        "devices.csv: row 1, column name: repeated column",
        "devices.csv: row 1, column 3: has no name; name it or delete it",
        "devices.csv: row 3: has 1 cells; the header has 4",
    ]
    assert problems(b"name,comment\nr1,x\n") == [
        "devices.csv: row 1: cannot tell devices from annotations by the header; pass --kind "
        "devices|annotations",
    ]
    assert parse_import(b"name,comment\nr1,x\n", "d.csv", None, "devices").devices[0].comment == "x"
    assert problems(b"name,address\nr1,192.0.2.1\n", kind="annotations")[0].startswith(
        "devices.csv: row 1, column name: unknown column"
    )
    assert problems(b"") == ["devices.csv: empty; the first row must name the columns"]
    assert problems(b'name,address\n"r1,192.0.2.1\n')[0].startswith(
        "devices.csv: row 2: not valid CSV"
    )


def test_encoding_format_and_size_bounds(tmp_path: Path) -> None:
    """E.26, E.38: UTF-8 only; the format from the suffix or --format; 4 MiB.

    Args:
        tmp_path: Scratch directory.

    Returns:
        None

    Raises:
        None
    """
    assert problems("name,address\nr\xe9,192.0.2.1\n".encode("latin-1")) == [
        "devices.csv: not UTF-8 at byte 14; save it as CSV UTF-8 (E.38)"]  # fmt: skip
    assert problems(b"x", "devices.txt") == [
        "devices.txt: cannot tell the format; pass --format csv|toml"]  # fmt: skip
    assert parse_import(b"name,address\nr1,192.0.2.1\n", "d.txt", "csv", None).devices
    assert problems(b"[[devices]\n", "import.toml")[0].startswith("import.toml: not valid TOML")
    big = tmp_path / "big.csv"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(InventoryError, match="larger than 4194304 bytes"):
        read_bounded(big)
    with pytest.raises(InventoryError, match="cannot read"):
        read_bounded(tmp_path / "missing.csv")


def test_row_bounds() -> None:
    """E.26: at most 5,000 device rows and 20,000 annotation rows per file.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rows = b"".join(b"r%d,192.0.%d.%d\n" % (n, n // 250, n % 250 + 1) for n in range(5_001))
    assert problems(b"name,address\n" + rows) == ["devices.csv: more than 5000 rows (E.26)"]
    notes = [b"e1,Gi%d\n" % n for n in range(20_001)]
    over = b"device,ifname\n" + b"".join(notes)
    assert problems(over, "n.csv") == ["n.csv: more than 20000 rows (E.26)"]
    at_bound = b"device,ifname\n" + b"".join(notes[:-1])
    assert len(parse_import(at_bound, "n.csv", None, None).annotations) == 20_000


def test_formula_guard_round_trips() -> None:
    """E.30 #10, E.38: cells a spreadsheet would run are guarded and restored exactly.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    values = ["=1+1", "+x", "-uplink", "@SUM", "'=x", "''", "'", "'plain", "plain", "", "Gi0/0/0"]
    assert [guard(v) for v in values[:4]] == ["'=1+1", "'+x", "'-uplink", "'@SUM"]
    assert [unguard(guard(v)) for v in values] == values
    text = write_rows(["comment"], [[v] for v in values])
    assert text.startswith("comment\n'=1+1\n'+x\n")
    rows = read_rows(text.encode(), "x.csv", 100)
    assert [cells[0] for _, cells in rows.rows] == [v for v in values if v]


def test_multiline_and_quoted_cells_survive() -> None:
    """E.38: RFC 4180 quoting keeps commas, quotes, and newlines inside a cell.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    comment = 'Handed off on "port 3", rack B\nsecond line'
    text = write_rows(["name", "address", "comment"], [["r1", "192.0.2.1", comment]])
    (device,) = parse_import(text.encode(), "d.csv", None, None).devices
    assert device.comment == comment
