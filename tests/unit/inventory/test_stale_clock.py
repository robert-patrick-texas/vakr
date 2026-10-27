"""LIFE-003: the ten-day stale clock counts from when an interface went inactive, nothing else.

Until metadata schema 7 the clock started at the interface's ``updated_at``,
which annotation and tag edits also move, so editing a down interface's
comment restarted its countdown (E.26, approved fix of 2026-10-27). The
stored ``inactive_since`` now carries the start: set on the move into
INACTIVE with the evidence time, kept through INACTIVE to STALE and while
the interface stays inactive, cleared on the move back to ACTIVE, and never
touched by an operator edit. Every scenario runs on the memory and the
SQLite metadata adapters, through discovery, Stage 1, and the
administrator's review decisions.

Dependencies:
    pytest; the memory and SQLite metadata adapters; tests.unit.inventory.inventory_fixtures.

Example:
    $ uv run pytest tests/unit/inventory/test_stale_clock.py
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.adapters.sqlite.sqlite_testkit import migrated_store
from tests.unit.inventory.inventory_fixtures import DAY, T0, add_device, discovery, row
from vakr.adapters.memory import memory_uow_factory
from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.discovery.reconcile.admin import create_identity, map_review
from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.interfaces import Interface
from vakr.inventory.edits.annotations import AnnotationEditor
from vakr.inventory.reads import device_interfaces, open_reviews
from vakr.inventory.service import InventoryService
from vakr.inventory.stage1_lifecycle import StatusEvidence
from vakr.policy.rules import Policy
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

HOUR = timedelta(hours=1)
WENT_DOWN = T0 + DAY
"""When the interface first went inactive in each scenario: ``T``."""


@pytest.fixture(params=["memory", "sqlite"])
def factory(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MetadataUnitOfWorkFactory]:
    """Open units of work on a fresh memory store or a migrated SQLite file.

    Args:
        request: Which adapter.
        tmp_path: Directory for the SQLite file.

    Returns:
        Iterator[MetadataUnitOfWorkFactory]: The factory, for the test's duration.

    Raises:
        None
    """
    if request.param == "memory":
        yield memory_uow_factory()
        return
    store = migrated_store(tmp_path / "metadata.db")
    yield sqlite_uow_factory(store)
    store.close()


def _service(factory: MetadataUnitOfWorkFactory, clock: ManualClock) -> InventoryService:
    """Build the inventory service with the default ten-day ``lifecycle.stale_after``.

    Args:
        factory: Opens units of work.
        clock: Time source for audit records.

    Returns:
        InventoryService: The service.

    Raises:
        None
    """
    return InventoryService(factory, new_id(CollectorId), Policy(), clock)


def _only(factory: MetadataUnitOfWorkFactory, device_id: DeviceId) -> Interface:
    """Read a device's single interface.

    Args:
        factory: Opens units of work.
        device_id: The device.

    Returns:
        Interface: The stored interface.

    Raises:
        None
    """
    with factory() as uow:
        (stored,) = device_interfaces(uow, device_id)
    return stored


def _edit(factory: MetadataUnitOfWorkFactory, interface_id: InterfaceId, at: datetime) -> None:
    """Annotate and tag an interface at ``at``, as an operator does in the web UI.

    Args:
        factory: Opens units of work.
        interface_id: The interface.
        at: Edit time.

    Returns:
        None

    Raises:
        None
    """
    editor = AnnotationEditor(factory, ManualClock(at))
    editor.annotate(interface_id, {"comment": "uplink to dc2, circuit pending"}, version=None,
                    actor="cli:netops")  # fmt: skip
    editor.tag([interface_id], add=["wan"], actor="cli:netops")


def _down(at: datetime, interface_id: InterfaceId) -> StatusEvidence:
    """Build a Stage 1 reading of up/down.

    Args:
        at: When Stage 1 read it.
        interface_id: The interface.

    Returns:
        StatusEvidence: The reading.

    Raises:
        None
    """
    return StatusEvidence(interface_id, at, IfAdminStatus.UP, IfOperStatus.DOWN)


def test_life003_an_edit_never_restarts_the_stale_clock_in_discovery(
    factory: MetadataUnitOfWorkFactory,
) -> None:
    """LIFE-003 regression: down at T, edited at T+5d, discovery makes it STALE at T+10d.

    The old code counted from ``updated_at`` (T+5d) and kept it INACTIVE until T+15d.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory, ManualClock(T0)), add_device(factory)
    down = row(3, "Gi0/0/3", oper_status=IfOperStatus.DOWN)
    service.record_discovery(device.id, discovery([row(3, "Gi0/0/3")]), T0)
    service.record_discovery(device.id, discovery([down]), WENT_DOWN)
    iface = _only(factory, device.id)
    assert (iface.lifecycle, iface.inactive_since) == (InterfaceLifecycle.INACTIVE, WENT_DOWN)
    _edit(factory, iface.id, WENT_DOWN + 5 * DAY)
    edited = _only(factory, device.id)
    assert (edited.comment, len(edited.tags)) == ("uplink to dc2, circuit pending", 1)
    assert edited.updated_at == WENT_DOWN + 5 * DAY  # the edits moved updated_at ...
    assert edited.inactive_since == WENT_DOWN  # ... but not the stale clock
    service.record_discovery(device.id, discovery([down]), WENT_DOWN + 10 * DAY - HOUR)
    assert _only(factory, device.id).lifecycle is InterfaceLifecycle.INACTIVE
    plan = service.record_discovery(device.id, discovery([down]), WENT_DOWN + 10 * DAY + HOUR)
    stale = _only(factory, device.id)
    assert (stale.lifecycle, stale.inactive_since) == (InterfaceLifecycle.STALE, WENT_DOWN)
    assert not plan.interfaces[0].check_status  # LIFE-004: down to the discovery cadence


def test_life003_an_edit_never_restarts_the_stale_clock_in_stage1(
    factory: MetadataUnitOfWorkFactory,
) -> None:
    """LIFE-003 regression through Stage 1: down at T, edited at T+5d, STALE at T+10d, then up.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory, ManualClock(T0)), add_device(factory)
    service.record_discovery(device.id, discovery([row(3, "Gi0/0/3")]), T0)
    iface = _only(factory, device.id)
    assert (iface.lifecycle, iface.inactive_since) == (InterfaceLifecycle.ACTIVE, None)
    assert service.record_stage1_status([_down(WENT_DOWN, iface.id)]) == 1
    assert _only(factory, device.id).inactive_since == WENT_DOWN  # set on entering INACTIVE
    _edit(factory, iface.id, WENT_DOWN + 5 * DAY)
    assert _only(factory, device.id).inactive_since == WENT_DOWN
    assert service.record_stage1_status([_down(WENT_DOWN + 10 * DAY + HOUR, iface.id)]) == 1
    stale = _only(factory, device.id)
    assert (stale.lifecycle, stale.inactive_since) == (InterfaceLifecycle.STALE, WENT_DOWN)
    up = StatusEvidence(iface.id, WENT_DOWN + 11 * DAY, IfAdminStatus.UP, IfOperStatus.UP)
    assert service.record_stage1_status([up]) == 1
    back = _only(factory, device.id)
    assert (back.lifecycle, back.inactive_since) == (InterfaceLifecycle.ACTIVE, None)
    assert service.record_stage1_status([_down(WENT_DOWN + 12 * DAY, iface.id)]) == 1
    again = _only(factory, device.id)  # a new inactive period starts a new clock
    assert (again.lifecycle, again.inactive_since) == (
        InterfaceLifecycle.INACTIVE,
        WENT_DOWN + 12 * DAY,
    )


def test_life003_discovery_sets_keeps_and_clears_the_clock_start(
    factory: MetadataUnitOfWorkFactory,
) -> None:
    """LIFE-003, E.6: new and down, a new cause, stale, up, missing, and back, through discovery.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    service, device = _service(factory, ManualClock(T0)), add_device(factory)
    down = row(3, "Gi0/0/3", oper_status=IfOperStatus.DOWN)
    service.record_discovery(device.id, discovery([down]), T0)
    assert _only(factory, device.id).inactive_since == T0  # a new identity seen down
    shut = row(3, "Gi0/0/3", if_alias="spare", admin_status=IfAdminStatus.DOWN)
    service.record_discovery(device.id, discovery([shut]), T0 + 3 * DAY)
    renamed = _only(factory, device.id)  # a new cause and a new ifAlias: same period
    assert (renamed.if_alias, renamed.inactive_since) == ("spare", T0)
    service.record_discovery(device.id, discovery([shut]), T0 + 11 * DAY)
    stale = _only(factory, device.id)
    assert (stale.lifecycle, stale.inactive_since) == (InterfaceLifecycle.STALE, T0)
    service.record_discovery(device.id, discovery([row(3, "Gi0/0/3")]), T0 + 12 * DAY)
    assert _only(factory, device.id).inactive_since is None
    service.record_discovery(device.id, discovery([]), T0 + 13 * DAY)
    missing = _only(factory, device.id)
    assert (missing.lifecycle, missing.if_index, missing.inactive_since) == (
        InterfaceLifecycle.INACTIVE,
        None,
        T0 + 13 * DAY,
    )
    service.record_discovery(device.id, discovery([]), T0 + 24 * DAY)
    assert _only(factory, device.id).inactive_since == T0 + 13 * DAY  # STALE while missing
    service.record_discovery(device.id, discovery([row(3, "Gi0/0/3")]), T0 + 25 * DAY)
    back = _only(factory, device.id)
    assert (back.lifecycle, back.inactive_since) == (InterfaceLifecycle.ACTIVE, None)


def test_life003_review_decisions_start_and_clear_the_clock(
    factory: MetadataUnitOfWorkFactory,
) -> None:
    """IFID-007, LIFE-003: a new identity and a released index start it; a mapping clears it.

    Args:
        factory: Opens units of work.

    Returns:
        None

    Raises:
        None
    """
    decided = T0 + 2 * DAY
    clock = ManualClock(decided)
    service = _service(factory, clock)
    device = add_device(factory)
    down = row(5, "Gi0/0/5", oper_status=IfOperStatus.DOWN)
    up = row(6, "Gi0/0/6")
    service.record_discovery(device.id, discovery([down, up]), T0)
    service.record_discovery(device.id, discovery([row(5, "Te0/0/5"), row(6, "Te0/0/6")]),
                             T0 + DAY)  # fmt: skip
    with factory() as uow:
        five, six = sorted(open_reviews(uow, device.id), key=lambda r: r.if_index)
    (inactive_old,) = five.candidates
    mapped = map_review(factory, five.id, inactive_old, "cli:admin", clock)
    assert (mapped.lifecycle, mapped.inactive_since) == (InterfaceLifecycle.ACTIVE, None)
    (active_old,) = six.candidates
    created = create_identity(factory, six.id, "cli:admin", clock)
    assert (created.lifecycle, created.inactive_since) == (InterfaceLifecycle.INACTIVE, decided)
    with factory() as uow:
        released = uow.interfaces.get(active_old)
    assert released is not None
    assert (released.lifecycle, released.if_index, released.inactive_since) == (
        InterfaceLifecycle.INACTIVE,
        None,
        decided,
    )
