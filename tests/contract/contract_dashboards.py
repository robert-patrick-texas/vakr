"""Contract: the custom-dashboard repository (UI-004, DASH-007, DB-003, port extension E.31).

Subclass ``DashboardsContract`` in an adapter's test module; the adapter's
``conftest.py`` supplies ``metadata_uow_factory``. Covers the round trip of
every widget kind, name uniqueness, the optimistic version (DB-003), fixed
fields, deletion, bounded ordered lists, and that none of it moves the
collection change counter.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.domain; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k Dashboards
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from tests.contract.contract_samples import T0
from vakr.domain.dashboard_widgets import (
    DeviceCriteria,
    DeviceSelection,
    InterfaceCriteria,
    InterfaceSelection,
    PinnedInterfaces,
    RankingKind,
    RankingWidget,
)
from vakr.domain.dashboards import Dashboard, Visibility
from vakr.domain.enums import InterestClass
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import DashboardId, GroupId, InterfaceId, SiteId, TagId, new_id
from vakr.domain.ownership import API_MANAGED, ManagedBy
from vakr.ports.metadata import MetadataUnitOfWorkFactory


def dashboard(name: str = "NOC core", **overrides: Any) -> Dashboard:
    """Return a valid dashboard with one widget of every kind.

    Args:
        name: Its name.
        **overrides: Fields to replace.

    Returns:
        Dashboard: The record, version 1.

    Raises:
        None
    """
    widgets = (
        PinnedInterfaces("Uplinks", (new_id(InterfaceId), new_id(InterfaceId)), graphs=False),
        InterfaceSelection("WAN at Dallas", InterfaceCriteria(site_id=new_id(SiteId),
                           provider="Lumen", wan_only=True, interest=InterestClass.PRIMARY), 40),
        DeviceSelection("Core group", DeviceCriteria(group_id=new_id(GroupId),
                                                     tag_id=new_id(TagId))),
        RankingWidget("Busiest WAN", RankingKind.ERRORS, InterfaceCriteria(wan_only=True),
                      "6h", 5),
    )  # fmt: skip
    values: dict[str, Any] = {
        "id": new_id(DashboardId), "name": name, "description": "Core and WAN at a glance",
        "widgets": widgets, "refresh_s": 60, "managed_by": API_MANAGED,
        "visibility": Visibility.PRIVATE, "created_by": "alice", "created_at": T0,
        "updated_by": "alice", "updated_at": T0,
    }  # fmt: skip
    values.update(overrides)
    return Dashboard(**values)


class DashboardsContract:
    """Behaviour every metadata adapter shares for custom dashboards."""

    def test_ui004_dashboards_round_trip_every_widget_kind(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """UI-004, DASH-007: every widget kind and field survives a write and a read.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        noc = dashboard()
        with metadata_uow_factory() as uow:
            before = uow.change_counter()
            assert uow.dashboards.add(noc) == noc
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.dashboards.get(noc.id) == noc
            assert uow.dashboards.get_by_name("NOC core") == noc
            assert uow.dashboards.get_by_name("noc core") is None  # names compare exactly
            assert uow.dashboards.get(new_id(DashboardId)) is None
            assert uow.dashboards.count() == 1
            assert uow.change_counter() == before  # the collector never reloads for a dashboard

    def test_names_are_unique_and_new_records_start_at_version_1(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """UI-004: a taken ID or name is a duplicate; a new record has version 1.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        noc = dashboard()
        with metadata_uow_factory() as uow:
            uow.dashboards.add(noc)
            uow.commit()
        with metadata_uow_factory() as uow:
            with pytest.raises(DuplicateError):
                uow.dashboards.add(dashboard())
            with pytest.raises(DuplicateError):
                uow.dashboards.add(dashboard("Other", id=noc.id))
            with pytest.raises(ValidationError):
                uow.dashboards.add(dashboard("Third", version=2))

    def test_db003_updates_check_the_version_and_the_fixed_fields(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-003: a change based on an old version conflicts; fixed fields never change.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        noc, wan = dashboard(), dashboard("WAN")
        with metadata_uow_factory() as uow:
            uow.dashboards.add(noc)
            uow.dashboards.add(wan)
            uow.commit()
        later = T0 + timedelta(hours=1)
        renamed = replace(noc, name="NOC", widgets=noc.widgets[:1], updated_by="bob",
                          updated_at=later)  # fmt: skip
        with metadata_uow_factory() as uow:
            stored = uow.dashboards.update(renamed)
            uow.commit()
        assert stored == replace(renamed, version=2)
        with metadata_uow_factory() as uow:
            assert uow.dashboards.get(noc.id) == stored
            with pytest.raises(ConflictError):
                uow.dashboards.update(replace(renamed, description="stale"))
            with pytest.raises(DuplicateError):
                uow.dashboards.update(replace(stored, name="WAN"))
            with pytest.raises(NotFoundError):
                uow.dashboards.update(dashboard("Ghost"))
            file_owned = ManagedBy.file("/etc/vakr/dashboards.toml")
            for change in ({"created_by": "mallory"}, {"created_at": later},
                           {"managed_by": file_owned}):  # fmt: skip
                with pytest.raises(ValidationError):
                    uow.dashboards.update(replace(stored, **change))

    def test_db003_concurrent_edits_conflict_instead_of_overwriting(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-003: two edits of the same version; the second to commit fails, nothing is lost.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        noc = dashboard()
        with metadata_uow_factory() as uow:
            uow.dashboards.add(noc)
            uow.commit()
        with metadata_uow_factory() as first:
            first.dashboards.update(replace(noc, description="first"))
            first.commit()
        with metadata_uow_factory() as second, pytest.raises(ConflictError):
            second.dashboards.update(replace(noc, description="second"))
        with metadata_uow_factory() as uow:
            stored = uow.dashboards.get(noc.id)
            assert stored is not None
            assert stored.description == "first"

    def test_delete_frees_the_name_and_lists_are_ordered_and_bounded(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """E.31: deletion returns the record and frees its name; lists are (name, id), bounded.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        boards = [dashboard(n) for n in ("b-wan", "a-core", "c-sites")]
        with metadata_uow_factory() as uow:
            for b in boards:
                uow.dashboards.add(b)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert [d.name for d in uow.dashboards.list_all(limit=10)] == [
                "a-core", "b-wan", "c-sites"]  # fmt: skip
            assert [d.name for d in uow.dashboards.list_all(limit=2)] == ["a-core", "b-wan"]
            assert uow.dashboards.list_all(limit=0) == ()
            assert uow.dashboards.delete(boards[0].id) == boards[0]
            assert uow.dashboards.delete(boards[0].id) is None
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.dashboards.count() == 2
            assert uow.dashboards.get(boards[0].id) is None
            uow.dashboards.add(dashboard("b-wan"))  # the name is free again
            uow.commit()
