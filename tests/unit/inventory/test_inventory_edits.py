"""Manual device entry and annotation edits through the service (27.4, META-008, CONF-008).

``DeviceEditor`` and ``AnnotationEditor`` over a memory store: created
records are API-managed, the file's field rules and checks apply, a stale
version is a conflict, a file-managed record is refused naming the file
and each field, retiring is a soft delete, every change is audited, and
collectors see each change through the change counter. Addresses are RFC
5737 documentation addresses.

Dependencies:
    pytest; vakr.inventory.edits; vakr.inventory.apply; tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_edits.py
"""

import pytest

from tests.unit.query.query_testkit import Fleet
from vakr.domain.catalog import Tag
from vakr.domain.enums import AdminState, InterestClass, InterfaceLifecycle
from vakr.domain.errors import ConflictError, NotFoundError, ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, TagId, new_id
from vakr.domain.inventory import API_MANAGED
from vakr.domain.paging import PageRequest
from vakr.inventory.apply import apply_inventory
from vakr.inventory.edits.annotations import AnnotationEditor
from vakr.inventory.edits.bulk import MAX_BULK_INTERFACES
from vakr.inventory.edits.devices import DeviceEditor
from vakr.inventory.edits.ownership import FileManagedError
from vakr.inventory.edits.records import find_device, find_interface
from vakr.inventory.errors import InventoryError
from vakr.inventory.guardrails import DEFAULT_DENIED, TargetPolicy
from vakr.inventory.model import FILE_OWNER, DeviceEntry, InventoryFile
from vakr.inventory.service import InventoryService
from vakr.inventory.validate import Checks
from vakr.policy.rules import Policy

CHECKS = Checks(frozenset({"network-v2c"}), targets=TargetPolicy.parse([], list(DEFAULT_DENIED)))
"""One credential name (no secret is involved, SEC-001) and the default guardrails (15.5)."""


def editors(fleet: Fleet) -> tuple[DeviceEditor, AnnotationEditor]:
    """Build both editors over a fleet.

    Args:
        fleet: The stores.

    Returns:
        tuple[DeviceEditor, AnnotationEditor]: The editors.

    Raises:
        None
    """
    return DeviceEditor(fleet.uow, fleet.clock, lambda: CHECKS), AnnotationEditor(
        fleet.uow, fleet.clock
    )


def audit_actions(fleet: Fleet) -> list[tuple[str, str, str]]:
    """List the audit events, oldest first.

    Args:
        fleet: The stores.

    Returns:
        list[tuple[str, str, str]]: Actor, action, and summary of each.

    Raises:
        None
    """
    with fleet.uow() as uow:
        events = uow.audit.list_page(PageRequest(100)).items
    return [(e.actor, e.action, e.summary) for e in reversed(events)]


def test_meta008_a_created_device_is_api_managed_audited_and_polled() -> None:
    """META-008, SEC-018, 6.2: API-managed, tags created, audited; collectors see it at once.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    fleet.site("dal1", "DAL1")
    devices, _ = editors(fleet)
    service = InventoryService(fleet.uow, new_id(CollectorId), Policy(), fleet.clock)
    before = service.change_counter()
    made = devices.create({"name": "edge9.example.net", "address": " 192.0.2.9 ", "site": "dal1",
                           "credential": "network-v2c", "tags": ["wan", "edge"],
                           "groups": ["routers"], "interval": "2m"}, actor="alice")  # fmt: skip
    d = made.stored.device
    assert (d.managed_by, d.version, d.poll_address, d.poll_interval_s) == (
        API_MANAGED, 1, "192.0.2.9", 120)  # fmt: skip
    assert (made.stored.site, made.stored.tags, made.stored.groups) == (
        "dal1", ("edge", "wan"), ("routers",))  # fmt: skip
    assert made.warnings == ()
    with fleet.uow() as uow:
        tag = uow.tags.get_by_name("wan")
    assert tag is not None
    assert tag.managed_by == API_MANAGED
    assert service.change_counter() > before
    assert [x.name for x in service.devices()] == ["edge9.example.net"]
    [(actor, action, summary)] = audit_actions(fleet)
    assert (actor, action) == ("alice", "device.create")
    assert "address: none -> 192.0.2.9" in summary
    assert "tags: none -> [edge, wan]" in summary


def test_conf008_the_files_checks_and_api_conflicts_refuse_by_field() -> None:
    """CONF-008, 15.5, META-010: guardrails, credentials, sites, names, addresses; nothing stored.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    fleet.device("edge1.example.net", poll_address="192.0.2.10", managed_by=FILE_OWNER)
    devices, _ = editors(fleet)
    with pytest.raises(InventoryError) as caught:
        devices.create({"name": "edge1.example.net", "address": "192.0.2.10", "site": "nowhere",
                        "credential": "missing"}, actor="alice")  # fmt: skip
    paths = sorted({i.path for i in caught.value.issues})
    assert paths == ["address", "credential", "name", "site"]
    with pytest.raises(InventoryError) as caught:  # field rules first, as when parsing the file
        devices.create({"name": "", "address": "192.0.2.300", "interval": "5s"}, actor="alice")
    assert sorted(i.path for i in caught.value.issues) == ["address", "interval", "name"]
    with pytest.raises(InventoryError) as caught:
        devices.create({"name": "meta", "address": "169.254.169.254"}, actor="alice")
    assert "security.denied_target_addresses" in str(caught.value)
    with pytest.raises(InventoryError) as caught:
        devices.create({"name": "x", "address": "192.0.2.11", "uuid": "nope"}, actor="alice")
    assert caught.value.issues[0].path == "uuid"
    warned = devices.create({"name": "sw9", "address": "192.0.2.19"}, actor="alice")
    assert [w.path for w in warned.warnings] == ["credential"]  # SNMP would fail: a warning
    assert [a for _, a, _ in audit_actions(fleet)] == ["device.create"]


def test_db003_a_change_merges_clears_and_refuses_a_stale_version() -> None:
    """DB-003: absent keeps, None clears, nothing to change writes nothing, stale is a conflict.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    devices, _ = editors(fleet)
    made = devices.create({"name": "edge9", "address": "192.0.2.9", "credential": "network-v2c",
                           "interval": "2m", "comment": "lab"}, actor="alice")  # fmt: skip
    key = made.stored.device.id
    changed = devices.update(key, {"interval": None, "admin_state": "maintenance"}, version=1,
                             actor="bob")  # fmt: skip
    d = changed.stored.device
    assert (d.version, d.poll_interval_s, d.admin_state, d.comment) == (
        2, None, AdminState.MAINTENANCE, "lab")  # fmt: skip
    assert changed.changes == ("admin_state: enabled -> maintenance", "interval_s: 120 -> none")
    same = devices.update(key, {"comment": "lab"}, version=1, actor="bob")
    assert (same.changes, same.stored.device.version) == ((), 2)
    with pytest.raises(ConflictError):
        devices.update(key, {"comment": "new"}, version=1, actor="bob")
    with pytest.raises(NotFoundError):
        devices.update(new_id(DeviceId), {"comment": "x"}, version=1, actor="bob")
    assert [a for _, a, _ in audit_actions(fleet)] == ["device.create", "device.update"]


def test_meta008_a_file_managed_device_is_refused_naming_the_file_and_fields() -> None:
    """META-008, CFG-006: no silent takeover; an unchanged field is no change at all.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    stored = fleet.device("edge1", poll_address="192.0.2.10", managed_by=FILE_OWNER)
    devices, _ = editors(fleet)
    with pytest.raises(FileManagedError) as caught:
        devices.update(stored.id, {"address": "192.0.2.12", "comment": "x"}, version=1,
                       actor="alice")  # fmt: skip
    assert caught.value.fields == ("address", "comment")
    assert "managed by inventory.toml" in str(caught.value)
    assert "vakr inventory apply" in str(caught.value)
    assert devices.update(stored.id, {"name": "edge1"}, version=1, actor="alice").changes == ()
    with pytest.raises(FileManagedError):
        devices.retire(stored.id, version=None, actor="alice")


def test_meta009_retiring_is_a_soft_delete_and_enabled_restores() -> None:
    """META-009: polling stops, interfaces stay; a later apply leaves the API device alone.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    devices, _ = editors(fleet)
    made = devices.create({"name": "edge9", "address": "192.0.2.9", "credential": "network-v2c"},
                          actor="alice")  # fmt: skip
    iface = fleet.interface(made.stored.device, "Gi0/0/0")
    service = InventoryService(fleet.uow, new_id(CollectorId), Policy(), fleet.clock)
    gone = devices.retire(made.stored.device.id, version=1, actor="alice")
    assert gone.stored.device.admin_state is AdminState.RETIRED
    assert devices.retire(made.stored.device.id, version=None, actor="alice").changes == ()
    assert service.devices() == ()
    with fleet.uow() as uow:
        assert uow.interfaces.get(iface.id) is not None
    with pytest.raises(InventoryError):  # the name stays taken while retired
        devices.create({"name": "edge9", "address": "192.0.2.99"}, actor="alice")
    back = devices.update(gone.stored.device.id, {"admin_state": "enabled"}, version=2,
                          actor="alice")  # fmt: skip
    assert back.stored.device.admin_state is AdminState.ENABLED
    entry = DeviceEntry("devices[0]", "edge9", "192.0.2.50", credential="network-v2c")
    with pytest.raises(InventoryError, match="CONF-008"):
        apply_inventory(fleet.uow, InventoryFile("inventory.toml", devices=(entry,)), CHECKS,
                        "cli:netops", fleet.clock)  # fmt: skip


def test_meta011_annotations_merge_with_the_alias_and_keep_description_tags() -> None:
    """META-007, META-011, META-013: alias, bounds, vakr tags apart, site override kept.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    device = fleet.device("edge1", poll_address="192.0.2.10")
    wan = fleet.add("tags", Tag(new_id(TagId), "isp", API_MANAGED, "from a keyword"))
    iface = fleet.interface(device, "Gi0/0/0", description_tags=frozenset({wan.id}))
    _, notes = editors(fleet)
    made = notes.annotate(iface.id, {"high_priority": True, "wan_circuit": True,
                                     "provider": "Example Carrier", "contracted_bps": 10**8,
                                     "tags": ["uplink"]}, version=1, actor="alice")  # fmt: skip
    a = made.interface.annotation
    assert (a.interest, a.is_wan_circuit, a.provider, a.contracted_bps) == (
        InterestClass.PRIMARY, True, "Example Carrier", 10**8)  # fmt: skip
    assert (made.tags, made.description_tags) == (("uplink",), ("isp",))
    assert "interest: secondary -> primary" in made.changes
    with pytest.raises(InventoryError) as caught:
        notes.annotate(iface.id, {"interest": "secondary", "high_priority": True,
                                  "contracted_bps": 0}, version=2, actor="alice")  # fmt: skip
    assert sorted(i.path for i in caught.value.issues) == ["contracted_bps", "high_priority"]
    cleared = notes.annotate(iface.id, {"provider": None, "tags": []}, version=2, actor="bob")
    assert (cleared.interface.annotation.provider, cleared.tags) == (None, ())
    assert cleared.description_tags == ("isp",)  # META-013: never touched by vakr tags
    with pytest.raises(ConflictError):
        notes.annotate(iface.id, {"sticky": True}, version=2, actor="bob")
    assert [a for _, a, _ in audit_actions(fleet)] == ["interface.annotate"] * 2


def test_e19_a_claimed_interface_is_refused_naming_the_file() -> None:
    """META-008, E.19: an interface inventory.toml governs changes only through the file.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    device = fleet.device("edge1", poll_address="192.0.2.10", managed_by=FILE_OWNER)
    claimed = fleet.interface(device, "Gi0/0/0", managed_by=FILE_OWNER)
    free = fleet.interface(device, "Gi0/0/1", if_index=2)
    _, notes = editors(fleet)
    with pytest.raises(FileManagedError) as caught:
        notes.annotate(claimed.id, {"sticky": True}, version=1, actor="alice")
    assert caught.value.fields == ("sticky",)
    assert "[[interface_annotations]]" in str(caught.value)
    with pytest.raises(FileManagedError) as caught:
        notes.tag([free.id, claimed.id], add=["wan"], actor="alice")
    assert caught.value.fields == ("interfaces[1]",)
    with fleet.uow() as uow:  # all or nothing
        unchanged = uow.interfaces.get(free.id)
    assert unchanged is not None
    assert unchanged.tags == frozenset()


def test_meta013_bulk_tagging_adds_and_removes_on_many_interfaces() -> None:
    """META-013: incremental, idempotent, bounded, and audited per interface.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    device = fleet.device("edge1", poll_address="192.0.2.10")
    ports = [fleet.interface(device, f"Gi0/0/{n}", if_index=n + 1) for n in range(3)]
    _, notes = editors(fleet)
    ids = [p.id for p in ports]
    first = notes.tag(ids[:2], add=["wan", "isp"], actor="alice")
    assert (first.changed, first.unchanged) == (tuple(ids[:2]), ())
    again = notes.tag(ids, add=["wan", "isp"], remove=["nowhere"], actor="alice")
    assert (again.changed, again.unchanged) == ((ids[2],), tuple(ids[:2]))
    off = notes.tag(ids, remove=["isp"], actor="bob")
    assert len(off.changed) == 3
    first_record = notes.get(ids[0])
    assert first_record is not None
    assert first_record.tags == ("wan",)
    for bad in ([], [ids[0], ids[0]]):
        with pytest.raises(ValidationError):
            notes.tag(bad, add=["wan"], actor="bob")
    with pytest.raises(ValidationError):
        notes.tag(ids, add=["wan"], remove=["wan"], actor="bob")
    with pytest.raises(ValidationError):
        notes.tag(ids, actor="bob")
    with pytest.raises(InventoryError):
        notes.tag(ids, add=["not a tag"], actor="bob")
    with pytest.raises(ValidationError):
        notes.tag(ids * (MAX_BULK_INTERFACES // 3 + 1), add=["x"], actor="bob")
    with pytest.raises(NotFoundError):
        notes.tag([new_id(InterfaceId)], add=["x"], actor="bob")
    assert sum(a == "interface.tag" for _, a, _ in audit_actions(fleet)) == 6


def test_meta013_editing_a_retired_interface_is_refused_by_name_lookup_only() -> None:
    """IFID-002: CLI lookups by name skip retired interfaces; UUID edits still reach them.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    device = fleet.device("edge1", poll_address="192.0.2.10")
    old = fleet.interface(device, "Gi0/0/0", lifecycle=InterfaceLifecycle.RETIRED, if_index=None)
    with fleet.uow() as uow:
        assert find_device(uow, str(device.id)) == find_device(uow, "edge1") == device
        with pytest.raises(NotFoundError):
            find_interface(uow, device, "gi0/0/0")
    _, notes = editors(fleet)
    assert notes.annotate(old.id, {"comment": "gone"}, version=1, actor="a").interface.version == 2
