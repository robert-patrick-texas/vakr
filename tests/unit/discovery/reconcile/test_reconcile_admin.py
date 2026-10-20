"""IFID-T07, IFID-005, DISC-008: administrator decisions and what discovery writes.

An administrator maps a review to an existing interface (its UUID and
history continue) or creates a new identity; both close the review, write
``ADMIN`` identity history naming the administrator, and audit the
decision; the next rediscovery then matches exactly. Discovery keeps what
a failed table could not refresh (ifAlias, MAC, addresses).

Dependencies:
    pytest; the memory metadata adapter; tests.unit.inventory.inventory_fixtures.

Example:
    $ uv run pytest tests/unit/discovery/reconcile/test_reconcile_admin.py
"""

import pytest

from tests.unit.inventory.inventory_fixtures import DAY, T0, add_device, discovery, row
from vakr.adapters.memory import memory_uow_factory
from vakr.discovery.reconcile.admin import ReviewError, create_identity, map_review
from vakr.discovery.reconcile.evidence import normalize, row_key, shown, strong_mac
from vakr.discovery.reconcile.records import MAX_EVIDENCE, bounded
from vakr.domain.enums import IdentityDecisionSource, ReconcileOutcome
from vakr.domain.ids import CollectorId, InterfaceId, ReviewId, new_id
from vakr.domain.paging import PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.inventory.reads import device_interfaces, open_reviews
from vakr.inventory.service import InventoryService
from vakr.policy.rules import Policy
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import AuditFilter, MetadataUnitOfWorkFactory

CLOCK = ManualClock(T0 + 2 * DAY)


def _held(factory: MetadataUnitOfWorkFactory) -> tuple[InventoryService, IdentityReview]:
    """Discover Gi0/0/5 at ifIndex 5, then see Te0/0/5 there: one probable review.

    Args:
        factory: Opens units of work.

    Returns:
        tuple[InventoryService, IdentityReview]: The service and the open review.

    Raises:
        None
    """
    service = InventoryService(factory, new_id(CollectorId), Policy(), CLOCK)
    device = add_device(factory)
    service.record_discovery(device.id, discovery([row(5, "Gi0/0/5")]), T0)
    service.record_discovery(device.id, discovery([row(5, "Te0/0/5")]), T0 + DAY)
    with factory() as uow:
        (review,) = open_reviews(uow, device.id)
    return service, review


def test_ifid_t07_map_continues_history_and_audits() -> None:
    """IFID-T07: mapping keeps the UUID, records ADMIN history, audits, and resolves.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    service, review = _held(factory)
    (old,) = review.candidates
    mapped = map_review(factory, review.id, old, "cli:admin", CLOCK)
    assert (mapped.id, mapped.if_index, mapped.if_name) == (old, 5, "Te0/0/5")
    with factory() as uow:
        closed = uow.reviews.get(review.id)
        history = uow.interfaces.identity_history(old, PageRequest()).items
        audit = uow.audit.list_page(PageRequest(), AuditFilter(actor="cli:admin")).items
    assert closed is not None
    assert (closed.state, closed.resolution) == (ReviewState.MAPPED, old)
    assert (history[-1].source, history[-1].actor) == (IdentityDecisionSource.ADMIN, "cli:admin")
    assert [a.action for a in audit] == ["interface.review.mapped"]
    plan = service.record_discovery(review.device_id, discovery([row(5, "Te0/0/5")]), T0 + 3 * DAY)
    assert [(p.if_index, p.interface_id) for p in plan.interfaces] == [(5, old)]
    with pytest.raises(ReviewError, match="already mapped"):
        map_review(factory, review.id, old, "cli:admin", CLOCK)


def test_ifid_t07_new_identity_releases_the_index() -> None:
    """IFID-T07: a new identity takes the ifIndex; the old one goes missing, history kept.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    service, review = _held(factory)
    (old,) = review.candidates
    created = create_identity(factory, review.id, "cli:admin", CLOCK)
    assert created.id != old
    assert created.if_index == 5
    with factory() as uow:
        previous = uow.interfaces.get(old)
        outcomes = [e.outcome for e in uow.interfaces.identity_history(old, PageRequest()).items]
    assert previous is not None
    assert previous.if_index is None
    assert outcomes[-1] is ReconcileOutcome.MISSING
    plan = service.record_discovery(review.device_id, discovery([row(5, "Te0/0/5")]), T0 + 3 * DAY)
    assert [(p.if_index, p.interface_id) for p in plan.interfaces] == [(5, created.id)]
    with pytest.raises(ReviewError, match="no identity review"):
        create_identity(factory, new_id(ReviewId), "cli:admin", CLOCK)


def test_map_refuses_an_interface_on_another_device() -> None:
    """IFID-007: a review maps only to an interface of its own device.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    _, review = _held(factory)
    with pytest.raises(ReviewError, match="not on the review's device"):
        map_review(factory, review.id, new_id(InterfaceId), "cli:admin", CLOCK)


def test_disc008_failed_tables_keep_known_values() -> None:
    """DISC-008: a row without ifAlias or MAC (table failed) keeps what was stored.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    service = InventoryService(factory, new_id(CollectorId), Policy(), CLOCK)
    device = add_device(factory)
    full = row(1, "Gi0/0/0", if_alias="WAN uplink", mac="00:00:5e:00:53:01",
               addresses=("192.0.2.1/30",))  # fmt: skip
    service.record_discovery(device.id, discovery([full]), T0)
    partial = row(1, "Gi0/0/0", if_alias=None, mac=None, missing=("ifAlias", "ifPhysAddress"))
    service.record_discovery(device.id, discovery([partial], ip_complete=False), T0 + DAY)
    with factory() as uow:
        (stored,) = device_interfaces(uow, device.id)
        tags = {t.name: t.id for n in ("wan", "uplink") if (t := uow.tags.get_by_name(n))}
    assert (stored.if_alias, stored.mac) == ("WAN uplink", "00:00:5e:00:53:01")
    assert stored.description_tags == {tags["wan"], tags["uplink"]}  # META-012, E.15
    assert stored.addresses == ("192.0.2.1/30",)


def test_odd_agent_values_never_fail_the_write() -> None:
    """DISC-007: a repeated address or ifType 0 is normalised, and the interface is stored.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    service = InventoryService(factory, new_id(CollectorId), Policy(), CLOCK)
    device = add_device(factory)
    odd = row(4, "Gi0/0/4", if_type=0, addresses=("192.0.2.9/31", "192.0.2.9/31"))
    service.record_discovery(device.id, discovery([odd]), T0)
    with factory() as uow:
        (stored,) = device_interfaces(uow, device.id)
    assert (stored.if_type, stored.addresses) == (None, ("192.0.2.9/31",))


def test_evidence_helpers() -> None:
    """IFID-008, 9.8 step 2: MAC strength, name normalisation, safe display, bounded evidence.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert strong_mac("00:00:5e:00:53:01")
    assert not strong_mac("00:00:00:00:00:00")
    assert not strong_mac("ff:ff:ff:ff:ff:ff")
    assert not strong_mac("01:00:5e:00:00:01")
    assert not strong_mac("00:00:5e:00:53:01:02:03")
    assert normalize(" Gi0/0/0\x00") == "gi0/0/0"
    assert row_key(row(1, "", if_descr="Serial0/1/0")) is not None
    assert row_key(row(1, "", if_descr="")) is None
    assert shown("bad\x07name") == "'bad?name'"
    many = tuple(f"item {n}" for n in range(40))
    assert len(bounded(many)) == MAX_EVIDENCE
    assert bounded(many)[-1] == f"{40 - MAX_EVIDENCE + 1} more evidence items not stored"
