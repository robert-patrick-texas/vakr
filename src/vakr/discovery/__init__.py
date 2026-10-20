"""SNMP discovery: what a device says about itself, its interfaces, and addresses.

Discovery reads the system group, IF-MIB, and IP-MIB of one device
(blueprint 9.4, DISC-004..DISC-008) and returns a ``DiscoveryResult``: facts
keyed by the ``ifIndex`` the agent used, with completeness recorded per
table and per field. Identity reconciliation (ADR-015) and inventory
updates consume it in milestone M4; collection policy consumes it now
through ``discovery_facts``.

Modules:
    mib: The OID catalog.
    parse, interfaces: Rows to typed per-interface values.
    discover: The discovery run.
    model: The result types.
    facts: Mapping into ``vakr.policy`` facts.

Dependencies:
    vakr.ports.snmp: The SNMP session port; no SNMP library is imported.
    vakr.policy.facts: The facts policy rules match on.

Safety:
    Read-only SNMP requests; ``ifIndex`` is never used as identity.

Example:
    >>> from vakr.discovery import SubtableStatus
    >>> SubtableStatus.FAILED.usable
    False
"""

from vakr.discovery.discover import discover
from vakr.discovery.facts import DeviceContext, discovery_facts, interface_facts, is_physical
from vakr.discovery.model import (
    DiscoveredInterface,
    DiscoveryResult,
    SubtableResult,
    SubtableStatus,
    SystemFacts,
)

__all__ = [
    "DeviceContext",
    "DiscoveredInterface",
    "DiscoveryResult",
    "SubtableResult",
    "SubtableStatus",
    "SystemFacts",
    "discover",
    "discovery_facts",
    "interface_facts",
    "is_physical",
]
