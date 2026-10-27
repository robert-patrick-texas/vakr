"""Interface lifecycle storage: the stale clock's start moves only with lifecycle (LIFE-003).

``Interface.inactive_since`` is the start of the unbroken INACTIVE/STALE
period that ``lifecycle.stale_after`` counts from. Every adapter must store
it exactly, change it only through ``set_lifecycle``, refuse a value that
does not fit the state, and keep it through operator edits, discovery
refreshes, and ownership transfers, which all move ``updated_at``.
Retiring clears it; restoring starts a new period at the restore (E.6).

Subclass ``MetadataLifecycleContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``metadata_uow_factory``.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.logic.lifecycle; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k MetadataLifecycle
"""

from dataclasses import replace
from datetime import timedelta, timezone
from typing import Any

import pytest

from tests.contract import contract_samples as s
from vakr.discovery.reconcile.records import current_record
from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.errors import ValidationError
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import API_MANAGED, Device
from vakr.domain.paging import PageRequest
from vakr.logic.lifecycle import restore, retire
from vakr.ports.metadata import InterfaceFilter, MetadataUnitOfWorkFactory

DOWN_AT = s.T0 + timedelta(hours=1)
"""When the interface went inactive; deliberately not any record's ``updated_at``."""


def _seed(factory: MetadataUnitOfWorkFactory, **overrides: Any) -> tuple[Device, Interface]:
    """Commit one device and one interface.

    Args:
        factory: Opens units of work.
        **overrides: Interface fields.

    Returns:
        tuple[Device, Interface]: The committed records.

    Raises:
        None
    """
    dev = s.device()
    iface = s.interface(dev.id, **overrides)
    with factory() as uow:
        uow.devices.add(dev)
        uow.interfaces.add(iface)
        uow.commit()
    return dev, iface


def _stored(factory: MetadataUnitOfWorkFactory, iface: Interface) -> Interface:
    """Read an interface back in a new unit of work, by ID and through the list.

    Args:
        factory: Opens units of work.
        iface: The interface.

    Returns:
        Interface: The stored record; both reads agree.

    Raises:
        None
    """
    with factory() as uow:
        got = uow.interfaces.get(iface.id)
        listed = uow.interfaces.list_page(PageRequest(), InterfaceFilter(include_retired=True))
    assert got is not None
    assert listed.items == (got,)
    return got


class MetadataLifecycleContract:
    """LIFE-003 and E.6 storage rules every metadata adapter must satisfy."""

    def test_life003_set_lifecycle_stores_sets_keeps_and_clears_the_start(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """LIFE-003: set on entering INACTIVE, kept into STALE, cleared on ACTIVE; exact times.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, iface = _seed(metadata_uow_factory)
        assert _stored(metadata_uow_factory, iface).inactive_since is None
        steps = (
            (InterfaceLifecycle.INACTIVE, DOWN_AT + s.MINUTE, DOWN_AT),
            (InterfaceLifecycle.STALE, DOWN_AT + timedelta(days=10), DOWN_AT),
            (InterfaceLifecycle.ACTIVE, DOWN_AT + timedelta(days=11), None),
        )
        for version, (state, at, start) in enumerate(steps, start=1):
            with metadata_uow_factory() as uow:
                written = uow.interfaces.set_lifecycle(iface.id, state, expected_version=version,
                                                       at=at, inactive_since=start)  # fmt: skip
                uow.commit()
            assert (written.lifecycle, written.inactive_since, written.updated_at) == (
                state, start, at)  # fmt: skip
            assert _stored(metadata_uow_factory, iface) == written

    def test_life003_an_inactive_interface_round_trips_its_start(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """LIFE-003: an interface added inactive keeps a start that differs from ``updated_at``.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        edited_at = DOWN_AT + timedelta(days=12)
        _, iface = _seed(metadata_uow_factory, lifecycle=InterfaceLifecycle.STALE,
                         inactive_since=DOWN_AT, updated_at=edited_at)  # fmt: skip
        stored = _stored(metadata_uow_factory, iface)
        assert stored == iface
        assert stored.inactive_since == DOWN_AT

    def test_life003_only_set_lifecycle_moves_the_start(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """LIFE-003: edits, discovery refreshes, and transfers keep it; edits cannot change it.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, iface = _seed(metadata_uow_factory, lifecycle=InterfaceLifecycle.INACTIVE,
                         inactive_since=DOWN_AT)  # fmt: skip
        later = DOWN_AT + timedelta(days=5)
        with metadata_uow_factory() as uow:
            for write in (lambda i: uow.interfaces.update(i, writer=API_MANAGED),
                          uow.interfaces.update_observed):  # fmt: skip
                with pytest.raises(ValidationError, match="inactive_since"):
                    write(replace(iface, inactive_since=later, updated_at=later))
            edited = uow.interfaces.update(replace(iface, comment="uplink", updated_at=later),
                                           writer=API_MANAGED)  # fmt: skip
            seen = uow.interfaces.update_observed(replace(edited, if_alias="WAN", updated_at=later))
            moved = uow.interfaces.transfer_owner(
                iface.id, s.FILE_OWNER, expected_version=seen.version, at=later
            )
            uow.commit()
        stored = _stored(metadata_uow_factory, iface)
        assert (stored.comment, stored.if_alias, stored.managed_by) == ("uplink", "WAN",
                                                                        s.FILE_OWNER)  # fmt: skip
        assert (stored.version, stored.updated_at, stored.inactive_since) == (4, later, DOWN_AT)
        assert stored == moved

    def test_life003_a_start_that_does_not_fit_the_state_is_refused(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """LIFE-003: INACTIVE/STALE need a UTC start; ACTIVE and RETIRED have none; nothing written.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, iface = _seed(metadata_uow_factory)
        local = DOWN_AT.astimezone(timezone(timedelta(hours=2)))
        wrong = (
            (InterfaceLifecycle.INACTIVE, None),
            (InterfaceLifecycle.STALE, None),
            (InterfaceLifecycle.ACTIVE, DOWN_AT),
            (InterfaceLifecycle.RETIRED, DOWN_AT),
            (InterfaceLifecycle.INACTIVE, local),
        )
        with metadata_uow_factory() as uow:
            for state, start in wrong:
                with pytest.raises(ValidationError, match="inactive_since"):
                    uow.interfaces.set_lifecycle(iface.id, state, expected_version=1, at=DOWN_AT,
                                                 inactive_since=start)  # fmt: skip
            uow.commit()
        assert _stored(metadata_uow_factory, iface) == iface
        with pytest.raises(ValidationError, match="inactive_since"):
            s.interface(iface.device_id, lifecycle=InterfaceLifecycle.STALE, inactive_since=None)

    def test_e6_retiring_clears_the_start_and_restoring_begins_a_new_one(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """E.6 decision: RETIRED has no stale clock; a restore starts one at the restore time.

        The records come from ``vakr.logic.lifecycle``'s own ``retire`` and
        ``restore``, as an administrator action would write them.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, iface = _seed(metadata_uow_factory, lifecycle=InterfaceLifecycle.STALE,
                         inactive_since=DOWN_AT)  # fmt: skip
        retired_at, restored_at = DOWN_AT + timedelta(days=20), DOWN_AT + timedelta(days=30)
        current = iface
        for action, at in ((retire, retired_at), (restore, restored_at)):
            record = action(current_record(current), at, "cli:admin").record
            with metadata_uow_factory() as uow:
                current = uow.interfaces.set_lifecycle(
                    iface.id, record.state, expected_version=current.version, at=at,
                    inactive_since=record.inactive_since,
                )  # fmt: skip
                uow.commit()
            if action is retire:
                assert (current.lifecycle, current.inactive_since) == (
                    InterfaceLifecycle.RETIRED, None)  # fmt: skip
        assert (current.lifecycle, current.inactive_since) == (
            InterfaceLifecycle.INACTIVE, restored_at)  # fmt: skip
        assert _stored(metadata_uow_factory, iface) == current
