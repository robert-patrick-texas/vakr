"""Builders shared by the bulk import and export tests (META-010, E.38).

``seeded()`` stores the sample ``inventory.toml`` (file-managed site,
devices, and one annotation), discovers three interfaces on the edge
router (``Gi0/0/0`` claimed by the file, ``Gi0/0/1`` and ``Gi0/0/2``
API-managed), and adds one API-managed device, ``core9``, so every
ownership case is present. Addresses are RFC 5737 documentation addresses.

Dependencies:
    vakr.adapters.memory, vakr.inventory.

Example:
    >>> store = seeded()
    >>> store.edge.name
    'edge1.example.net'
"""

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from tests.unit.inventory.inventory_fixtures import SAMPLE_INVENTORY, T0, discovery, row
from vakr.adapters.memory import memory_uow_factory
from vakr.domain.ids import CollectorId, DeviceId, new_id
from vakr.domain.inventory import API_MANAGED, Device
from vakr.inventory.apply import apply_inventory
from vakr.inventory.claim import claim_interface
from vakr.inventory.guardrails import DEFAULT_DENIED, TargetPolicy
from vakr.inventory.import_apply import apply_import
from vakr.inventory.import_file import parse_import
from vakr.inventory.import_plan import import_snapshot, plan_import
from vakr.inventory.model import InventoryFile
from vakr.inventory.parse import parse_document
from vakr.inventory.plan_model import InventoryPlan
from vakr.inventory.render import plan_lines
from vakr.inventory.service import InventoryService
from vakr.inventory.validate import Checks
from vakr.policy.rules import Policy
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

CHECKS = Checks(frozenset({"network-v2c"}), None, TargetPolicy.parse([], list(DEFAULT_DENIED)))
CLOCK = ManualClock(datetime(2026, 10, 27, 12, tzinfo=UTC))
CORE9_UUID = "01900000-0000-7000-8000-0000000000c9"


@dataclass(frozen=True, slots=True)
class Seeded:
    """A metadata store with every ownership case.

    Attributes:
        factory: Opens units of work.
        edge: The file-managed edge router.
        core9: The API-managed device.
    """

    factory: MetadataUnitOfWorkFactory
    edge: Device
    core9: Device


def toml_doc(text: str = SAMPLE_INVENTORY) -> InventoryFile:
    """Parse ``inventory.toml`` text.

    Args:
        text: TOML text.

    Returns:
        InventoryFile: The file.

    Raises:
        None
    """
    return parse_document(tomllib.loads(text), "inventory.toml")


def seeded() -> Seeded:
    """Store the sample inventory, discover the edge router, claim one interface.

    Args:
        None

    Returns:
        Seeded: The store and its devices.

    Raises:
        None
    """
    factory = memory_uow_factory()
    apply_inventory(factory, toml_doc(), CHECKS, "cli:netops", CLOCK)
    core9 = Device(DeviceId(UUID(CORE9_UUID)), "core9", "192.0.2.90", API_MANAGED, T0, T0,
                   credential_ref="network-v2c")  # fmt: skip
    with factory() as uow:
        edge = uow.devices.get_by_name("edge1.example.net")
        uow.devices.add(core9)
        uow.commit()
    assert edge is not None
    service = InventoryService(factory, new_id(CollectorId), Policy(), CLOCK)
    rows = [row(1, "Gi0/0/0"), row(2, "Gi0/0/1", if_alias="=HYPERLINK(1)"), row(3, "Gi0/0/2")]
    service.record_discovery(edge.id, discovery(rows), T0)
    service.record_discovery(core9.id, discovery([row(1, "Te1/0/1")]), T0)
    claim_interface(factory, toml_doc(), "edge1.example.net", "Gi0/0/0", "cli:netops", CLOCK)
    return Seeded(factory, edge, core9)


def plan_of(factory: MetadataUnitOfWorkFactory, raw: bytes, name: str) -> InventoryPlan:
    """Parse and plan an import without applying it.

    Args:
        factory: Opens units of work.
        raw: File content.
        name: File name, whose suffix gives the format.

    Returns:
        InventoryPlan: The plan.

    Raises:
        InventoryError: If the import has problems.
    """
    imp = parse_import(raw, name, None, None)
    with factory() as uow:
        return plan_import(imp, import_snapshot(uow, imp), CHECKS)[0]


def plan_text(factory: MetadataUnitOfWorkFactory, raw: bytes, name: str) -> list[str]:
    """Plan an import and render it.

    Args:
        factory: Opens units of work.
        raw: File content.
        name: File name.

    Returns:
        list[str]: The plan's lines.

    Raises:
        InventoryError: If the import has problems.
    """
    return plan_lines(plan_of(factory, raw, name))


def apply_raw(factory: MetadataUnitOfWorkFactory, raw: bytes, name: str) -> InventoryPlan:
    """Parse and apply an import.

    Args:
        factory: Opens units of work.
        raw: File content.
        name: File name.

    Returns:
        InventoryPlan: What was applied.

    Raises:
        InventoryError: If the import has problems; nothing is written.
    """
    return apply_import(factory, parse_import(raw, name, None, None), CHECKS, "cli:netops", CLOCK)[
        0
    ]
