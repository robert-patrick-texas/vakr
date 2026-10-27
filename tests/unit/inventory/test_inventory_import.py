"""CFG-010, CFG-011, CONF-008, META-001, META-010: plan and apply bulk imports.

On the memory metadata adapter with every ownership case (``bulk_fixtures``):
the dry run is deterministic and changes nothing; an apply is atomic,
audited, and idempotent; records it creates are ``api:import``; rows
change only the fields they give; ``inventory.toml`` records and claimed
interfaces are never changed and ownership never moves; UUIDs keep
identity across renames of devices and interfaces.

Dependencies:
    pytest; tests.unit.inventory.bulk_fixtures.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_import.py
"""

from dataclasses import replace

import pytest

from tests.unit.inventory.bulk_fixtures import (
    CHECKS,
    CORE9_UUID,
    apply_raw,
    plan_of,
    plan_text,
    seeded,
)
from vakr.domain.enums import InterestClass
from vakr.domain.inventory import API_MANAGED
from vakr.domain.paging import PageRequest
from vakr.inventory.errors import InventoryError
from vakr.inventory.import_file import parse_import
from vakr.inventory.import_plan import import_snapshot, plan_import
from vakr.inventory.model import FILE_OWNER, IMPORT_OWNER
from vakr.inventory.reads import device_interfaces
from vakr.inventory.render import plan_dict
from vakr.inventory.validate import Checks
from vakr.ports.metadata import MetadataUnitOfWorkFactory

HEAD = b"name,address,credential,tags,comment\n"
ROWS = [b"core2,192.0.2.30,network-v2c,core;dc,new box\n",
        b"core9,192.0.2.90,network-v2c,core,\n",
        b"edge1.example.net,192.0.2.10,network-v2c,edge;wan,Primary WAN router\n",
        b"aa-lab,192.0.2.40,,,\n"]  # fmt: skip


def refused(factory: MetadataUnitOfWorkFactory, raw: bytes, name: str = "devices.csv") -> list[str]:
    """Plan an import that must be refused.

    Args:
        factory: Opens units of work.
        raw: File content.
        name: File name.

    Returns:
        list[str]: The problem lines.

    Raises:
        None
    """
    with pytest.raises(InventoryError) as caught:
        plan_of(factory, raw, name)
    return caught.value.lines


def counter(factory: MetadataUnitOfWorkFactory) -> tuple[int, int]:
    """Return the change counter and the audit event count.

    Args:
        factory: Opens units of work.

    Returns:
        tuple[int, int]: Both, to show nothing was written.

    Raises:
        None
    """
    with factory() as uow:
        return uow.change_counter(), len(uow.audit.list_page(PageRequest(limit=500)).items)


def test_cfg010_plan_is_deterministic_and_changes_nothing() -> None:
    """CFG-010, META-010: same input, same bytes; row order does not matter; storage untouched.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    before = counter(store.factory)
    raw = HEAD + b"".join(ROWS)
    first = "\n".join(plan_text(store.factory, raw, "devices.csv"))
    assert first == "\n".join(plan_text(store.factory, raw, "devices.csv"))
    assert first == "\n".join(
        plan_text(store.factory, HEAD + b"".join(reversed(ROWS)), "devices.csv")
    )
    assert first == "\n".join([
        "devices.csv: 2 create, 1 update, 1 unchanged",
        "  create device aa-lab: 192.0.2.40",
        "  create device core2: 192.0.2.30",
        "  update device core9: tags: none -> [core]",
    ])  # fmt: skip
    assert counter(store.factory) == before


def test_cfg011_apply_is_atomic_audited_and_idempotent() -> None:
    """CFG-011, SEC-018, E.38: api:import records, audit per change, nothing the second time.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    raw = HEAD + b"".join(ROWS)
    assert apply_raw(store.factory, raw, "devices.csv").summary == "2 create, 1 update, 1 unchanged"
    with store.factory() as uow:
        core2, core9 = uow.devices.get_by_name("core2"), uow.devices.get_by_name("core9")
        dc = uow.tags.get_by_name("dc")
        events = [e for e in uow.audit.list_page(PageRequest(limit=500)).items
                  if e.action.startswith("inventory.import")]  # fmt: skip
    assert core2 is not None
    assert core9 is not None
    assert dc is not None
    assert (core2.managed_by, core9.managed_by, dc.managed_by) == (IMPORT_OWNER, API_MANAGED,
                                                                   IMPORT_OWNER)  # fmt: skip
    assert (core2.credential_ref, core2.comment) == ("network-v2c", "new box")
    assert sorted(e.action for e in events) == [
        "inventory.import", "inventory.import.device.create", "inventory.import.device.create",
        "inventory.import.device.update"]  # fmt: skip
    assert {e.actor for e in events} == {"cli:netops"}
    assert "devices.csv: 2 create, 1 update, 1 unchanged" in [e.summary for e in events]
    after = counter(store.factory)
    again = apply_raw(store.factory, raw, "devices.csv")
    assert (again.changes, again.unchanged) == ((), 4)
    assert counter(store.factory) == after


def test_conf008_import_never_changes_file_records_or_ownership() -> None:
    """CONF-008, META-008, CFG-006: file records conflict when changed, pass when equal.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    edge = str(store.edge.id)
    raw = (b"name,address,comment,managed_by\n"
           b"edge1.example.net,192.0.2.10,changed,file:inventory.toml\n"
           b"sw1.example.net,192.0.2.20,,api:api\n"
           b"new1,192.0.2.50,,file:inventory.toml\n"
           b"core9,192.0.2.90,,api:import\n")  # fmt: skip
    before = counter(store.factory)
    assert refused(store.factory, raw) == [
        f"devices.csv: row 2, column comment: device {edge} is file:inventory.toml; change it in "
        "inventory.toml, not by import (CONF-008)",
        "devices.csv: row 3, column managed_by: the stored device is file:inventory.toml; an "
        "import never changes ownership (E.38)",
        "devices.csv: row 4, column managed_by: an import creates API-managed records "
        "(api:import); to manage this device from inventory.toml, add it there",
        "devices.csv: row 5, column managed_by: the stored device is api:api; an import never "
        "changes ownership (E.38)",
    ]
    with pytest.raises(InventoryError):
        apply_raw(store.factory, raw, "devices.csv")
    assert counter(store.factory) == before  # DB-002: refused before any write
    same = b"name,address,comment,managed_by\nedge1.example.net,192.0.2.10,Primary WAN router," \
           b"file:inventory.toml\n"  # fmt: skip
    assert plan_text(store.factory, same, "devices.csv") == ["devices.csv: no changes"]
    assert plan_of(store.factory, same, "devices.csv").unchanged == 1


def test_meta001_uuid_keeps_identity_across_renames() -> None:
    """META-001, META-002, META-010: a supplied UUID creates, renames, and follows interfaces.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    new = "01900000-0000-7000-8000-0000000000d1"
    apply_raw(store.factory, f"name,uuid,address\nlab1,{new},192.0.2.60\n".encode(), "d.csv")
    renamed = f"name,uuid,address\nlab1-new,{new},192.0.2.60\ncore9-b,{CORE9_UUID},192.0.2.90\n"
    plan = plan_of(store.factory, renamed.encode(), "d.csv")
    assert [(c.name, str(c.record_id), c.details) for c in plan.changes] == [
        ("core9-b", CORE9_UUID, ("name: core9 -> core9-b",)),
        ("lab1-new", new, ("name: lab1 -> lab1-new",)),
    ]
    assert plan_dict(plan)["changes"][1]["id"] == new
    stolen = f"name,uuid,address\nedge1.example.net,{new},192.0.2.60\n".encode()
    assert refused(store.factory, stolen, "d.csv") == [
        f"d.csv: row 2, column uuid: device name 'edge1.example.net' belongs to device "
        f"{store.edge.id} (file:inventory.toml), not to uuid {new}"]  # fmt: skip
    with store.factory() as uow:
        (te,) = device_interfaces(uow, store.core9.id)
        uow.interfaces.update_observed(replace(te, if_name="Hu1/0/1"))  # as a review map does
        uow.commit()
    note = f"device,ifname,uuid,sticky\ncore9,Te1/0/1,{te.id},true\n".encode()
    (change,) = plan_of(store.factory, note, "n.csv").changes
    assert (change.name, change.record_id) == ("core9 Hu1/0/1", te.id)
    assert change.details == ("sticky: False -> True",)


def test_rows_change_only_the_fields_they_give() -> None:
    """E.38: a column left out keeps the stored value; an empty cell clears it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    only_comment = b"name,comment\ncore9,patched\n"
    imp = parse_import(only_comment, "d.csv", None, "devices")
    with store.factory() as uow:
        (change,) = plan_import(imp, import_snapshot(uow, imp), CHECKS)[0].changes
    assert change.details == ("comment changed",)
    cleared = plan_of(store.factory, b"name,address,credential\ncore9,192.0.2.90,\n", "d.csv")
    assert cleared.changes[0].details == ("credential: network-v2c -> none",)
    toml = b'[[devices]]\nname = "core9"\ninterval = "5m"\n'
    assert (
        plan_text(store.factory, toml, "bulk.toml")[1]
        == "  update device core9: interval_s: none -> 300"
    )
    assert refused(store.factory, b"name,address\ncore9,\nlab9,\n", "d.csv") == [
        "d.csv: row 2, column address: cannot be empty; every device needs a poll address",
        "d.csv: row 3, column address: cannot be empty; every device needs a poll address",
        "d.csv: row 3, column address: required to create a device",
    ]


def test_device_rows_are_checked_like_inventory_toml() -> None:
    """CONF-008, CFG-004, 15.5: addresses, sites, credentials, guardrails, and repeats.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    raw = (b"name,address,site,credential,address_family\n"
           b"x1,192.0.2.10,,,\nx2,169.254.169.254,nowhere,netwrok-v2c,\nx3,192.0.2.70,,,\n"
           b"x3,192.0.2.71,,,\ncore9,,,,ipv6\n")  # fmt: skip
    found = refused(store.factory, raw)
    assert found == [
        "devices.csv: row 2, column address: already polled as file:inventory.toml device "
        "'edge1.example.net' (CONF-008)",
        "devices.csv: row 3, column address: 169.254.169.254 is denied by "
        "security.denied_target_addresses (169.254.169.254/32) (15.5)",
        "devices.csv: row 3, column site: no site 'nowhere' in storage or this import",
        "devices.csv: row 3, column credential: no SNMP credential named 'netwrok-v2c' in the "
        "credentials file",
        "devices.csv: row 5, column name: device name already used by row 4",
        "devices.csv: row 6, column address: cannot be empty; every device needs a poll address",
    ]
    family = b"name,address_family\ncore9,ipv6\n"
    assert refused(store.factory, family + b"", "d.csv") == [
        "d.csv: row 1: cannot tell devices from annotations by the header; pass --kind "
        "devices|annotations"]  # fmt: skip
    imp = parse_import(family, "d.csv", None, "devices")
    with (
        store.factory() as uow,
        pytest.raises(InventoryError, match="row 2, column address_family"),
    ):
        plan_import(imp, import_snapshot(uow, imp), CHECKS)
    twice = f"name,uuid,address\ncore9,,192.0.2.90\ncore9-alias,{CORE9_UUID},192.0.2.91\n".encode()
    assert refused(store.factory, twice)[-1] == (
        "devices.csv: row 3, column uuid: names the same device as row 2"
    )
    unread = Checks(None, "credentials.toml: permission denied", CHECKS.targets)
    imp = parse_import(b"name,address,credential\nz1,192.0.2.80,network-v2c\n", "d.csv", None, None)
    with (
        store.factory() as uow,
        pytest.raises(InventoryError, match="credential names not checked"),
    ):
        plan_import(imp, import_snapshot(uow, imp), unread)


def test_meta013_annotation_rows_classify_discovered_interfaces() -> None:
    """META-007, META-011, META-013, E.19: bulk-classify API interfaces without claims.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    raw = (b"device,ifname,interest,tags,provider\n"
           b"edge1.example.net,gi0/0/1,primary,uplink;isp,Example Carrier\n"
           b"edge1.example.net,Gi0/0/9,,,\n"
           b"edge1.example.net,Gi0/0/0,primary,,Example Carrier\n")  # fmt: skip
    plan = plan_of(store.factory, raw, "notes.csv")
    assert [(c.name, c.details) for c in plan.changes] == [("edge1.example.net Gi0/0/1", (
        "interest: secondary -> primary", "provider: none -> Example Carrier",
        "tags: none -> [isp, uplink]"))]  # fmt: skip
    assert [(w.entry.ifname, w.reason) for w in plan.waiting] == [("Gi0/0/9", "not discovered yet")]
    assert plan.unchanged == 1  # Gi0/0/0 is file-governed and already says so
    apply_raw(store.factory, raw, "notes.csv")
    with store.factory() as uow:
        gi1 = next(i for i in device_interfaces(uow, store.edge.id) if i.if_name == "Gi0/0/1")
        isp = uow.tags.get_by_name("isp")
    assert gi1.managed_by == API_MANAGED
    assert gi1.annotation.interest is InterestClass.PRIMARY
    assert isp is not None
    assert isp.id in gi1.tags
    assert isp.managed_by == IMPORT_OWNER
    assert plan_text(store.factory, raw, "notes.csv")[0] == "notes.csv: no changes"


def test_annotation_row_problems_and_waits() -> None:
    """CONF-008, E.19, E.38: claimed interfaces, unknown devices and UUIDs, new devices.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    with store.factory() as uow:
        gi0 = next(i for i in device_interfaces(uow, store.edge.id) if i.if_name == "Gi0/0/0")
    assert gi0.managed_by == FILE_OWNER
    raw = (b"device,ifname,uuid,sticky\n"
           b"edge1.example.net,Gi0/0/0,,true\n"
           b"ghost,Gi1,,true\n"
           b"core9,Te1/0/1," + str(gi0.id).encode() + b",true\n"
           b"core9,Te1/0/1,,true\ncore9,te1/0/1,,true\n")  # fmt: skip
    assert refused(store.factory, raw, "notes.csv") == [
        f"notes.csv: row 2, column sticky: interface {gi0.id} is file:inventory.toml; change its "
        "annotation in inventory.toml, not by import (CONF-008)",
        "notes.csv: row 3, column device: no device named 'ghost' in storage or this import",
        f"notes.csv: row 4, column uuid: no interface of core9 has uuid {gi0.id} (retired ones "
        "cannot be annotated)",
        "notes.csv: row 5, column ifname: this device and ifname already used by row 4",
        "notes.csv: row 6, column ifname: names the same interface as row 5",
    ]
    toml = (
        b'[[devices]]\nname = "lab2"\naddress = "192.0.2.61"\ncredential = "network-v2c"\n'
        b'[[interface_annotations]]\ndevice = "lab2"\nifname = "Gi1"\nsticky = true\n'
    )
    plan = plan_of(store.factory, toml, "bulk.toml")
    assert [w.reason for w in plan.waiting] == [
        "this import creates the device; import its annotations after discovery"]  # fmt: skip


def test_toml_import_creates_and_updates_api_sites() -> None:
    """META-006, META-010: a TOML import carries sites; file sites stay the file's.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    raw = (b'[[sites]]\nname = "aus1"\nregion = "US-South"\n'
           b'[[devices]]\nname = "lab3"\naddress = "192.0.2.62"\nsite = "aus1"\n'
           b'credential = "network-v2c"\n')  # fmt: skip
    assert plan_text(store.factory, raw, "bulk.toml") == [
        "bulk.toml: 2 create", "  create site aus1: code AUS1",
        "  create device lab3: 192.0.2.62 at site aus1"]  # fmt: skip
    apply_raw(store.factory, raw, "bulk.toml")
    with store.factory() as uow:
        aus1, lab3 = uow.sites.get_by_name("aus1"), uow.devices.get_by_name("lab3")
    assert aus1 is not None
    assert lab3 is not None
    assert (aus1.managed_by, lab3.site_id) == (IMPORT_OWNER, aus1.id)
    renamed = raw.replace(b'region = "US-South"', b'region = "US-Central"')
    assert plan_text(store.factory, renamed, "bulk.toml")[1] == (
        "  update site aus1: region: US-South -> US-Central"
    )
    file_site = b'[[sites]]\nname = "dal1"\nregion = "Elsewhere"\n'
    assert refused(store.factory, file_site, "bulk.toml")[0].startswith(
        "bulk.toml: sites[0].region: site "
    )
