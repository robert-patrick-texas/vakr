"""Metadata CRUD parity: DB-001 (round trips, order, paging, filters) and DB-007.

Subclass ``MetadataCrudContract`` in an adapter's test module; the adapter's
``conftest.py`` supplies ``metadata_uow_factory``.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k MetadataCrud
"""

from dataclasses import replace

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import (
    AddressFamily,
    AdminState,
    InterestClass,
    InterfaceLifecycle,
    ReachabilityStrategy,
)
from vakr.domain.errors import DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import DeviceId, GroupId, SiteId, TagId, new_id
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.inventory import API_MANAGED, Device
from vakr.domain.paging import PageRequest
from vakr.ports.metadata import DeviceFilter, InterfaceFilter, MetadataUnitOfWorkFactory


def _rich_device(site_id: SiteId, tag_id: TagId, group_id: GroupId) -> Device:
    """Build a device with every optional field set.

    Args:
        site_id: Existing site.
        tag_id: Existing tag.
        group_id: Existing group.

    Returns:
        Device: The device.

    Raises:
        None
    """
    return s.device(
        "edge-ams", poll_address="edge-ams.example.net", address_family=AddressFamily.IPV6,
        site_id=site_id, admin_state=AdminState.MAINTENANCE, poll_interval_s=30,
        discovery_interval_s=86_400, reachability=ReachabilityStrategy.SNMP_FALLBACK,
        credential_ref="snmp-core", comment="line 1\nline 2", tags=frozenset({tag_id}),
        groups=frozenset({group_id}),
    )  # fmt: skip


class MetadataCrudContract:
    """DB-001 and DB-007 for any ``MetadataUnitOfWork`` implementation."""

    def test_db001_every_entity_round_trips(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-001: every record type reads back equal, by ID and by natural key.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        site, tag, group = s.site(tags=frozenset()), s.tag(), s.group()
        dev = _rich_device(site.id, tag.id, group.id)
        iface = s.interface(
            dev.id, "Gi0/0/0", if_alias="WAN-ISP_Carrier", if_type=6, mac="00:1a:2b:3c:4d:5e",
            if_speed=1_000_000_000, if_high_speed=1000, addresses=("10.0.0.1/30", "2001:db8::1/64"),
            comment="to ISP", tags=frozenset({tag.id}), description_tags=frozenset({tag.id}),
            annotation=InterfaceAnnotation(interest=InterestClass.PRIMARY, sticky=True,
                is_wan_circuit=True, provider="Carrier", circuit_id="C-1", contracted_bps=10**9,
                capacity_override_bps=5 * 10**8, site_override=site.id, public_dashboard=True,
                display_rank=1),
        )  # fmt: skip
        pol, col = s.policy(), s.collector()
        with metadata_uow_factory() as uow:
            uow.sites.add(site)
            uow.tags.add(tag)
            uow.groups.add(group)
            uow.policies.add(pol)
            uow.collectors.add(col)
            uow.devices.add(dev)
            uow.interfaces.add(iface)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert (uow.sites.get(site.id), uow.sites.get_by_name(site.name)) == (site, site)
            assert (uow.tags.get(tag.id), uow.tags.get_by_name(tag.name)) == (tag, tag)
            assert (uow.groups.get(group.id), uow.groups.get_by_name(group.name)) == (group, group)
            assert (uow.policies.get(pol.id), uow.policies.get_by_name(pol.name)) == (pol, pol)
            assert (uow.collectors.get(col.id), uow.collectors.get_by_name(col.name)) == (col, col)
            assert (uow.devices.get(dev.id), uow.devices.get_by_name(dev.name)) == (dev, dev)
            assert uow.interfaces.get(iface.id) == iface
            assert uow.devices.get(new_id(DeviceId)) is None
            assert uow.devices.get_by_name("no-such-device") is None

    def test_db001_update_increments_version(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-001/DB-003: an update stores ``version + 1`` and returns what it stored.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev = s.device()
        with metadata_uow_factory() as uow:
            uow.devices.add(dev)
            uow.commit()
        with metadata_uow_factory() as uow:
            stored = uow.devices.update(replace(dev, comment="moved", updated_at=s.T0 + s.MINUTE),
                                        writer=API_MANAGED)  # fmt: skip
            assert stored.version == 2
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.devices.get(dev.id) == stored
            assert stored.comment == "moved"

    def test_db001_paging_is_ordered_complete_and_stable(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """API-005: pages follow ``(name, id)``, carry a total, and survive inserts.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        names = ["d", "b", "e", "a", "c"]
        with metadata_uow_factory() as uow:
            for name in names:
                uow.devices.add(s.device(name))
            uow.commit()
        with metadata_uow_factory() as uow:
            first = uow.devices.list_page(PageRequest(limit=2))
            assert [d.name for d in first.items] == ["a", "b"]
            assert first.total == 5
            assert first.next_cursor is not None
            uow.devices.add(s.device("aa"))  # sorts before the cursor: must not reappear
            second = uow.devices.list_page(PageRequest(limit=2, cursor=first.next_cursor))
            third = uow.devices.list_page(PageRequest(limit=2, cursor=second.next_cursor))
        assert [d.name for d in second.items] == ["c", "d"]
        assert [d.name for d in third.items] == ["e"]
        assert third.next_cursor is None
        assert third.total == 6

    def test_db001_invalid_cursor_is_rejected(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """API-005: a cursor this repository did not issue raises ``ValidationError``.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        with metadata_uow_factory() as uow:
            uow.devices.add(s.device())
            with pytest.raises(ValidationError):
                uow.devices.list_page(PageRequest(cursor="not a cursor!"))

    def test_db001_device_filters(self, metadata_uow_factory: MetadataUnitOfWorkFactory) -> None:
        """DB-001, 7.5: site, tag, group, and state filters; retired hidden by default.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        site, tag, group = s.site(), s.tag(), s.group()
        a = s.device("a", site_id=site.id, tags=frozenset({tag.id}))
        b = s.device("b", groups=frozenset({group.id}), admin_state=AdminState.DISABLED)
        c = s.device("c", admin_state=AdminState.RETIRED)
        with metadata_uow_factory() as uow:
            uow.sites.add(site)
            uow.tags.add(tag)
            uow.groups.add(group)
            for d in (a, b, c):
                uow.devices.add(d)
            uow.commit()

            def names(where: DeviceFilter | None) -> list[str]:
                """List device names matching a filter.

                Args:
                    where: The filter.

                Returns:
                    list[str]: Names in list order.

                Raises:
                    None
                """
                return [d.name for d in uow.devices.list_page(PageRequest(), where).items]

            assert names(None) == ["a", "b"]
            assert names(DeviceFilter(include_retired=True)) == ["a", "b", "c"]
            assert names(DeviceFilter(admin_state=AdminState.RETIRED)) == ["c"]
            assert names(DeviceFilter(site_id=site.id)) == ["a"]
            assert names(DeviceFilter(tag_id=tag.id)) == ["a"]
            assert names(DeviceFilter(group_id=group.id)) == ["b"]

    def test_db001_interface_filters_and_order(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-001: interfaces list by ``(device_id, if_name, id)`` and filter by device and labels.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        wan = s.tag()
        d1, d2 = s.device("d1"), s.device("d2")
        primary = InterfaceAnnotation(interest=InterestClass.PRIMARY, is_wan_circuit=True)
        i1 = s.interface(d1.id, "Gi2", annotation=primary)
        i2 = s.interface(d1.id, "Gi1", description_tags=frozenset({wan.id}))
        i3 = s.interface(d2.id, "Gi1", lifecycle=InterfaceLifecycle.RETIRED)
        with metadata_uow_factory() as uow:
            uow.tags.add(wan)
            for record in (d1, d2):
                uow.devices.add(record)
            for record in (i1, i2, i3):
                uow.interfaces.add(record)
            uow.commit()
        with metadata_uow_factory() as uow:

            def ids(where: InterfaceFilter | None) -> list[object]:
                """List interface IDs matching a filter.

                Args:
                    where: The filter.

                Returns:
                    list[object]: IDs in list order.

                Raises:
                    None
                """
                return [i.id for i in uow.interfaces.list_page(PageRequest(), where).items]

            assert ids(InterfaceFilter(device_id=d1.id)) == [i2.id, i1.id]
            assert ids(InterfaceFilter(interest=InterestClass.PRIMARY)) == [i1.id]
            assert ids(InterfaceFilter(is_wan_circuit=True)) == [i1.id]
            assert ids(InterfaceFilter(is_wan_circuit=False)) == [i2.id]
            assert ids(InterfaceFilter(tag_id=wan.id)) == [i2.id]
            assert i3.id not in ids(None)
            assert ids(InterfaceFilter(lifecycle=InterfaceLifecycle.RETIRED)) == [i3.id]
            expected = sorted((i1, i2, i3), key=lambda x: (str(x.device_id), x.if_name, str(x.id)))
            assert ids(InterfaceFilter(include_retired=True)) == [x.id for x in expected]

    def test_db001_policies_list_in_evaluation_order(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-001, POLICY: policies list by ``(order, name, id)``.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        with metadata_uow_factory() as uow:
            for name, order in (("z", 1), ("b", 20), ("a", 20), ("m", 5)):
                uow.policies.add(s.policy(name, order))
            listed = uow.policies.list_page(PageRequest()).items
        assert [p.name for p in listed] == ["z", "m", "a", "b"]

    def test_db001_duplicates_and_missing_references(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-001: reused IDs/names are ``DuplicateError``; dangling references ``NotFoundError``.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev, other = s.device("a"), s.device("b")
        with metadata_uow_factory() as uow:
            uow.devices.add(dev)
            uow.devices.add(other)
            uow.commit()
        with metadata_uow_factory() as uow:
            with pytest.raises(DuplicateError):
                uow.devices.add(replace(dev, name="fresh"))
            with pytest.raises(DuplicateError):
                uow.devices.add(s.device("a"))
            with pytest.raises(DuplicateError):
                uow.devices.update(replace(other, name="a"), writer=API_MANAGED)
            with pytest.raises(NotFoundError):
                uow.devices.add(s.device("c", site_id=new_id(SiteId)))
            with pytest.raises(NotFoundError):
                uow.devices.update(
                    replace(dev, tags=frozenset({new_id(TagId)})), writer=API_MANAGED
                )
            with pytest.raises(NotFoundError):
                uow.interfaces.add(s.interface(new_id(DeviceId)))
            with pytest.raises(NotFoundError):
                uow.devices.update(s.device("ghost"), writer=API_MANAGED)
            with pytest.raises(ValidationError):
                uow.devices.add(s.device("v2", version=2))

    def test_db007_no_row_ids_in_metadata_reads(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """DB-007, API-002: read models carry UUIDs only; cursors are not row numbers.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        dev = s.device()
        with metadata_uow_factory() as uow:
            uow.devices.add(dev)
            uow.devices.add(s.device("b"))
            uow.interfaces.add(s.interface(dev.id))
            uow.commit()
        with metadata_uow_factory() as uow:
            page = uow.devices.list_page(PageRequest(limit=1))
            s.assert_no_row_ids(page)
            s.assert_no_row_ids(uow.interfaces.list_page(PageRequest()))
        assert page.next_cursor is not None
        assert not page.next_cursor.isdigit()
