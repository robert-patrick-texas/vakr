"""Contract: the admin-operation repository (API-011, DISC-T03, API-T06, port extension E.32).

Subclass ``OperationsContract`` in an adapter's test module; the adapter's
``conftest.py`` supplies ``metadata_uow_factory``. Covers the round trip,
one active operation per device, the compare-and-set claim that lets only
one of two collectors win, final states, the bounded lists in both orders,
bounded purges, and that operations never move the change counter.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.domain.operations.

Example:
    $ uv run pytest tests/contract -k Operations
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from tests.contract import contract_samples as s
from tests.contract.contract_samples import T0
from vakr.domain.catalog import Collector
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import CollectorId, DeviceId, OperationId, new_id
from vakr.domain.inventory import Device
from vakr.domain.operations import Operation, OperationKind, OperationResult, OperationState
from vakr.domain.paging import PageRequest
from vakr.ports.metadata import MetadataUnitOfWorkFactory, OperationFilter

RESULT = OperationResult(48, new=2, renumbered=1, missing=1, reviews_opened=1,
                         capability_changes=3, partial=True, duration_ms=2400)  # fmt: skip


def operation(device_id: DeviceId, at_s: int = 0, **overrides: Any) -> Operation:
    """Build a queued rediscovery.

    Args:
        device_id: The device.
        at_s: Seconds after ``T0`` it was requested.
        **overrides: Field overrides.

    Returns:
        Operation: The record.

    Raises:
        None
    """
    fields: dict[str, Any] = {
        "id": new_id(OperationId),
        "kind": OperationKind.REDISCOVERY,
        "device_id": device_id,
        "state": OperationState.QUEUED,
        "requested_by": "alice",
        "requested_at": T0 + timedelta(seconds=at_s),
    }
    fields.update(overrides)
    return Operation(**fields)


def _failed(device_id: DeviceId, collector_id: CollectorId, n: int) -> Operation:
    """Build a rediscovery requested and claimed at ``T0 + n`` s that failed a second later.

    Args:
        device_id: The device.
        collector_id: The collector that ran it.
        n: Seconds after ``T0``.

    Returns:
        Operation: The failed operation.

    Raises:
        None
    """
    at = T0 + timedelta(seconds=n)
    return operation(device_id, n).claimed(collector_id, at).failed(at + timedelta(seconds=1), "no")


def _seed(factory: MetadataUnitOfWorkFactory) -> tuple[Device, Device, Collector]:
    """Commit two devices and a collector.

    Args:
        factory: Opens units of work.

    Returns:
        tuple[Device, Device, Collector]: ``core1``, ``core2``, and ``east``.

    Raises:
        None
    """
    one, two, east = s.device("core1"), s.device("core2", poll_address="192.0.2.2"), s.collector()
    with factory() as uow:
        uow.devices.add(one)
        uow.devices.add(two)
        uow.collectors.add(east)
        uow.commit()
    return one, two, east


class OperationsContract:
    """Behaviour every metadata adapter shares for admin operations."""

    def test_api011_round_trip_and_one_active_per_device(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """API-011, E.32: stored as given; a device has at most one active rediscovery.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        one, two, _ = _seed(metadata_uow_factory)
        first = operation(one.id)
        with metadata_uow_factory() as uow:
            uow.operations.add(first)
            uow.operations.add(operation(two.id, 1))
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.operations.get(first.id) == first
            assert uow.operations.get(new_id(OperationId)) is None
            assert uow.operations.active_for_device(one.id, OperationKind.REDISCOVERY) == first
            assert uow.operations.count_active() == 2
            with pytest.raises(DuplicateError):
                uow.operations.add(operation(one.id, 5))
            with pytest.raises(DuplicateError):
                uow.operations.add(operation(two.id, 5, id=first.id))
            with pytest.raises(NotFoundError):
                uow.operations.add(operation(new_id(DeviceId)))

    def test_disc_t03_claim_is_compare_and_set(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DISC-T03, E.32: of two claims on one queued operation, exactly one commits.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        one, _, east = _seed(metadata_uow_factory)
        queued = operation(one.id)
        with metadata_uow_factory() as uow:
            uow.operations.add(queued)
            uow.commit()
        claim = queued.claimed(east.id, T0 + timedelta(seconds=2))
        with metadata_uow_factory() as uow:
            uow.operations.update(claim)
            uow.commit()

        def claim_again() -> None:
            """Claim the operation as read before the first claim, as a second collector would.

            Args:
                None

            Returns:
                None

            Raises:
                ConflictError: From the update or the commit, depending on the adapter.
            """
            with metadata_uow_factory() as uow:
                uow.operations.update(queued.claimed(east.id, T0 + timedelta(seconds=3)))
                uow.commit()

        with pytest.raises(ConflictError):
            claim_again()
        with metadata_uow_factory() as uow:
            assert uow.operations.get(queued.id) == claim
            assert uow.operations.running_on(east.id, limit=10) == (claim,)
            assert uow.operations.queued(limit=10) == ()
            with pytest.raises(NotFoundError):
                uow.operations.update(operation(one.id, version=2))

    def test_api_t06_finished_is_final_and_frees_the_device(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """API-T06: a result is stored exactly; a finished operation never changes again.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        one, _, east = _seed(metadata_uow_factory)
        claim = operation(one.id).claimed(east.id, T0)
        done = claim.succeeded(T0 + timedelta(seconds=3), RESULT)
        with metadata_uow_factory() as uow:
            uow.operations.add(claim)
            uow.operations.update(done)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.operations.get(done.id) == done
            assert uow.operations.active_for_device(one.id, OperationKind.REDISCOVERY) is None
            assert uow.operations.count_active() == 0
            stale = replace(done, version=done.version + 1)
            with pytest.raises(ValidationError):
                uow.operations.update(stale)
            uow.operations.add(operation(one.id, 9))  # the device is free again
            uow.commit()

    def test_api005_lists_in_both_orders_with_filters(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """API-005: newest-first pages with filters; the pickup list is oldest first.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        one, two, east = _seed(metadata_uow_factory)
        ops = [operation(one.id, 0).claimed(east.id, T0).failed(T0 + timedelta(seconds=1), "no"),
               operation(two.id, 10), operation(one.id, 20)]  # fmt: skip
        with metadata_uow_factory() as uow:
            for op in ops:
                uow.operations.add(op)
            uow.commit()
        with metadata_uow_factory() as uow:
            first = uow.operations.list_page(PageRequest(limit=2))
            assert [o.id for o in first.items] == [ops[2].id, ops[1].id]
            assert first.total == 3
            assert first.next_cursor is not None
            rest = uow.operations.list_page(PageRequest(limit=2, cursor=first.next_cursor))
            assert [o.id for o in rest.items] == [ops[0].id]
            assert rest.next_cursor is None
            mine = uow.operations.list_page(PageRequest(), OperationFilter(device_id=one.id))
            assert [o.id for o in mine.items] == [ops[2].id, ops[0].id]
            failed = OperationFilter(states=frozenset({OperationState.FAILED}))
            assert [o.id for o in uow.operations.list_page(PageRequest(), failed).items] == [
                ops[0].id
            ]
            assert [o.id for o in uow.operations.queued(limit=5)] == [ops[1].id, ops[2].id]
            assert len(uow.operations.queued(limit=1)) == 1

    def test_e32_purges_are_bounded_and_spare_active_operations(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """E.32: old or surplus finished rows go, oldest first, at most ``limit`` per call.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        one, two, east = _seed(metadata_uow_factory)
        finished = [_failed(one.id, east.id, n) for n in range(5)]
        active = operation(two.id, 50)
        with metadata_uow_factory() as uow:
            for op in (*finished, active):
                uow.operations.add(op)
            before = uow.change_counter()
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.change_counter() == before  # an operational log, not inventory
            cutoff = T0 + timedelta(seconds=3)  # rows that ended at +1 s and +2 s are old
            assert uow.operations.purge_finished(before=cutoff, keep=100, limit=1) == 1
            assert uow.operations.purge_finished(before=cutoff, keep=100, limit=10) == 1
            assert uow.operations.purge_finished(before=T0, keep=2, limit=10) == 1
            uow.commit()
        with metadata_uow_factory() as uow:
            left = uow.operations.list_page(PageRequest()).items
            assert [o.id for o in left] == [active.id, finished[4].id, finished[3].id]
            assert uow.operations.purge_finished(before=T0, keep=0, limit=0) == 0
