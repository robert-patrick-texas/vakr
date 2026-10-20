"""The SNMP port: sessions, targets, values, and results with no library types.

Discovery and the Stage 1/Stage 2 collectors talk to devices only through
``SnmpSessionFactory`` and ``SnmpSession``. The PySNMP adapter in
``vakr.adapters.snmp`` implements them; tests use simulators. Nothing from
PySNMP or pyasn1 crosses this boundary (ARCH-001), so a later SNMPv3 or Go
implementation can stand behind the same protocol.

Modules:
    values: OIDs, value kinds, and varbinds.
    target: The agent to poll (SNMP-016, SNMP-019).
    settings: The request budget (SNMP-011, SNMP-017).
    errors: Explicit errors, and which side of the wire failed.
    results: GET and walk results and effort counts (SNMP-014, SNMP-018).
    session: The session and factory protocols (SNMP-012, SNMP-015).

Dependencies:
    vakr.domain: Enums, quality flags, and validation helpers.

Safety:
    Targets name credentials by reference only; no port type holds a secret.

Example:
    >>> from vakr.ports.snmp import SnmpTarget, parse_oid
    >>> SnmpTarget("192.0.2.1", credential="network-v2c").port, parse_oid(".1.3.6.1")
    (161, (1, 3, 6, 1))
"""

from vakr.ports.snmp.errors import (
    COLLECTOR_SIDE,
    STATUS_NAMES,
    SnmpError,
    SnmpErrorKind,
    SnmpSessionError,
)
from vakr.ports.snmp.results import GetResult, SnmpStats, WalkResult
from vakr.ports.snmp.session import SnmpSession, SnmpSessionFactory
from vakr.ports.snmp.settings import SnmpSettings
from vakr.ports.snmp.target import SnmpTarget
from vakr.ports.snmp.values import (
    EXCEPTION_KINDS,
    Oid,
    ValueKind,
    VarBind,
    format_oid,
    in_subtree,
    parse_oid,
    require_oid,
)

__all__ = [
    "COLLECTOR_SIDE",
    "EXCEPTION_KINDS",
    "STATUS_NAMES",
    "GetResult",
    "Oid",
    "SnmpError",
    "SnmpErrorKind",
    "SnmpSession",
    "SnmpSessionError",
    "SnmpSessionFactory",
    "SnmpSettings",
    "SnmpStats",
    "SnmpTarget",
    "ValueKind",
    "VarBind",
    "WalkResult",
    "format_oid",
    "in_subtree",
    "parse_oid",
    "require_oid",
]
