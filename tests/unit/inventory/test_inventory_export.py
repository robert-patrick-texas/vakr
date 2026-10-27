"""META-010, CFG-011, E.38: exports round-trip, are byte-identical, and carry UUIDs and owners.

Every export plans as no changes against the storage it came from: through
``vakr inventory import`` in both formats, and, for a ``--owner file`` TOML
export, through ``vakr inventory plan`` as an ``inventory.toml``. A mixed
export fed to ``inventory plan`` is refused by CONF-008, which shows the
two paths never blur ownership.

Dependencies:
    pytest; tests.unit.inventory.bulk_fixtures.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_export.py
"""

import re
import tomllib
from dataclasses import replace

import pytest

from tests.unit.inventory.bulk_fixtures import CHECKS, Seeded, plan_text, seeded, toml_doc
from vakr.domain.enums import AdminState, InterfaceLifecycle
from vakr.inventory import export_data
from vakr.inventory.export_data import ExportError, load_export
from vakr.inventory.export_render import export_csv, export_toml, toml_string
from vakr.inventory.plan import make_plan
from vakr.inventory.reads import device_interfaces
from vakr.inventory.render import plan_lines
from vakr.inventory.snapshot import load_snapshot
from vakr.inventory.validate import validate

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def exported(store: Seeded, fmt: str, kind: str | None = None, owner: str = "all",
             *, unannotated: bool = False) -> str:  # fmt: skip
    """Export the store.

    Args:
        store: The seeded store.
        fmt: ``toml`` or ``csv``.
        kind: ``devices``, ``annotations``, or ``None``.
        owner: ``all``, ``file``, or ``api``.
        unannotated: List interfaces with nothing set too.

    Returns:
        str: The export text.

    Raises:
        None
    """
    with store.factory() as uow:
        found = load_export(uow, owner, unannotated=unannotated)
    if fmt == "toml":
        return export_toml(found, kind)
    assert kind is not None
    return export_csv(found, kind)


def test_meta010_every_export_plans_as_no_changes() -> None:
    """META-010, CFG-011: TOML and CSV exports, every owner filter, re-plan to nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    cases = [
        ("toml", None, "all"), ("toml", None, "api"), ("toml", "devices", "file"),
        ("csv", "devices", "all"), ("csv", "annotations", "all"), ("csv", "annotations", "api"),
    ]  # fmt: skip
    for fmt, kind, owner in cases:
        for unannotated in (False, True):
            text = exported(store, fmt, kind, owner, unannotated=unannotated)
            lines = plan_text(store.factory, text.encode(), f"export.{fmt}")
            assert lines == [f"export.{fmt}: no changes"], (fmt, kind, owner, unannotated)


def test_file_export_is_an_inventory_toml_that_plans_as_no_changes() -> None:
    """META-010, CONF-008: an --owner file export is inventory.toml; a mixed one is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    inv = toml_doc(exported(store, "toml", owner="file"))
    with store.factory() as uow:
        snapshot = load_snapshot(uow, inv)
    assert validate(inv, snapshot, CHECKS).problems == ()
    assert plan_lines(make_plan(inv, snapshot)) == ["inventory.toml: no changes"]
    assert [a.ifname for a in inv.annotations] == ["Gi0/0/0"]
    mixed = toml_doc(exported(store, "toml"))
    with store.factory() as uow:
        problems = validate(mixed, load_snapshot(uow, mixed), CHECKS).problems
    assert [p.path for p in problems] == [
        "devices[0].uuid",
        "devices[0].address",
    ]  # core9: CONF-008


def test_export_is_byte_identical_ordered_and_carries_uuids_and_owners() -> None:
    """META-010, E.38: the same storage gives the same bytes, in name order, with owners.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    text = exported(store, "toml")
    assert text == exported(store, "toml")
    assert _UUID.sub("U", text) == "\n".join([
        "# vakr inventory export (owner all): 1 site(s), 3 device(s), 1 annotation(s).",
        "# Plan it with `vakr inventory import FILE`; an --owner file export is also an "
        "inventory.toml.", "",
        "# file:inventory.toml", "[[sites]]", 'name = "dal1"', 'uuid = "U"', 'code = "DAL1"',
        'region = "US-South"', "",
        "# api:api", "[[devices]]", 'name = "core9"', 'uuid = "U"', 'address = "192.0.2.90"',
        'credential = "network-v2c"', "",
        "# file:inventory.toml", "[[devices]]", 'name = "edge1.example.net"', 'uuid = "U"',
        'address = "192.0.2.10"', 'site = "dal1"', 'credential = "network-v2c"',
        'tags = ["edge", "wan"]', 'groups = ["routers"]', 'comment = "Primary WAN router"', "",
        "# file:inventory.toml", "[[devices]]", 'name = "sw1.example.net"', 'uuid = "U"',
        'address = "192.0.2.20"', 'site = "dal1"', 'credential = "network-v2c"',
        'interval = "2m"', 'reachability = "snmp_fallback"', "",
        "# file:inventory.toml", "[[interface_annotations]]", 'device = "edge1.example.net"',
        'ifname = "Gi0/0/0"', 'uuid = "U"', 'interest = "primary"', "wan_circuit = true",
        'provider = "Example Carrier"', 'circuit_id = "CKT-0001"', "contracted_bps = 100000000",
        "",
    ])  # fmt: skip
    with store.factory() as uow:
        edge_ifaces = device_interfaces(uow, store.edge.id)
    assert str(store.edge.id) in text
    assert str(edge_ifaces[0].id) in text
    devices = exported(store, "csv", "devices")
    assert devices.splitlines()[0] == ("name,uuid,managed_by,address,site,credential,admin_state,"
                                       "interval,discovery_interval,reachability,address_family,"
                                       "tags,groups,comment")  # fmt: skip
    assert devices.splitlines()[2].startswith(
        f"edge1.example.net,{store.edge.id},file:inventory.toml,"
    )
    assert ",edge;wan,routers,Primary WAN router" in devices
    partial = exported(store, "toml", "devices", "file").splitlines()[1]
    assert partial.endswith("; only devices: not a whole inventory.toml.")


def test_annotation_csv_lists_the_classification_worklist() -> None:
    """META-013, E.30 #10: --unannotated lists every interface; aliases are formula-guarded.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    assert [line.split(",")[:2] for line in exported(store, "csv", "annotations").splitlines()] == [
        ["device", "ifname"], ["edge1.example.net", "Gi0/0/0"]]  # fmt: skip
    rows = exported(store, "csv", "annotations", unannotated=True).splitlines()
    assert [r.split(",")[1] for r in rows[1:]] == ["Te1/0/1", "Gi0/0/0", "Gi0/0/1", "Gi0/0/2"]
    assert ",'=HYPERLINK(1)," in rows[3]  # the alias the device reported, guarded
    edited = "\n".join([rows[0], rows[3].replace("'=HYPERLINK(1)", "ignored alias")]) + "\n"
    assert plan_text(store.factory, edited.encode(), "notes.csv") == ["notes.csv: no changes"]


def test_export_states_and_names() -> None:
    """E.38: ended devices, retired and unnameable interfaces are left out; governed kept.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    with store.factory() as uow:
        core9 = uow.devices.get(store.core9.id)
        assert core9 is not None
        uow.devices.set_admin_state(core9.id, AdminState.DISABLED, expected_version=core9.version,
                                    writer=core9.managed_by, at=core9.updated_at)  # fmt: skip
        edge = uow.devices.get(store.edge.id)
        assert edge is not None
        uow.devices.set_admin_state(edge.id, AdminState.DISABLED, expected_version=edge.version,
                                    writer=edge.managed_by, at=edge.updated_at)  # fmt: skip
        gi1, gi2 = [
            i for i in device_interfaces(uow, edge.id) if i.if_name in ("Gi0/0/1", "Gi0/0/2")
        ]
        uow.interfaces.set_lifecycle(gi1.id, InterfaceLifecycle.RETIRED,
                                     expected_version=gi1.version, at=gi1.updated_at,
                                     inactive_since=None)  # fmt: skip
        uow.interfaces.update_observed(replace(gi2, if_name="bad\tname"))
        uow.commit()
    text = exported(store, "toml", unannotated=True)
    assert "core9" not in text  # disabled, and API-managed: nothing of it is governed
    assert 'name = "edge1.example.net"' in text  # disabled, but inventory.toml governs Gi0/0/0
    assert "Gi0/0/1" not in text
    assert "bad" not in text
    assert plan_text(store.factory, text.encode(), "export.toml") == ["export.toml: no changes"]


def test_toml_strings_round_trip() -> None:
    """E.38: quoting survives quotes, backslashes, control characters, and non-ASCII.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for text in [
        'say "hi"',
        "back\\slash",
        "line\nbreak\ttab",
        "del\x7f",
        "nul\x00",
        "Zürich ✓",
        "",
    ]:
        assert tomllib.loads(f"k = {toml_string(text)}")["k"] == text


def test_export_is_bounded_by_what_one_import_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    """E.26, E.38: more rows than one import file may hold are refused, not truncated.

    Args:
        monkeypatch: Lowers the bound.

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    monkeypatch.setattr(export_data, "MAX_ANNOTATIONS", 2)
    with store.factory() as uow, pytest.raises(ExportError, match="more than 5000 devices or 2"):
        load_export(uow, "all", unannotated=True)
    with store.factory() as uow:
        assert len(load_export(uow, "all").interfaces) == 1


def test_meta010_inventory_toml_annotation_uuid_follows_a_renamed_interface() -> None:
    """META-002, META-010, E.38: a file annotation with ``uuid`` survives the interface's rename.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = seeded()
    with store.factory() as uow:
        (gi0,) = [i for i in device_interfaces(uow, store.edge.id) if i.if_name == "Gi0/0/0"]
        uow.interfaces.update_observed(replace(gi0, if_name="Te0/0/0"))  # as a review map does
        uow.commit()
    text = exported(store, "toml", owner="file").replace('ifname = "Te0/0/0"', 'ifname = "Gi0/0/0"')
    edited = toml_doc(text.replace('circuit_id = "CKT-0001"', 'circuit_id = "CKT-0002"'))
    with store.factory() as uow:
        snapshot = load_snapshot(uow, edited)
    assert validate(edited, snapshot, CHECKS).problems == ()
    (change,) = make_plan(edited, snapshot).changes
    assert (change.name, change.record_id) == ("edge1.example.net Te0/0/0", gi0.id)
    assert change.details == ("circuit_id: CKT-0001 -> CKT-0002",)
    unknown = toml_doc(text.replace(str(gi0.id), str(store.core9.id)))
    with store.factory() as uow:
        plan = make_plan(unknown, load_snapshot(uow, unknown))
    assert [w.reason for w in plan.waiting] == [
        f"no interface of edge1.example.net has uuid {store.core9.id}"]  # fmt: skip
    twice = toml_doc(text + text[text.index("# file:inventory.toml\n[[interface_annotations]]"):]
                     .replace('ifname = "Gi0/0/0"', 'ifname = "Gi0/0/9"'))  # fmt: skip
    with store.factory() as uow:
        problems = validate(twice, load_snapshot(uow, twice), CHECKS).problems
    assert [(p.path, p.reason) for p in problems] == [
        ("interface_annotations[1].uuid", "uuid already used by interface_annotations[0]")
    ]
