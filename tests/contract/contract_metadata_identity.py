"""Port extension contract: identity reviews, ownership transfer, change counter.

These cover what the M3 inventory loader added to the metadata ports
(blueprint E.26): ``IdentityReviewRepository`` (IFID-004, IFID-007),
``InterfaceRepository.transfer_owner`` (META-008, E.19 "Claiming interfaces
from files"), and ``MetadataUnitOfWork.change_counter`` (6.2). Subclass
``MetadataIdentityContract`` in an adapter's test module; the adapter's
``conftest.py`` supplies ``metadata_uow_factory``.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k MetadataIdentity
"""

from dataclasses import replace
from typing import Any

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import DeviceId, InterfaceId, ReviewId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import API_MANAGED, Device
from vakr.domain.paging import PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.ports.metadata import MetadataUnitOfWorkFactory, ReviewFilter


def review(
    device_id: DeviceId, candidates: tuple[InterfaceId, ...], **overrides: Any
) -> IdentityReview:
    """Build an open review of ``ifIndex`` 5 on a device.

    Args:
        device_id: The device.
        candidates: Candidate interfaces.
        **overrides: Field values to replace.

    Returns:
        IdentityReview: The review.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(ReviewId), "device_id": device_id, "opened_at": s.T0,
        "outcome": ReconcileOutcome.PROBABLE, "if_index": 5, "if_name": "Te0/0/5",
        "if_descr": "TenGigabitEthernet0/0/5", "if_alias": "uplink",
        "candidates": tuple(sorted(candidates, key=str)), "evidence": ("same ifIndex",),
        "mac": "00:00:5e:00:53:01", "addresses": ("192.0.2.9/31",),
    }  # fmt: skip
    return IdentityReview(**base | overrides)


def _seed(factory: MetadataUnitOfWorkFactory) -> tuple[Device, Interface, Interface]:
    """Commit one device with two interfaces.

    Args:
        factory: Opens units of work.

    Returns:
        tuple[Device, Interface, Interface]: The records.

    Raises:
        None
    """
    dev = s.device()
    one = s.interface(dev.id, "Gi0/0/5", if_index=5)
    two = s.interface(dev.id, "Gi0/0/6", if_index=6)
    with factory() as uow:
        uow.devices.add(dev)
        uow.interfaces.add(one)
        uow.interfaces.add(two)
        uow.commit()
    return dev, one, two


class MetadataIdentityContract:
    """Behaviour every metadata adapter shares for the M3 port extension."""

    def test_ifid007_reviews_round_trip_and_list(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """IFID-T05/T06: an open review keeps the observed row and its candidates; lists page.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, one, two = _seed(metadata_uow_factory)
        first = review(dev.id, (one.id,))
        second = review(dev.id, (one.id, two.id), outcome=ReconcileOutcome.AMBIGUOUS,
                        opened_at=s.T0 + s.MINUTE)  # fmt: skip
        with metadata_uow_factory() as uow:
            uow.reviews.add(second)
            uow.reviews.add(first)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.reviews.get(first.id) == first
            assert uow.reviews.get(new_id(ReviewId)) is None
            page = uow.reviews.list_page(PageRequest(limit=1))
            rest = uow.reviews.list_page(PageRequest(cursor=page.next_cursor))
            assert [*page.items, *rest.items] == [first, second]
            assert page.total == 2
            mine = ReviewFilter(device_id=dev.id, state=ReviewState.OPEN)
            assert uow.reviews.list_page(PageRequest(), mine).total == 2
            other = ReviewFilter(device_id=s.device().id)
            assert uow.reviews.list_page(PageRequest(), other).total == 0
            closed = ReviewFilter(state=ReviewState.MAPPED)
            assert uow.reviews.list_page(PageRequest(), closed).total == 0

    def test_ifid007_review_add_rules(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """IFID-007: a new review is open, unique, and names existing records on its device.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, one, _ = _seed(metadata_uow_factory)
        other = s.device("edge2")
        stray = s.interface(other.id)
        good = review(dev.id, (one.id,))
        with metadata_uow_factory() as uow:
            uow.devices.add(other)
            uow.interfaces.add(stray)
            uow.reviews.add(good)
            with pytest.raises(DuplicateError):
                uow.reviews.add(good)
            with pytest.raises(NotFoundError):
                uow.reviews.add(review(s.device().id, (one.id,)))
            with pytest.raises(NotFoundError):
                uow.reviews.add(review(dev.id, (new_id(InterfaceId),)))
            with pytest.raises(ValidationError):
                uow.reviews.add(review(dev.id, (stray.id,)))
            with pytest.raises(ValidationError):
                uow.reviews.add(review(dev.id, (one.id,), version=2))

    def test_ifid007_review_closes_once(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """IFID-T07: closing checks existence, version, and state, and is final.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, one, _ = _seed(metadata_uow_factory)
        open_review = review(dev.id, (one.id,))
        later = s.T0 + s.MINUTE
        with metadata_uow_factory() as uow:
            uow.reviews.add(open_review)
            uow.commit()
        with metadata_uow_factory() as uow:
            with pytest.raises(NotFoundError):
                uow.reviews.close(new_id(ReviewId), ReviewState.SUPERSEDED, expected_version=1,
                                  at=later)  # fmt: skip
            with pytest.raises(NotFoundError):
                uow.reviews.close(open_review.id, ReviewState.MAPPED, expected_version=1, at=later,
                                  resolution=new_id(InterfaceId), actor="admin")  # fmt: skip
            with pytest.raises(ConflictError):
                uow.reviews.close(open_review.id, ReviewState.SUPERSEDED, expected_version=3,
                                  at=later)  # fmt: skip
            with pytest.raises(ValidationError):
                uow.reviews.close(open_review.id, ReviewState.MAPPED, expected_version=1, at=later)
            done = uow.reviews.close(open_review.id, ReviewState.MAPPED, expected_version=1,
                                     at=later, resolution=one.id, actor="admin")  # fmt: skip
            uow.commit()
        assert (done.state, done.version, done.resolution, done.actor) == (
            ReviewState.MAPPED, 2, one.id, "admin")  # fmt: skip
        with metadata_uow_factory() as uow:
            assert uow.reviews.get(open_review.id) == done
            with pytest.raises(ValidationError):
                uow.reviews.close(open_review.id, ReviewState.SUPERSEDED, expected_version=2,
                                  at=later)  # fmt: skip

    def test_meta008_transfer_owner(self, metadata_uow_factory: MetadataUnitOfWorkFactory) -> None:
        """META-008, E.19: the ownership transfer changes only the owner and is version-checked.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, one, _ = _seed(metadata_uow_factory)
        later = s.T0 + s.MINUTE
        with metadata_uow_factory() as uow:
            with pytest.raises(NotFoundError):
                uow.interfaces.transfer_owner(new_id(InterfaceId), s.FILE_OWNER,
                                              expected_version=1, at=later)  # fmt: skip
            with pytest.raises(ConflictError):
                uow.interfaces.transfer_owner(one.id, s.FILE_OWNER, expected_version=4, at=later)
            moved = uow.interfaces.transfer_owner(one.id, s.FILE_OWNER, expected_version=1,
                                                  at=later)  # fmt: skip
            uow.commit()
        assert moved == replace(one, managed_by=s.FILE_OWNER, updated_at=later, version=2)
        with metadata_uow_factory() as uow:
            assert uow.interfaces.get(one.id) == moved
            back = uow.interfaces.transfer_owner(one.id, API_MANAGED, expected_version=2, at=later)
            assert back.managed_by == API_MANAGED

    def test_change_counter_grows_on_every_change(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """6.2: adds and updates raise the counter; reads and rollbacks do not.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        with metadata_uow_factory() as uow:
            assert uow.change_counter() == 0
        dev, one, _ = _seed(metadata_uow_factory)
        with metadata_uow_factory() as uow:
            seeded = uow.change_counter()
            uow.interfaces.update_observed(replace(one, if_alias="WAN"))
            assert uow.change_counter() == seeded + 1
        with metadata_uow_factory() as uow:
            assert uow.change_counter() == seeded == 6
            uow.reviews.add(review(dev.id, (one.id,)))
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.change_counter() == seeded + 2
