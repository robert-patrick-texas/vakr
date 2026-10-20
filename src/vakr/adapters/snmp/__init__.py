"""The PySNMP adapter behind the SNMP port: SNMPv2c sessions over asyncio UDP.

``PysnmpSessionFactory`` implements ``vakr.ports.snmp.SnmpSessionFactory``
with PySNMP's asyncio ``v1arch`` API, which speaks SNMPv1/v2c without the
SNMPv3 engine machinery. SNMPv3 (post-MVP, blueprint 27.4) will be a second
transport behind the same request engine: ``engine``, ``getbatch``, and
``walking`` hold the version-neutral logic, and only ``transport`` and
``factory`` know PySNMP's v2c API (ADR-034).

Modules:
    pdu: The PDU/reply seam between the engine and a transport.
    engine: Complete-attempt retries, size halving, and accounting.
    getbatch, walking: GET batching and GETBULK subtree walks.
    codec: PySNMP values to port varbinds.
    transport: Single PDU attempts through PySNMP.
    guards: PySNMP's secret leaks, closed.
    session, factory: The port implementation and the per-device locks.
    messages: Operator-language errors.

Dependencies:
    pysnmp, pyasn1: The SNMP library.
    vakr.credentials: Credentials by reference.

Safety:
    Communities are resolved per session and dropped when it closes; no
    log message, exception, or ``repr`` in this package contains one.

Example:
    >>> from vakr.adapters.snmp import PysnmpSessionFactory
    >>> PysnmpSessionFactory(resolver=None)
    PysnmpSessionFactory(open_sessions=0)
"""

from vakr.adapters.snmp.factory import PysnmpSessionFactory

__all__ = ["PysnmpSessionFactory"]
