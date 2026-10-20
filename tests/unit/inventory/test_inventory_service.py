"""InventoryService: devices, change counter, discovery persistence, and poll plans.

Covers DISC-T01 (initial discovery gives persistent UUIDs), DISC-T05
(suppressed interfaces stay inventoried), IFID-T02/T05/T06/T08 through the
service, identity history (IFID-005), lifecycle MISSING to inactive and
stale (LIFE-T01, E.6), and the ``record_discovery`` to ``poll_plan`` round
trip the collector relies on.

Dependencies:
    pytest; the memory metadata adapter; tests.unit.inventory.inventory_fixtures.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_service.py
"""

import pytest

from tests.unit.inventory.inventory_fixtures import DAY, T0, add_device, discovery, row
from vakr.adapters.memory import memory_uow_factory
from vakr.domain.enums import (
    AdminState,
    CounterFamily,
    IfOperStatus,
    InterfaceLifecycle,
    ReconcileOutcome,
)
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import CollectorId, DeviceId, new_id
from vakr.domain.inventory import API_MANAGED
from vakr.domain.paging import PageRequest
from vakr.domain.reviews import ReviewState
from vakr.inventory.reads import device_interfaces, open_reviews
from vakr.inventory.service import InventoryService
from vakr.policy.rules_parse import parse_policy
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import AuditFilter, MetadataUnitOfWorkFactory

SKIP_LOOPBACKS = parse_policy(
    {
        "interface_policy": {
            "rules": [{"name": "no-loopbacks", "action": "exclude", "match": {"ifname": ["Lo*"]}}]
        }
    }
)


@pytest.fixture
def factory() -> MetadataUnitOfWorkFactory:
    """Open units of work on a fresh memory store.

    Args:
        None

    Returns:
        MetadataUnitOfWorkFactory: The factory.

    Raises:
        None
    """
    return memory_uow_factory()


def _service(factory: MetadataUnitOfWorkFactory) -> InventoryService:
    """Build the service with a policy that suppresses loopbacks.

    Args:
        factory: Opens units of work.

    Returns:
        InventoryService: The service.

    Raises:
        None
    """
    return InventoryService(factory, new_id(CollectorId), SKIP_LOOPBACKS, ManualClock(T0))


def test_disc_t01_initial_discovery_round_trip(factory: MetadataUnitOfWorkFactory) -> None:
    """DISC-T01, DISC-T05: discovery creates UUIDs; the plan carries them and policy's verdict.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory), add_device(factory)
    assert service.poll_plan(device.id) is None  # never discovered: needs discovery (DISC-001)
    rows = [row(1, "Gi0/0/0", if_alias="WAN to Example Carrier", hc_octets=False),
            row(2, "Lo0", if_type=24)]  # fmt: skip
    plan = service.record_discovery(device.id, discovery(rows), T0)
    assert plan == service.poll_plan(device.id)
    by_index = {p.if_index: p for p in plan.interfaces}
    assert by_index[1].collect
    assert by_index[1].family is CounterFamily.LEGACY32
    assert not by_index[2].collect
    assert by_index[2].check_status
    with factory() as uow:
        stored = {i.if_name: i for i in device_interfaces(uow, device.id)}
        wan = uow.tags.get_by_name("wan")
        history = uow.interfaces.identity_history(stored["Gi0/0/0"].id, PageRequest())
    assert set(stored) == {"Gi0/0/0", "Lo0"}  # DISC-007: suppressed Lo0 is inventoried
    assert by_index[1].interface_id == stored["Gi0/0/0"].id
    assert wan is not None
    assert stored["Gi0/0/0"].description_tags == {wan.id}
    assert stored["Gi0/0/0"].managed_by == API_MANAGED
    assert [e.outcome for e in history.items] == [ReconcileOutcome.NEW]


def test_ifid_t02_renumber_keeps_uuid_and_records_history(
    factory: MetadataUnitOfWorkFactory,
) -> None:
    """IFID-T02, IFID-005: a reload that renumbers keeps UUIDs; history keeps the old index.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory), add_device(factory)
    first = service.record_discovery(device.id, discovery([row(1, "Gi0/0/0")]), T0)
    counter = service.change_counter()
    again = service.record_discovery(device.id, discovery([row(1, "Gi0/0/0")]), T0 + DAY)
    assert again == first
    assert service.change_counter() == counter
    moved = service.record_discovery(device.id, discovery([row(7, "Gi0/0/0")]), T0 + 2 * DAY)
    assert moved.interfaces[0].if_index == 7
    assert moved.interfaces[0].interface_id == first.interfaces[0].interface_id
    assert service.change_counter() > counter
    with factory() as uow:
        iid = first.interfaces[0].interface_id
        assert iid is not None
        entries = uow.interfaces.identity_history(iid, PageRequest()).items
    renumber = entries[-1]
    assert renumber.outcome is ReconcileOutcome.CONFIDENT_RENUMBER
    assert (renumber.previous_if_index, renumber.observed_if_index) == (1, 7)
    assert renumber.decided_at == T0 + 2 * DAY
    assert renumber.evidence


def test_ifid_t06_probable_case_waits_for_review(factory: MetadataUnitOfWorkFactory) -> None:
    """IFID-T05/T06, IFID-004: neither identity receives observations; the review persists.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory), add_device(factory)
    first = service.record_discovery(device.id, discovery([row(5, "Gi0/0/5")]), T0)
    renamed = discovery([row(5, "Te0/0/5")])
    plan = service.record_discovery(device.id, renamed, T0 + DAY)
    assert [(p.if_index, p.interface_id) for p in plan.interfaces] == [(5, None)]
    service.record_discovery(device.id, renamed, T0 + 2 * DAY)  # same case: same review
    with factory() as uow:
        reviews = open_reviews(uow, device.id)
        audit = uow.audit.list_page(PageRequest(), AuditFilter(object_id=device.id)).items
    assert len(reviews) == 1
    assert reviews[0].candidates == (first.interfaces[0].interface_id,)
    assert reviews[0].outcome is ReconcileOutcome.PROBABLE
    assert reviews[0].if_name == "Te0/0/5"
    assert [a.action for a in audit] == ["discovery.reconcile", "discovery.reconcile"]
    back = service.record_discovery(device.id, discovery([row(5, "Gi0/0/5")]), T0 + 3 * DAY)
    assert back == first  # the case went away: review superseded, identity polled again
    with factory() as uow:
        closed = uow.reviews.get(reviews[0].id)
    assert closed is not None
    assert closed.state is ReviewState.SUPERSEDED


def test_ifid_t08_missing_then_returning(factory: MetadataUnitOfWorkFactory) -> None:
    """IFID-T08, LIFE-005, E.6: missing goes inactive and stale; it returns with its UUID.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory), add_device(factory)
    first = service.record_discovery(
        device.id, discovery([row(1, "Gi0/0/0"), row(2, "Gi0/0/1")]), T0
    )
    lost = first.interfaces[1].interface_id
    assert lost is not None
    partial = service.record_discovery(
        device.id, discovery([row(1, "Gi0/0/0")], complete=False), T0 + DAY
    )
    assert len(partial.interfaces) == 2  # DISC-008: an incomplete walk marks nothing missing
    gone = service.record_discovery(device.id, discovery([row(1, "Gi0/0/0")]), T0 + DAY)
    assert [p.if_index for p in gone.interfaces] == [1]
    with factory() as uow:
        stored = uow.interfaces.get(lost)
        assert stored is not None
        assert (stored.if_index, stored.lifecycle) == (None, InterfaceLifecycle.INACTIVE)
    service.record_discovery(device.id, discovery([row(1, "Gi0/0/0")]), T0 + 12 * DAY)
    with factory() as uow:
        stale = uow.interfaces.get(lost)
    assert stale is not None
    assert stale.lifecycle is InterfaceLifecycle.STALE
    back = service.record_discovery(
        device.id, discovery([row(1, "Gi0/0/0"), row(9, "Gi0/0/1")]), T0 + 13 * DAY
    )
    assert back.interfaces[1].interface_id == lost
    with factory() as uow:
        returned = uow.interfaces.get(lost)
        outcomes = [e.outcome for e in uow.interfaces.identity_history(lost, PageRequest()).items]
    assert returned is not None
    assert returned.lifecycle is InterfaceLifecycle.ACTIVE
    assert outcomes == [ReconcileOutcome.NEW, ReconcileOutcome.MISSING,
                        ReconcileOutcome.CONFIDENT_RENUMBER]  # fmt: skip


def test_devices_and_counter(factory: MetadataUnitOfWorkFactory) -> None:
    """7.5, 6.2: only enabled and maintenance devices are polled; any change bumps the counter.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service = _service(factory)
    a, b = add_device(factory, "a.example.net"), add_device(factory, "b.example.net")
    c = add_device(factory, "c.example.net")
    before = service.change_counter()
    with factory() as uow:
        uow.devices.set_admin_state(b.id, AdminState.MAINTENANCE, expected_version=1,
                                    writer=API_MANAGED, at=T0)  # fmt: skip
        uow.devices.set_admin_state(c.id, AdminState.DISABLED, expected_version=1,
                                    writer=API_MANAGED, at=T0)  # fmt: skip
        uow.commit()
    assert [d.id for d in service.devices()] == [a.id, b.id]
    assert service.change_counter() == before + 2
    service.replace_policy(SKIP_LOOPBACKS)
    assert service.change_counter() == before + 3
    with pytest.raises(NotFoundError):
        service.poll_plan(new_id(DeviceId))
    with pytest.raises(NotFoundError):
        service.record_discovery(new_id(DeviceId), discovery([]), T0)


def test_stale_interfaces_drop_status_checks(factory: MetadataUnitOfWorkFactory) -> None:
    """LIFE-004, LIFE-T01: an interface down for ten days goes stale and stops minute polls.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory), add_device(factory)
    down = row(3, "Gi0/0/3", oper_status=IfOperStatus.DOWN)
    plan = service.record_discovery(device.id, discovery([down]), T0)
    assert plan.interfaces[0].check_status
    assert plan.interfaces[0].collect
    later = service.record_discovery(device.id, discovery([down]), T0 + 11 * DAY)
    assert not later.interfaces[0].check_status
    assert not later.interfaces[0].collect
    with factory() as uow:
        (stored,) = device_interfaces(uow, device.id)
    assert (stored.lifecycle, stored.if_index) == (InterfaceLifecycle.STALE, 3)
