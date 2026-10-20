"""CFG-004, CFG-006, NETSEC-001, 11.4: inventory.toml problems are reported where they are.

Every problem names the file and the path inside it, every problem in a
file is reported in one run, and the checks cover duplicate names and
addresses, unknown sites, missing credentials (by name only), target
guardrails (15.5), and collisions with API-managed records (CONF-008).

Dependencies:
    pytest; vakr.inventory.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_validate.py
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.unit.inventory.inventory_fixtures import SAMPLE_INVENTORY, T0
from vakr.domain.ids import DeviceId, SiteId, new_id
from vakr.domain.inventory import API_MANAGED, Device, Site
from vakr.inventory.errors import InventoryError, render_issues
from vakr.inventory.files import MAX_FILE_BYTES, inventory_path, read_inventory
from vakr.inventory.guardrails import DEFAULT_DENIED, TargetPolicy, target_problem
from vakr.inventory.model import InventoryFile
from vakr.inventory.parse import parse_document
from vakr.inventory.snapshot import EMPTY, Snapshot, StoredDevice
from vakr.inventory.validate import Checks, validate

CREDS = frozenset({"network-v2c"})
POLICY = TargetPolicy.parse([], list(DEFAULT_DENIED))


def _lines(text: str, checks: Checks | None = None, snapshot: Snapshot = EMPTY) -> list[str]:
    """Parse and validate a document; return every problem line.

    Args:
        text: TOML text.
        checks: Checks to use; known credentials and default guardrails when ``None``.
        snapshot: Stored records.

    Returns:
        list[str]: ``inventory.toml: path: reason`` lines; empty when valid.

    Raises:
        None
    """
    try:
        inv = parse_document(tomllib.loads(text), "inventory.toml")
    except InventoryError as exc:
        return exc.lines
    found = validate(inv, snapshot, checks or Checks(CREDS, None, POLICY))
    return render_issues(inv.source, found.problems)


def test_sample_is_valid_and_canonical() -> None:
    """11.3: the documented sample parses, with defaults filled in.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    inv = parse_document(tomllib.loads(SAMPLE_INVENTORY), "inventory.toml")
    assert _lines(SAMPLE_INVENTORY) == []
    edge, sw = inv.devices
    assert (edge.tags, edge.groups, edge.site) == (("edge", "wan"), ("routers",), "dal1")
    assert (sw.interval_s, sw.reachability.value if sw.reachability else None) == (
        120,
        "snmp_fallback",
    )
    assert inv.annotations[0].interest.value == "primary"  # high_priority alias (META-011)
    assert inv.sites[0].code == "DAL1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[[devices]]\nname = "r1"\naddress = "300.1.1.1"\n',
         "inventory.toml: devices[0].address: '300.1.1.1' is neither an IP address"),
        ('[[devices]]\naddress = "192.0.2.1"\n', "inventory.toml: devices[0].name: required"),
        ('[[devices]]\nname = "r1"\naddress = "192.0.2.1"\ncolour = "red"\n',
         "inventory.toml: devices[0].colour: unknown key"),
        ('[[devices]]\nname = "r1"\naddress = "192.0.2.1"\ninterval = "5s"\n',
         "inventory.toml: devices[0].interval: must be whole seconds between 30s and 1h"),
        ('[[devices]]\nname = "r1"\naddress = "192.0.2.1"\ntags = ["has space"]\n',
         "inventory.toml: devices[0].tags: 'has space': use 1-64 letters"),
        ('[[devices]]\nname = "r1"\naddress = "192.0.2.1"\nuuid = "nope"\n',
         "inventory.toml: devices[0].uuid: uuid must be a hyphenated UUID string"),
        ('[[sites]]\nname = "x"\ntimezone = "Mars/Olympus"\n',
         "inventory.toml: sites[0].timezone: 'Mars/Olympus' is not an IANA timezone"),
        ('[[interface_annotations]]\ndevice = "r1"\nifname = "Gi1"\nhigh_priority = true\n'
         'interest = "secondary"\n', "contradicts interest = 'secondary'"),
        ('[[interface_annotations]]\ndevice = "r1"\nifname = "Gi1"\ncontracted_bps = 0\n',
         "interface_annotations[0].contracted_bps"),
        ('devices = 3\n', "inventory.toml: devices: expected an array of tables"),
        ('[extra]\n', "inventory.toml: extra: unknown key"),
    ],
)  # fmt: skip
def test_cfg004_parse_problems_name_their_place(text: str, expected: str) -> None:
    """11.4, CFG-004: a malformed entry is reported at its path.

    Args:
        text: TOML text.
        expected: Start of, or text in, the expected line.

    Returns:
        None

    Raises:
        None
    """
    lines = _lines(text)
    assert any(line.startswith(expected) or expected in line for line in lines), lines


def test_cfg004_cross_record_problems_are_all_reported() -> None:
    """CFG-004: duplicates, unknown sites, credentials, and annotations, in one run.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = """
[[devices]]
name = "r1"
address = "192.0.2.1"
site = "nowhere"
credential = "missing-cred"
[[devices]]
name = "r1"
address = "192.0.2.1"
credential = "network-v2c"
[[interface_annotations]]
device = "r9"
ifname = "Gi1"
"""
    assert _lines(text) == [
        "inventory.toml: devices[1].name: device name already used by devices[0]",
        "inventory.toml: devices[1].address: address already used by devices[0]",
        "inventory.toml: devices[0].site: no site 'nowhere' in the file or storage",
        "inventory.toml: devices[0].credential: no SNMP credential named 'missing-cred' "
        "in the credentials file",
        "inventory.toml: interface_annotations[0].device: no [[devices]] entry named 'r9'",
    ]


def test_credentials_unreadable_and_absent_are_explicit() -> None:
    """SEC-001: an unreadable provider is reported, never assumed; no credential is a warning.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = '[[devices]]\nname = "r1"\naddress = "192.0.2.1"\ncredential = "network-v2c"\n'
    lines = _lines(text, Checks(None, "file missing", POLICY))
    assert lines == ["inventory.toml: devices: credential names not checked: file missing"]
    bare = parse_document(tomllib.loads('[[devices]]\nname = "r1"\naddress = "192.0.2.1"\n'), "x")
    found = validate(bare, EMPTY, Checks(CREDS, None, POLICY))
    assert not found.problems
    assert "SNMP polling will fail" in found.warnings[0].reason


@pytest.mark.parametrize(
    ("address", "why"),
    [
        ("224.0.0.5", "is a multicast address"),
        ("0.0.0.0", "is an unspecified address"),  # noqa: S104  # a refused target, not a bind
        ("::", "is an unspecified address"),
        ("255.255.255.255", "is the broadcast address"),
        ("169.254.169.254", "is denied by security.denied_target_addresses"),
        ("::ffff:169.254.169.254", "is denied by security.denied_target_addresses"),
        ("fd00:ec2::254", "is denied by security.denied_target_addresses"),
    ],
)
def test_netsec001_targets_refused(address: str, why: str) -> None:
    """NETSEC-001, 15.5: unsafe and metadata-service targets fail validation.

    Args:
        address: The address.
        why: Expected reason.

    Returns:
        None

    Raises:
        None
    """
    assert (target_problem(address, POLICY) or "").startswith(why)
    text = f'[[devices]]\nname = "r1"\naddress = "{address}"\ncredential = "network-v2c"\n'
    assert any(why in line and "devices[0].address" in line for line in _lines(text))


def test_netsec001_allow_list_and_names() -> None:
    """15.5: an allow list confines targets; DNS names wait until they are resolved.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    confined = TargetPolicy.parse(["192.0.2.0/24", "2001:db8::/32"], [])
    assert target_problem("192.0.2.7", confined) is None
    assert target_problem("2001:db8::7", confined) is None
    assert "outside every network" in (target_problem("198.51.100.7", confined) or "")
    assert target_problem("edge1.example.net", confined) is None
    with pytest.raises(ValueError, match=r"security.allowed_target_cidrs\[0\]"):
        TargetPolicy.parse(["192.0.2.1/24"], [])


def _stored(name: str, address: str = "192.0.2.50") -> StoredDevice:
    """Build an API-managed stored device.

    Args:
        name: Device name.
        address: Poll address.

    Returns:
        StoredDevice: The device.

    Raises:
        None
    """
    return StoredDevice(Device(new_id(DeviceId), name, address, API_MANAGED, T0, T0), None, (), ())


def test_cfg006_api_managed_collisions_fail() -> None:
    """CFG-006, CONF-008: name, UUID, and address collisions with API records fail; no takeover.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    api = _stored("edge1", "192.0.2.10")
    site = Site(new_id(SiteId), "dal1", "DAL1", API_MANAGED)
    snapshot = Snapshot((site,), (api,), ())
    text = f"""
[[sites]]
name = "dal1"
[[devices]]
name = "edge1"
address = "192.0.2.99"
credential = "network-v2c"
[[devices]]
name = "edge2"
address = "192.0.2.10"
credential = "network-v2c"
[[devices]]
name = "edge3"
address = "192.0.2.11"
uuid = "{api.device.id}"
credential = "network-v2c"
"""
    lines = _lines(text, snapshot=snapshot)
    assert any(line.startswith("inventory.toml: sites[0].name: site ") and "api:api" in line
               and "CONF-008" in line for line in lines)  # fmt: skip
    assert any(line.startswith("inventory.toml: devices[0].name: device ") for line in lines)
    assert any("devices[1].address: already polled as api:api device 'edge1'" in x for x in lines)
    assert any(line.startswith("inventory.toml: devices[2].uuid: device ") for line in lines)


def test_files_are_bounded_and_optional(tmp_path: Path) -> None:
    """E.26: a missing file is no inventory; an oversized or non-TOML file is an error.

    Args:
        tmp_path: Directory for files.

    Returns:
        None

    Raises:
        None
    """
    assert inventory_path(tmp_path, "") == tmp_path / "inventory.toml"
    assert inventory_path(tmp_path, "/srv/inv.toml") == Path("/srv/inv.toml")
    assert read_inventory(tmp_path / "inventory.toml") is None
    big = tmp_path / "big.toml"
    big.write_bytes(b"#" * (MAX_FILE_BYTES + 1))
    with pytest.raises(InventoryError, match="larger than"):
        read_inventory(big)
    bad = tmp_path / "bad.toml"
    bad.write_text("[[devices]\n", encoding="utf-8")
    with pytest.raises(InventoryError, match=r"bad\.toml: not valid TOML"):
        read_inventory(bad)
    many: dict[str, Any] = {"devices": [{"name": "d", "address": "192.0.2.1"}] * 5001}
    with pytest.raises(InventoryError, match="at most 5000"):
        parse_document(many, "inventory.toml")
    assert isinstance(parse_document({}, "inventory.toml"), InventoryFile)
