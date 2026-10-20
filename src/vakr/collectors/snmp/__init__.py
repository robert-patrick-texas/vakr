"""SNMP collection: Stage 1 status, Stage 2 counters, and observation builders.

Each poll of a device runs Stage 1 (``sysUpTime.0`` plus exact status OIDs)
and then Stage 2 (counters) only for interfaces that are admin up and oper
up and that policy collects (ADR-005, blueprint 9.5-9.6). The results are raw
samples stamped with UTC and monotonic time. Rates are derived later by the
pipeline through ``vakr.logic``, and the builders in ``observations`` turn
samples and rates into canonical observations (10.2, 10.3).

Modules:
    plan: What a poll asks for and the capabilities it remembers.
    stage1, stage2, batches, counters: The two stages.
    poll: One device poll and its SNMP state.
    observations: SNMP device and interface observation builders.
    config: The ``[snmp]`` configuration.

Dependencies:
    vakr.ports.snmp: The SNMP session port.
    vakr.discovery: The OID catalog and discovery results.
    vakr.logic: Counter samples, rates, and uptime continuity.

Safety:
    Read-only SNMP; missing values are ``None`` with quality flags.

Example:
    >>> from vakr.collectors.snmp import InterfacePlan, PollPlan
    >>> len(PollPlan((InterfacePlan(1), InterfacePlan(2))).interfaces)
    2
"""

from vakr.collectors.snmp.counters import InterfaceSample
from vakr.collectors.snmp.observations import (
    UptimeReading,
    build_interface_observation,
    build_snmp_device_observation,
    skipped_snmp_observation,
    uptime_continuity,
)
from vakr.collectors.snmp.plan import (
    CapabilityChange,
    InterfacePlan,
    PollPlan,
    plan_from_discovery,
    reprobe_due,
)
from vakr.collectors.snmp.poll import SnmpPollResult, poll_device, snmp_state
from vakr.collectors.snmp.stage1 import InterfaceStatus, Stage1Result, run_stage1
from vakr.collectors.snmp.stage2 import Stage2Result, run_stage2

__all__ = [
    "CapabilityChange",
    "InterfacePlan",
    "InterfaceSample",
    "InterfaceStatus",
    "PollPlan",
    "SnmpPollResult",
    "Stage1Result",
    "Stage2Result",
    "UptimeReading",
    "build_interface_observation",
    "build_snmp_device_observation",
    "plan_from_discovery",
    "poll_device",
    "reprobe_due",
    "run_stage1",
    "run_stage2",
    "skipped_snmp_observation",
    "snmp_state",
    "uptime_continuity",
]
