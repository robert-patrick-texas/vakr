"""Metadata transaction and ownership rules: DB-002, DB-003, META-008, META-009, IFID-005, SEC-018.

Subclass ``MetadataRulesContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``metadata_uow_factory``. At most one
unit of work holds uncommitted writes at a time, so single-writer backends
such as SQLite can run every test; one test opens a reader beside an
uncommitted writer, which a WAL-mode backend must allow (DB-008).

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k MetadataRules
"""

from dataclasses import replace

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import AdminState, InterfaceLifecycle
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OwnershipError,
    ValidationError,
)
from vakr.domain.ids import InterfaceId, SiteId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import API_MANAGED, Device, ManagedBy
from vakr.domain.paging import PageRequest
from vakr.ports.metadata import (
    AuditFilter,
    DeviceFilter,
    MetadataUnitOfWork,
    MetadataUnitOfWorkFactory,
)


def _seed(
    factory: MetadataUnitOfWorkFactory, owner: ManagedBy = API_MANAGED
) -> tuple[Device, Interface]:
    """Commit one device and one interface with the given owner.

    Args:
        factory: Opens units of work.
        owner: Owner of both records.

    Returns:
        tuple[Device, Interface]: The committed records.

    Raises:
        None
    """
    dev = s.device(managed_by=owner)
    iface = s.interface(dev.id, managed_by=owner)
    with factory() as uow:
        uow.devices.add(dev)
        uow.interfaces.add(iface)
        uow.commit()
    return dev, iface


class _BoomError(Exception):
    """Raised inside a unit of work to prove rollback."""


def _change_then_fail(factory: MetadataUnitOfWorkFactory, dev: Device, iface: Interface) -> SiteId:
    """Make four related changes in one unit of work, then raise before commit.

    Args:
        factory: Opens units of work.
        dev: A committed device.
        iface: A committed interface.

    Returns:
        SiteId: Never returns; the type names the site that was staged.

    Raises:
        _BoomError: Always, after staging the changes.
    """
    site = s.site()
    with factory() as uow:
        uow.sites.add(site)
        uow.devices.update(replace(dev, site_id=site.id), writer=API_MANAGED)
        uow.audit.append(s.audit(s.T0))
        uow.interfaces.append_identity_history(s.history(iface, s.T0))
        raise _BoomError(site.id)


def _update_and_commit(uow: MetadataUnitOfWork, dev: Device) -> None:
    """Update a device and commit; a conflict may surface from either call.

    Args:
        uow: An open unit of work.
        dev: The edited device.

    Returns:
        None

    Raises:
        ConflictError: If ``dev.version`` is stale.
    """
    uow.devices.update(dev, writer=API_MANAGED)
    uow.commit()


class MetadataRulesContract:
    """Transaction, concurrency, ownership, and history rules for any metadata backend."""

    def test_db002_failed_unit_of_work_commits_nothing(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-002: an exception or a missing commit leaves no partial changes.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface = _seed(metadata_uow_factory)
        with pytest.raises(_BoomError) as boom:
            _change_then_fail(metadata_uow_factory, dev, iface)
        (site_id,) = boom.value.args
        with metadata_uow_factory() as uow:
            uow.tags.add(s.tag())  # never committed
        with metadata_uow_factory() as uow:
            uow.tags.add(s.tag("other"))
            uow.rollback()
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.sites.get(site_id) is None
            assert uow.devices.get(dev.id) == dev
            assert uow.audit.list_page(PageRequest()).total == 0
            assert uow.interfaces.identity_history(iface.id, PageRequest()).total == 0
            assert uow.tags.list_page(PageRequest()).total == 0

    def test_db002_uncommitted_changes_are_private(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """5.2 rule 4: a unit of work sees its own changes; others see them after commit.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev = s.device()
        with metadata_uow_factory() as writer:
            writer.devices.add(dev)
            assert writer.devices.get(dev.id) == dev
            with metadata_uow_factory() as reader:
                assert reader.devices.get(dev.id) is None
            writer.commit()
            writer.devices.update(replace(dev, comment="second"), writer=API_MANAGED)
            writer.commit()
        with metadata_uow_factory() as reader:
            stored = reader.devices.get(dev.id)
        assert stored is not None
        assert stored.comment == "second"
        assert stored.version == 2

    def test_db003_stale_versions_conflict(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-003: two edits of the same version: the second raises ``ConflictError``.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface = _seed(metadata_uow_factory)
        with metadata_uow_factory() as alice_reads:
            alice_copy = alice_reads.devices.get(dev.id)
        with metadata_uow_factory() as bob_reads:
            bob_copy = bob_reads.devices.get(dev.id)
        assert alice_copy is not None
        assert bob_copy is not None
        with metadata_uow_factory() as bob:
            bob.devices.update(replace(bob_copy, comment="bob"), writer=API_MANAGED)
            bob.commit()
        with metadata_uow_factory() as alice:
            with pytest.raises(ConflictError) as caught:
                _update_and_commit(alice, replace(alice_copy, comment="alice"))
            assert caught.value.expected_version == 1
            assert caught.value.actual_version == 2
        with metadata_uow_factory() as uow:
            with pytest.raises(ConflictError):
                uow.devices.set_admin_state(dev.id, AdminState.DISABLED, expected_version=1,
                                            writer=API_MANAGED, at=s.T0)  # fmt: skip
            with pytest.raises(ConflictError):
                uow.interfaces.set_lifecycle(iface.id, InterfaceLifecycle.STALE, expected_version=9,
                                             at=s.T0, inactive_since=s.T0)  # fmt: skip
            with pytest.raises(ConflictError):
                uow.interfaces.update_observed(replace(iface, if_index=2, version=5))
            stored = uow.devices.get(dev.id)
        assert stored is not None
        assert stored.comment == "bob"

    def test_meta008_ownership_is_enforced(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """META-008, 11.2: the API cannot edit file records; a file cannot overwrite API records.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        file_dev, _ = _seed(metadata_uow_factory, s.FILE_OWNER)
        api_dev = s.device("api-made")
        other_file = ManagedBy.file("/etc/vakr/inventory/other.toml")
        with metadata_uow_factory() as uow:
            uow.devices.add(api_dev)
            with pytest.raises(OwnershipError) as caught:
                uow.devices.update(replace(file_dev, comment="x"), writer=API_MANAGED)
            assert "devices.toml" in str(caught.value)
            with pytest.raises(OwnershipError):
                uow.devices.update(replace(file_dev, comment="x"), writer=other_file)
            with pytest.raises(OwnershipError):
                uow.devices.set_admin_state(file_dev.id, AdminState.MAINTENANCE, expected_version=1,
                                            writer=API_MANAGED, at=s.T0)  # fmt: skip
            with pytest.raises(OwnershipError):
                uow.devices.update(replace(api_dev, comment="x"), writer=s.FILE_OWNER)
            with pytest.raises(OwnershipError):  # ownership is checked before the version
                uow.devices.update(replace(file_dev, version=7), writer=API_MANAGED)
            stored = uow.devices.update(replace(file_dev, comment="ok"), writer=s.FILE_OWNER)
            assert stored.version == 2
            with pytest.raises(ValidationError):
                uow.devices.update(replace(stored, managed_by=API_MANAGED), writer=s.FILE_OWNER)
            with pytest.raises(ValidationError):
                uow.devices.update(replace(stored, created_at=s.T0 - s.MINUTE), writer=s.FILE_OWNER)

    def test_meta008_interface_write_paths(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """META-008, IFID-001: discovery writes observed fields; owners write owned fields only.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, iface = _seed(metadata_uow_factory, s.FILE_OWNER)
        wan = s.tag()
        with metadata_uow_factory() as uow:
            uow.tags.add(wan)
            seen = uow.interfaces.update_observed(
                replace(iface, if_index=42, if_alias="WAN", description_tags=frozenset({wan.id}))
            )
            assert seen.version == 2
            assert seen.if_index == 42
            with pytest.raises(OwnershipError):
                uow.interfaces.update(replace(seen, comment="x"), writer=API_MANAGED)
            with pytest.raises(ValidationError):
                uow.interfaces.update(replace(seen, if_index=1), writer=s.FILE_OWNER)
            with pytest.raises(ValidationError):
                uow.interfaces.update_observed(replace(seen, comment="x"))
            with pytest.raises(ValidationError):
                uow.interfaces.update_observed(replace(seen, lifecycle=InterfaceLifecycle.STALE,
                                                       inactive_since=s.T0))  # fmt: skip
            owned = uow.interfaces.update(replace(seen, comment="uplink", tags=frozenset({wan.id})),
                                          writer=s.FILE_OWNER)  # fmt: skip
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.interfaces.get(iface.id) == owned
            assert owned.if_index == 42
            assert owned.comment == "uplink"
            assert owned.version == 3

    def test_meta009_retiring_keeps_records_and_history(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """META-009, LIFE-002: retiring is a state change; records and history remain.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface = _seed(metadata_uow_factory)
        later = s.T0 + s.MINUTE
        with metadata_uow_factory() as uow:
            uow.interfaces.append_identity_history(s.history(iface, s.T0))
            retired = uow.devices.set_admin_state(dev.id, AdminState.RETIRED, expected_version=1,
                                                  writer=API_MANAGED, at=later)  # fmt: skip
            gone = uow.interfaces.set_lifecycle(iface.id, InterfaceLifecycle.RETIRED,
                                                expected_version=1, at=later,
                                                inactive_since=None)  # fmt: skip
            uow.commit()
        assert (retired.version, retired.updated_at, gone.version) == (2, later, 2)
        with metadata_uow_factory() as uow:
            assert uow.devices.get(dev.id) == retired
            assert uow.interfaces.get(iface.id) == gone
            assert uow.devices.list_page(PageRequest()).total == 0
            assert (
                uow.devices.list_page(PageRequest(), DeviceFilter(include_retired=True)).total == 1
            )
            assert uow.interfaces.identity_history(iface.id, PageRequest()).total == 1
            with pytest.raises(NotFoundError):
                uow.devices.set_admin_state(s.device().id, AdminState.ENABLED, expected_version=1,
                                            writer=API_MANAGED, at=later)  # fmt: skip

    def test_ifid005_identity_history(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """IFID-005: history keeps every decision, oldest first, with evidence and scores.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        _, iface = _seed(metadata_uow_factory)
        entries = [s.history(iface, s.T0 + m * s.MINUTE) for m in (2, 0, 1)]
        with metadata_uow_factory() as uow:
            for entry in entries:
                uow.interfaces.append_identity_history(entry)
            with pytest.raises(DuplicateError):
                uow.interfaces.append_identity_history(entries[0])
            stray = s.interface(iface.device_id)  # never added
            with pytest.raises(NotFoundError):
                uow.interfaces.append_identity_history(s.history(stray, s.T0))
            with pytest.raises(ValidationError):
                uow.interfaces.append_identity_history(
                    s.history(iface, s.T0, device_id=s.device().id)
                )
            uow.commit()
        with metadata_uow_factory() as uow:
            first = uow.interfaces.identity_history(iface.id, PageRequest(limit=2))
            rest = uow.interfaces.identity_history(iface.id, PageRequest(cursor=first.next_cursor))
            assert uow.interfaces.identity_history(new_id(InterfaceId), PageRequest()).total == 0
        assert [*first.items, *rest.items] == sorted(entries, key=lambda e: e.decided_at)
        assert first.total == 3
        assert rest.next_cursor is None

    def test_sec018_audit_events(self, metadata_uow_factory: MetadataUnitOfWorkFactory) -> None:
        """SEC-018: audit is append-only, newest first, and filterable.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev = s.device()
        events = [
            s.audit(s.T0, actor="alice", object_id=dev.id),
            s.audit(s.T0 + 2 * s.MINUTE, actor="bob", request_id="req-1"),
            s.audit(s.T0 + s.MINUTE, actor="alice"),
        ]
        with metadata_uow_factory() as uow:
            for event in events:
                uow.audit.append(event)
            with pytest.raises(DuplicateError):
                uow.audit.append(events[0])
            uow.commit()
        with metadata_uow_factory() as uow:
            first = uow.audit.list_page(PageRequest(limit=2))
            rest = uow.audit.list_page(PageRequest(limit=2, cursor=first.next_cursor))
            by_alice = uow.audit.list_page(PageRequest(), AuditFilter(actor="alice"))
            by_object = uow.audit.list_page(PageRequest(), AuditFilter(object_id=dev.id))
        assert [*first.items, *rest.items] == [events[1], events[2], events[0]]
        assert [e.actor for e in by_alice.items] == ["alice", "alice"]
        assert by_object.items == (events[0],)
        assert by_object.total == 1
