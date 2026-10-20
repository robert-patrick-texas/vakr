"""ICMP collector: probe groups to ``IcmpObservation`` values (ICMP-001..011, E.1, E.7).

``IcmpCollector`` asks an ``IcmpProber`` for one cycle per device and
summarises it with ``vakr.logic.icmp``: per-probe RTTs, loss, min/avg/
median/max, jitter (ICMP-010), the E.1 state with ``PARTIAL_LOSS`` and
``SINGLE_REPLY`` flags, and the SNMP plan for the device's strategy.

- ``settings``: the ``[icmp]`` configuration block and its validation.
- ``cycle``: the pure conversion and the collector.

Dependencies:
    vakr.ports.icmp, vakr.logic.icmp, vakr.domain: Contract, math, records.

Safety:
    Probes the collector could not send or watch never count as loss; a
    collector fault marks the cycle ``COLLECTOR_DEGRADED``, and a cycle
    with no probe sent is ``UNKNOWN``/``NOT_ATTEMPTED`` (invariant 2).

Example:
    >>> from vakr.collectors.icmp.settings import IcmpSettings
    >>> IcmpSettings().request("192.0.2.1").count
    5
"""
