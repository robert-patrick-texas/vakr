"""CFG-007, CFG-010, CFG-011, META-008, E.19: plan, apply, disable-on-removal, and claims.

On the memory metadata adapter: a plan changes nothing and is
deterministic; an apply writes everything in one unit of work with audit
records (SEC-018) and is idempotent; removing a device disables it and
keeps it; an explicit ``uuid`` keeps identity across renames (META-001);
a discovered interface is governed by the file only after an audited
claim, and is released when its annotation leaves the file.

Dependencies:
    pytest; the memory metadata adapter; vakr.inventory.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_apply.py
"""

import tomllib
from dataclasses import replace

import pytest

from tests.unit.inventory.inventory_fixtures import SAMPLE_INVENTORY, T0, discovery, row
from vakr.adapters.memory import memory_uow_factory
from vakr.domain.enums import AdminState, InterestClass
from vakr.domain.errors import OwnershipError
from vakr.domain.ids import CollectorId, new_id
from vakr.domain.inventory import API_MANAGED
from vakr.domain.paging import PageRequest
from vakr.inventory.apply import apply_inventory
from vakr.inventory.claim import ClaimError, claim_interface
from vakr.inventory.errors import InventoryError
from vakr.inventory.guardrails import DEFAULT_DENIED, TargetPolicy
from vakr.inventory.model import FILE_OWNER, InventoryFile
from vakr.inventory.parse import parse_document
from vakr.inventory.plan import make_plan
from vakr.inventory.reads import device_interfaces
from vakr.inventory.render import plan_dict, plan_lines
from vakr.inventory.service import InventoryService
from vakr.inventory.snapshot import load_snapshot
from vakr.inventory.validate import Checks
from vakr.policy.rules import Policy
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import AuditFilter, MetadataUnitOfWorkFactory

CHECKS = Checks(frozenset({"network-v2c"}), None, TargetPolicy.parse([], list(DEFAULT_DENIED)))
CLOCK = ManualClock(T0)


def doc(text: str = SAMPLE_INVENTORY) -> InventoryFile:
    """Parse inventory text.

    Args:
        text: TOML text.

    Returns:
        InventoryFile: The file.

    Raises:
        None
    """
    return parse_document(tomllib.loads(text), "inventory.toml")


def plan_text(factory: MetadataUnitOfWorkFactory, inv: InventoryFile) -> list[str]:
    """Plan without applying.

    Args:
        factory: Opens units of work.
        inv: The file.

    Returns:
        list[str]: Rendered plan.

    Raises:
        None
    """
    with factory() as uow:
        return plan_lines(make_plan(inv, load_snapshot(uow, inv)))


def test_cfg010_plan_is_a_deterministic_dry_run() -> None:
    """CFG-010: the plan changes nothing; reordering the file does not reorder the plan.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    first = plan_text(factory, doc())
    reordered = SAMPLE_INVENTORY.replace("sw1.example.net", "aa.example.net")
    assert plan_text(factory, doc()) == first
    assert first[:4] == [
        "inventory.toml: 3 create",
        "  create site dal1: code DAL1",
        "  create device edge1.example.net: 192.0.2.10 at site dal1",
        "  create device sw1.example.net: 192.0.2.20 at site dal1",
    ]
    assert plan_text(factory, doc(reordered))[2].startswith("  create device aa.example.net")
    with factory() as uow:
        assert uow.change_counter() == 0


def test_cfg011_apply_is_atomic_audited_and_idempotent() -> None:
    """CFG-011, SEC-018, META-008: one apply writes everything with audit; the second, nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    plan, _ = apply_inventory(factory, doc(), CHECKS, "cli:netops", CLOCK)
    assert plan.summary == "3 create"
    with factory() as uow:
        edge = uow.devices.get_by_name("edge1.example.net")
        counter = uow.change_counter()
        events = uow.audit.list_page(PageRequest()).items
    assert edge is not None
    assert edge.managed_by == FILE_OWNER
    assert edge.credential_ref == "network-v2c"
    assert edge.comment == "Primary WAN router"
    assert sorted(e.action for e in events) == [
        "inventory.apply", "inventory.device.create", "inventory.device.create",
        "inventory.site.create",
    ]  # fmt: skip
    assert {e.actor for e in events} == {"cli:netops"}
    again, _ = apply_inventory(factory, doc(), CHECKS, "cli:netops", CLOCK)
    assert (again.changes, again.unchanged) == ((), 3)
    with factory() as uow:
        assert uow.change_counter() == counter
        assert uow.audit.list_page(PageRequest()).total == len(events)
        with pytest.raises(OwnershipError):
            uow.devices.update(replace(edge, comment="ui edit"), writer=API_MANAGED)


def test_cfg007_removed_devices_are_disabled_and_return() -> None:
    """CFG-007, META-009, 11.2: removal disables and keeps the record; re-adding enables it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    apply_inventory(factory, doc(), CHECKS, "cli:netops", CLOCK)
    without = SAMPLE_INVENTORY.split('[[devices]]\nname = "sw1')[0]
    without += SAMPLE_INVENTORY.split('reachability = "snmp_fallback"\n')[1]
    plan, _ = apply_inventory(factory, doc(without), CHECKS, "cli:netops", CLOCK)
    assert [(c.action.value, c.name) for c in plan.changes] == [("disable", "sw1.example.net")]
    with factory() as uow:
        sw = uow.devices.get_by_name("sw1.example.net")
    assert sw is not None
    assert sw.admin_state is AdminState.DISABLED
    assert apply_inventory(factory, doc(without), CHECKS, "x", CLOCK)[0].changes == ()
    back, _ = apply_inventory(factory, doc(), CHECKS, "cli:netops", CLOCK)
    assert back.changes[0].details == ("admin_state: disabled -> enabled",)


def test_meta001_uuid_keeps_identity_across_rename_and_updates_show_fields() -> None:
    """META-001, META-010: an explicit uuid survives a rename; updates name each field.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    uuid = "01900000-0000-7000-8000-0000000000aa"
    first = (
        f'[[devices]]\nname = "r1"\naddress = "192.0.2.1"\ncredential = "network-v2c"\n'
        f'uuid = "{uuid}"\n'
    )
    apply_inventory(factory, doc(first), CHECKS, "cli:netops", CLOCK)
    renamed = first.replace('"r1"', '"r1-new"') + 'interval = "300s"\ntags = ["core"]\n'
    plan, _ = apply_inventory(factory, doc(renamed), CHECKS, "cli:netops", CLOCK)
    assert plan.changes[0].details == (
        "name: r1 -> r1-new", "tags: none -> [core]", "interval_s: none -> 300")  # fmt: skip
    with factory() as uow:
        device = uow.devices.get_by_name("r1-new")
        core = uow.tags.get_by_name("core")
    assert device is not None
    assert str(device.id) == uuid
    assert core is not None
    assert core.managed_by == FILE_OWNER
    assert device.tags == {core.id}
    assert plan_dict(plan)["changes"][0]["id"] == uuid


def test_validation_failures_write_nothing() -> None:
    """DB-002: a file with problems is refused before any write.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    bad = SAMPLE_INVENTORY.replace('credential = "network-v2c"\ntags', 'credential = "nope"\ntags')
    with pytest.raises(InventoryError, match=r"devices\[0\]\.credential"):
        apply_inventory(factory, doc(bad), CHECKS, "cli:netops", CLOCK)
    with factory() as uow:
        assert uow.change_counter() == 0


def test_e19_claim_then_govern_then_release() -> None:
    """E.19, META-008, SEC-018: annotations wait for an audited claim, then apply, then release.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    apply_inventory(factory, doc(), CHECKS, "cli:netops", CLOCK)
    with factory() as uow:
        edge = uow.devices.get_by_name("edge1.example.net")
    assert edge is not None
    service = InventoryService(factory, new_id(CollectorId), Policy(), CLOCK)
    service.record_discovery(edge.id, discovery([row(1, "Gi0/0/0"), row(2, "Gi0/0/1")]), T0)
    waiting = plan_text(factory, doc())
    claim = "vakr interface claim edge1.example.net Gi0/0/0"
    assert waiting[-1] == (
        f"  edge1.example.net Gi0/0/0: API-managed; run `{claim}` to let inventory.toml govern it "
        "(E.19)"
    )
    with pytest.raises(ClaimError, match="no \\[\\[interface_annotations\\]\\] entry"):
        claim_interface(factory, doc(), "edge1.example.net", "Gi0/0/1", "cli:netops", CLOCK)
    claimed, changes = claim_interface(factory, doc(), "edge1.example.net", "gi0/0/0",
                                       "cli:netops", CLOCK)  # fmt: skip
    assert claimed.managed_by == FILE_OWNER
    assert claimed.annotation.interest is InterestClass.PRIMARY
    assert claimed.annotation.provider == "Example Carrier"
    assert "provider: none -> Example Carrier" in changes
    with pytest.raises(ClaimError, match="already governed"):
        claim_interface(factory, doc(), "edge1.example.net", "Gi0/0/0", "cli:netops", CLOCK)
    with factory() as uow:
        audit = uow.audit.list_page(PageRequest(), AuditFilter(object_id=claimed.id)).items
        with pytest.raises(OwnershipError):
            uow.interfaces.update(replace(claimed, comment="ui"), writer=API_MANAGED)
    assert [(a.action, a.actor) for a in audit] == [("interface.claim", "cli:netops")]
    assert "api:api -> file:inventory.toml" in audit[0].summary
    assert plan_text(factory, doc()) == ["inventory.toml: no changes"]
    edited = SAMPLE_INVENTORY.replace('"CKT-0001"', '"CKT-0002"')
    plan, _ = apply_inventory(factory, doc(edited), CHECKS, "cli:netops", CLOCK)
    assert plan.changes[0].details == ("circuit_id: CKT-0001 -> CKT-0002",)
    bare = SAMPLE_INVENTORY.split("[[interface_annotations]]")[0]
    released, _ = apply_inventory(factory, doc(bare), CHECKS, "cli:netops", CLOCK)
    assert released.changes[0].action.value == "release"
    with factory() as uow:
        (gi0,) = [i for i in device_interfaces(uow, edge.id) if i.if_name == "Gi0/0/0"]
    assert gi0.managed_by == API_MANAGED
    assert gi0.annotation.provider is None


def test_claim_refusals_explain_themselves() -> None:
    """E.19: undiscovered interfaces and unapplied devices cannot be claimed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    with pytest.raises(ClaimError, match="run `vakr inventory apply`"):
        claim_interface(factory, doc(), "edge1.example.net", "Gi0/0/0", "cli:netops", CLOCK)
    apply_inventory(factory, doc(), CHECKS, "cli:netops", CLOCK)
    with pytest.raises(ClaimError, match="not discovered yet"):
        claim_interface(factory, doc(), "edge1.example.net", "Gi0/0/0", "cli:netops", CLOCK)
