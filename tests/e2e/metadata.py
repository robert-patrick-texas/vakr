"""Write the end-to-end fleet into ``metadata.db`` through the repositories (WEB-T01 to WEB-T03).

``seed_metadata`` stores the sites, tags, groups, collectors, devices,
interfaces, and one open identity review of ``tests.e2e.fleet`` exactly as
the inventory service would, through the SQLite unit of work: no SQL here.
It returns the records, so the telemetry seed and the fixture collector
know every UUID, and ``Seeded.fixtures`` lists them for the browser tests.

Dependencies:
    vakr.adapters.sqlite.metadata: The store and unit of work.
    tests.contract.contract_samples, contract_metadata_identity: Record builders.

Safety:
    Writes only to the relocated install it is given; holds no credential.

Example:
    >>> Seeded.__name__
    'Seeded'
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.contract import contract_samples as samples
from tests.contract.contract_metadata_identity import review
from tests.e2e.fleet import (
    COLLECTORS,
    DEVICES,
    FILE_MANAGED,
    GROUPS,
    SITES,
    TAGS,
    DeviceSpec,
    IfaceSpec,
)
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, sqlite_uow_factory
from vakr.domain.catalog import Collector
from vakr.domain.enums import AdminState, InterestClass
from vakr.domain.interfaces import Interface, InterfaceAnnotation
from vakr.domain.inventory import API_MANAGED, Device
from vakr.inventory.model import FILE_OWNER

_ADMIN = {"disabled": AdminState.DISABLED, "retired": AdminState.RETIRED}
ETHERNET = 6


@dataclass
class Seeded:
    """Everything the seed stored.

    Attributes:
        collectors: Collector records by name.
        devices: Device records by name.
        specs: Device plans by name.
        interfaces: Interface records by ``device/ifName``.
        iface_specs: Interface plans by ``device/ifName``.
        labels: Site, tag, and group UUIDs by kind and name.
        reviews: Open review UUIDs.
    """

    collectors: dict[str, Collector] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)
    specs: dict[str, DeviceSpec] = field(default_factory=dict)
    interfaces: dict[str, Interface] = field(default_factory=dict)
    iface_specs: dict[str, IfaceSpec] = field(default_factory=dict)
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    reviews: list[str] = field(default_factory=list)

    def fixtures(self, scenario: str, anchor: datetime) -> dict[str, Any]:
        """Describe the seed for the browser tests (UUIDs and names only, no secrets).

        Args:
            scenario: ``normal`` or ``no-telemetry``.
            anchor: The seed's "now", aligned to the minute (UTC).

        Returns:
            dict[str, Any]: JSON-ready fixtures.

        Raises:
            None
        """
        return {
            "scenario": scenario,
            "anchor": anchor.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "collectors": {n: str(c.id) for n, c in self.collectors.items()},
            "devices": {n: str(d.id) for n, d in self.devices.items()},
            "interfaces": {k: str(i.id) for k, i in self.interfaces.items()},
            "sites": self.labels.get("sites", {}),
            "tags": self.labels.get("tags", {}),
            "groups": self.labels.get("groups", {}),
            "reviews": list(self.reviews),
        }


def _annotation(spec: IfaceSpec) -> InterfaceAnnotation:
    """Build an interface's operator annotation from its plan.

    Args:
        spec: The plan.

    Returns:
        InterfaceAnnotation: Interest, sticky, WAN circuit metadata.

    Raises:
        None
    """
    contracted = spec.contracted_mbps * 1_000_000 if spec.contracted_mbps else None
    interest = InterestClass.PRIMARY if spec.primary else InterestClass.SECONDARY
    return InterfaceAnnotation(interest=interest, sticky=spec.sticky, is_wan_circuit=spec.wan,
                               provider=spec.provider, circuit_id=spec.circuit,
                               contracted_bps=contracted)  # fmt: skip


def _interface(device: Device, spec: IfaceSpec) -> Interface:
    """Build an interface record from its plan.

    Args:
        device: The owning device.
        spec: The plan.

    Returns:
        Interface: The record (not stored yet).

    Raises:
        None
    """
    owner = FILE_OWNER if f"{device.name}/{spec.name}" in FILE_MANAGED else API_MANAGED
    return samples.interface(
        device.id, spec.name, if_index=spec.if_index, if_descr=spec.name, if_alias=spec.alias,
        if_type=ETHERNET, if_high_speed=spec.speed_mbps, if_speed=None, mac=spec.mac,
        addresses=spec.addresses, annotation=_annotation(spec), managed_by=owner,
    )  # fmt: skip


def _labels(uow: Any, seeded: Seeded) -> dict[str, dict[str, Any]]:
    """Store sites, tags, groups, and collectors.

    Args:
        uow: An open unit of work.
        seeded: Receives the collectors and label UUIDs.

    Returns:
        dict[str, dict[str, Any]]: Records by kind and key (site code, tag, group name).

    Raises:
        None
    """
    sites = {code: uow.sites.add(samples.site(name, code=code, timezone=zone))
             for name, code, zone in SITES}  # fmt: skip
    tags = {name: uow.tags.add(samples.tag(name)) for name in TAGS}
    groups = {name: uow.groups.add(samples.group(name)) for name in GROUPS}
    for name in COLLECTORS:
        seeded.collectors[name] = uow.collectors.add(samples.collector(name))
    seeded.labels = {
        "sites": {s.name: str(s.id) for s in sites.values()},
        "tags": {n: str(t.id) for n, t in tags.items()},
        "groups": {n: str(g.id) for n, g in groups.items()},
    }
    return {"sites": sites, "tags": tags, "groups": groups}


def _devices(uow: Any, seeded: Seeded, labels: dict[str, dict[str, Any]]) -> None:
    """Store every planned device and its interfaces.

    Args:
        uow: An open unit of work.
        seeded: Receives the records.
        labels: Sites, tags, and groups from ``_labels``.

    Returns:
        None

    Raises:
        None
    """
    for spec in DEVICES:
        site = labels["sites"][spec.site].id if spec.site else None
        device = uow.devices.add(samples.device(
            spec.name, poll_address=spec.address, site_id=site,
            admin_state=_ADMIN.get(spec.behavior, AdminState.ENABLED),
            tags=frozenset(labels["tags"][t].id for t in spec.tags),
            groups=frozenset(labels["groups"][g].id for g in spec.groups),
            comment=f"e2e fixture: {spec.behavior}",
            managed_by=FILE_OWNER if spec.name in FILE_MANAGED else API_MANAGED,
        ))  # fmt: skip
        seeded.devices[spec.name] = device
        seeded.specs[spec.name] = spec
        for iface_spec in spec.interfaces:
            key = f"{spec.name}/{iface_spec.name}"
            seeded.interfaces[key] = uow.interfaces.add(_interface(device, iface_spec))
            seeded.iface_specs[key] = iface_spec


def seed_metadata(state_dir: Path, opened_at: datetime) -> Seeded:
    """Store the whole fleet and one open identity review on ``dist1``.

    Args:
        state_dir: The install's state directory (``metadata.db`` is migrated).
        opened_at: When the review was opened (UTC).

    Returns:
        Seeded: The stored records.

    Raises:
        None
    """
    seeded = Seeded()
    store = SqliteMetadataStore(state_dir / METADATA_FILE)
    try:
        with sqlite_uow_factory(store)() as uow:
            labels = _labels(uow, seeded)
            _devices(uow, seeded, labels)
            dist1 = seeded.devices["dist1"]
            candidates = (
                seeded.interfaces["dist1/Te0/0/5"].id,
                seeded.interfaces["dist1/Te0/0/6"].id,
            )
            case = uow.reviews.add(review(dist1.id, candidates, opened_at=opened_at))
            seeded.reviews.append(str(case.id))
            uow.commit()
    finally:
        store.close()
    return seeded
