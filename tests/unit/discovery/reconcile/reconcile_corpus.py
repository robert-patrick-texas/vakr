"""Load the versioned reconciliation corpus into domain records (IFID-009).

``tests/fixtures/reconcile/corpus-v1.toml`` describes devices as plain
tables; this module turns them into stored ``Interface`` records and
discovered rows, and gives each stored record a stable UUID derived from
its case and key so expectations can name them.

Dependencies:
    tomllib; vakr.discovery, vakr.domain.

Example:
    >>> [c.name for c in load_cases()][:1]
    ['unchanged-physical-ports']
"""

import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vakr.discovery.model import DiscoveredInterface
from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.ownership import API_MANAGED

CORPUS = Path(__file__).parents[3] / "fixtures" / "reconcile" / "corpus-v1.toml"
T0 = datetime(2026, 10, 1, tzinfo=UTC)
DEVICE = DeviceId(uuid.UUID("01900000-0000-7000-8000-00000000d001"))
_NAMESPACE = uuid.UUID("6f0b6f3e-6d1d-4a0e-9a64-0d0c0a0f0001")


@dataclass(frozen=True, slots=True)
class Case:
    """One corpus device.

    Attributes:
        name: Case name.
        stored: Stored interfaces by key.
        rows: Discovered rows.
        expect: Expected outcome tables, one per row.
        missing: Keys expected missing.
        held: Keys expected held.
        inventory_complete: Whether ``ifTable`` was complete.
        addresses_complete: Whether IP-MIB was complete.
    """

    name: str
    stored: dict[str, Interface]
    rows: tuple[DiscoveredInterface, ...]
    expect: tuple[dict[str, Any], ...]
    missing: tuple[str, ...]
    held: tuple[str, ...]
    inventory_complete: bool
    addresses_complete: bool


def interface_id(case: str, key: str) -> InterfaceId:
    """Return the stable UUID of a stored corpus interface.

    Args:
        case: Case name.
        key: Interface key.

    Returns:
        InterfaceId: A name-based UUID.

    Raises:
        None
    """
    return InterfaceId(uuid.uuid5(_NAMESPACE, f"{case}/{key}"))


def _stored(case: str, table: dict[str, Any]) -> Interface:
    """Build a stored interface from a corpus table.

    Args:
        case: Case name.
        table: The table.

    Returns:
        Interface: The record.

    Raises:
        None
    """
    return Interface(
        id=interface_id(case, table["key"]), device_id=DEVICE, if_index=table.get("if_index"),
        if_name=table.get("if_name", ""), if_descr=table.get("if_descr", ""),
        if_alias=table.get("if_alias", ""),
        lifecycle=InterfaceLifecycle(table.get("lifecycle", "active")), managed_by=API_MANAGED,
        created_at=T0, updated_at=T0, if_type=table.get("if_type"), mac=table.get("mac"),
        addresses=tuple(table.get("addresses", ())),
    )  # fmt: skip


def row(table: dict[str, Any]) -> DiscoveredInterface:
    """Build a discovered row, up/up unless the table says otherwise.

    Args:
        table: The table.

    Returns:
        DiscoveredInterface: The row.

    Raises:
        None
    """
    return DiscoveredInterface(
        if_index=table["if_index"], if_descr=table.get("if_descr"), if_name=table.get("if_name"),
        if_alias=table.get("if_alias", ""), if_type=table.get("if_type"), mac=table.get("mac"),
        admin_status=IfAdminStatus(table.get("admin", 1)),
        oper_status=IfOperStatus(table.get("oper", 1)), addresses=tuple(table.get("addresses", ())),
    )  # fmt: skip


def load_cases(path: Path = CORPUS) -> tuple[Case, ...]:
    """Load every case of the corpus.

    Args:
        path: The corpus file.

    Returns:
        tuple[Case, ...]: Cases in file order.

    Raises:
        None
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in data["cases"]:
        stored = {t["key"]: _stored(c["name"], t) for t in c["stored"]}
        cases.append(
            Case(
                c["name"], stored, tuple(row(r) for r in c["rows"]), tuple(c["expect"]),
                tuple(c.get("missing", ())), tuple(c.get("held", ())),
                c.get("inventory_complete", True), c.get("addresses_complete", True),
            )
        )  # fmt: skip
    return tuple(cases)


def corpus_versions(path: Path = CORPUS) -> tuple[int, str]:
    """Return the corpus version and the rules version it was approved under.

    Args:
        path: The corpus file.

    Returns:
        tuple[int, str]: ``corpus_version`` and ``rules_version``.

    Raises:
        None
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["corpus_version"], data["rules_version"]
