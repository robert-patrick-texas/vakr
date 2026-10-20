"""End to end on the SQLite metadata adapter: apply, discover, plan, review, upgrade.

A CLI process and a collector process each open their own store on one
``metadata.db``. The collector sees an apply through the persisted change
counter (6.2), records discoveries, and plans polls; identity history,
reviews, and lifecycle survive in SQLite (IFID-T01/T02/T06, DISC-T01,
DISC-T05). A schema-1 database written by release 0.1.1 upgrades to
schema 2 with its data intact (MIG-002).

Dependencies:
    pytest; the SQLite metadata adapter; tests.unit.inventory.inventory_fixtures.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_sqlite.py
"""

import tomllib
from pathlib import Path

from tests.contract import contract_samples as s
from tests.unit.inventory.inventory_fixtures import DAY, SAMPLE_INVENTORY, T0, discovery, row
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_family,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.ids import CollectorId, new_id
from vakr.domain.paging import PageRequest
from vakr.inventory.apply import apply_inventory
from vakr.inventory.guardrails import TargetPolicy
from vakr.inventory.parse import parse_document
from vakr.inventory.reads import device_interfaces, open_reviews
from vakr.inventory.service import InventoryService
from vakr.inventory.validate import Checks
from vakr.policy.rules_parse import parse_policy
from vakr.ports.clock import ManualClock

CHECKS = Checks(frozenset({"network-v2c"}), None, TargetPolicy())
NO_LOOPBACKS = parse_policy(
    {
        "interface_policy": {
            "rules": [{"name": "no-lo", "action": "exclude", "match": {"ifname": ["Lo*"]}}]
        }
    }
)


def test_end_to_end_two_processes(tmp_path: Path) -> None:
    """6.2, DISC-T01, DISC-T05, IFID-T02, IFID-T06 on SQLite with two stores on one file.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    metadata_migrations(db).migrate()
    cli, collector = SqliteMetadataStore(db), SqliteMetadataStore(db)
    clock = ManualClock(T0)
    service = InventoryService(
        sqlite_uow_factory(collector), new_id(CollectorId), NO_LOOPBACKS, clock
    )
    before = service.change_counter()
    inv = parse_document(tomllib.loads(SAMPLE_INVENTORY), "inventory.toml")
    apply_inventory(sqlite_uow_factory(cli), inv, CHECKS, "cli:netops", clock)
    assert service.change_counter() > before  # the other process sees the apply
    edge = next(d for d in service.devices() if d.name == "edge1.example.net")
    assert service.poll_plan(edge.id) is None  # DISC-001: needs discovery
    rows = [row(1, "Gi0/0/0", if_alias="ISP circuit"), row(2, "Lo0", if_type=24), row(3, "Gi0/0/3")]
    plan = service.record_discovery(edge.id, discovery(rows), T0)
    assert [(p.if_index, p.collect) for p in plan.interfaces] == [(1, True), (2, False), (3, True)]
    moved = [row(11, "Gi0/0/0", if_alias="ISP circuit"), row(2, "Lo0", if_type=24),
             row(3, "Te0/0/3")]  # fmt: skip
    after = service.record_discovery(edge.id, discovery(moved), T0 + DAY)
    by_index = {p.if_index: p.interface_id for p in after.interfaces}
    assert by_index[11] == plan.interfaces[0].interface_id  # renumber keeps the UUID
    assert by_index[3] is None  # the renamed port waits for review
    gi0_id = by_index[11]
    assert gi0_id is not None
    with sqlite_uow_factory(cli)() as uow:
        (review,) = open_reviews(uow, edge.id)
        lo = next(i for i in device_interfaces(uow, edge.id) if i.if_name == "Lo0")
        gi0 = uow.interfaces.get(gi0_id)
        history = uow.interfaces.identity_history(gi0_id, PageRequest()).items
    assert review.outcome is ReconcileOutcome.PROBABLE
    assert review.if_name == "Te0/0/3"
    assert lo.if_index == 2  # DISC-007: suppressed but inventoried
    assert gi0 is not None
    assert gi0.if_alias == "ISP circuit"
    assert [e.outcome for e in history] == [
        ReconcileOutcome.NEW,
        ReconcileOutcome.CONFIDENT_RENUMBER,
    ]
    assert history[-1].previous_if_index == 1
    cli.close()
    collector.close()


def test_mig002_schema_1_upgrades_with_data(tmp_path: Path) -> None:
    """MIG-002: a 0.1.1 database (schema 1) gains identity reviews and keeps its records.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    db = tmp_path / METADATA_FILE
    full = metadata_family()
    old = SchemaFamily(full.name, full.application_id, full.migrations[:1])
    SqliteMigrationManager(db, old).migrate()
    store = SqliteMetadataStore(db, family=old)
    device = s.device()
    with sqlite_uow_factory(store)() as uow:
        uow.devices.add(device)
        uow.commit()
    store.close()
    applied = metadata_migrations(db).migrate()
    assert [(a.version, a.name) for a in applied] == [(2, "identity_review")]
    upgraded = SqliteMetadataStore(db)
    with sqlite_uow_factory(upgraded)() as uow:
        assert uow.devices.get(device.id) == device
        assert uow.reviews.list_page(PageRequest()).total == 0
        assert uow.change_counter() == 2  # one device at version 1
    upgraded.close()
