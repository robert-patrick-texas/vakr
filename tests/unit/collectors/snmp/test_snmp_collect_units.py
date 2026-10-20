"""SNMP-T06, SNMP-T07 (unit part), SNMP-010: plans, samples, batching, and state.

Pure parts of Stage 1/Stage 2 are checked without a network: capability
memory and re-probe, the OIDs per counter family, exact packet aggregation
from six components (none of which is kept), type checking of counters,
interface-aligned batching, and how a poll's outcome maps to an SNMP state.

Dependencies:
    pytest: Parametrised cases.
    vakr.collectors.snmp: The code under test.

Example:
    $ uv run pytest tests/unit/collectors/snmp/test_snmp_collect_units.py
"""

from datetime import UTC, datetime, timedelta

import pytest

from vakr.collectors.snmp import CapabilityChange, InterfacePlan, PollPlan, plan_from_discovery
from vakr.collectors.snmp.batches import first_batch, learn, pack
from vakr.collectors.snmp.config import snmp_config
from vakr.collectors.snmp.counters import counter_oids, counter_values
from vakr.collectors.snmp.plan import reprobe_due
from vakr.collectors.snmp.poll import SnmpPollResult, snmp_state
from vakr.collectors.snmp.stage1 import InterfaceStatus, Stage1Result
from vakr.collectors.snmp.stage2 import Stage2Result
from vakr.config.loader import EffectiveConfig
from vakr.discovery.model import DiscoveredInterface, DiscoveryResult, SystemFacts
from vakr.domain.enums import CounterFamily, IfAdminStatus, IfOperStatus, SnmpState, SnmpVersion
from vakr.domain.errors import ValidationError
from vakr.domain.quality import Quality
from vakr.ports.snmp import SnmpError, SnmpErrorKind, SnmpStats, ValueKind, VarBind

AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_plan_entries_validate_and_learn_capabilities() -> None:
    """SNMP-006, SNMP-010: counters need status checks; learned limits stick until re-probe.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        InterfacePlan(3, check_status=False)
    with pytest.raises(ValidationError):
        PollPlan((InterfacePlan(3), InterfacePlan(3)))
    with pytest.raises(ValidationError):
        InterfacePlan(0)
    plan = PollPlan((InterfacePlan(1), InterfacePlan(2)))
    learned = plan.with_changes(
        [
            CapabilityChange(1, "hc_octets", False),
            CapabilityChange(2, "hc_packets", False),
            CapabilityChange(2, "discontinuity", False),
            CapabilityChange(9, "hc_octets", False),
        ]
    )
    one, two = learned.interfaces
    assert one.family is CounterFamily.LEGACY32
    assert (two.hc_packets, two.discontinuity) == (False, False)
    reset = learned.reprobe().interfaces
    assert all(e.family is CounterFamily.HC64 and e.hc_packets and e.discontinuity for e in reset)
    assert reprobe_due(AT, AT + timedelta(days=7), timedelta(days=7))
    assert not reprobe_due(AT, AT + timedelta(days=6), timedelta(days=7))


def test_plan_from_discovery_tries_unknown_capabilities() -> None:
    """SNMP-010: a failed probe is tried again, never assumed unsupported.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    interfaces = (
        DiscoveredInterface(1, hc_octets=True, hc_packets=True, discontinuity=False),
        DiscoveredInterface(2, hc_octets=False),
        DiscoveredInterface(3),
    )
    result = DiscoveryResult("sim", AT, AT, 0.0, SystemFacts(), interfaces, ())
    plan = plan_from_discovery(result, collect=lambda i: i.if_index != 3)
    families = [e.family for e in plan.interfaces]
    assert families == [CounterFamily.HC64, CounterFamily.LEGACY32, CounterFamily.HC64]
    assert [e.discontinuity for e in plan.interfaces] == [False, True, True]
    assert [e.collect for e in plan.interfaces] == [True, True, False]


def _counters(entry: InterfacePlan, kind: ValueKind, start: int = 100) -> dict:
    """Answer every counter OID of ``entry`` with distinct values.

    Args:
        entry: The interface plan.
        kind: The value kind for octets and packets.
        start: First value.

    Returns:
        dict: Varbinds by OID.

    Raises:
        None
    """
    oids = counter_oids(entry)
    got = {}
    for n, oid in enumerate(oids.all()):
        is_error = oid in (oids.in_errors, oids.out_errors)
        value_kind = ValueKind.COUNTER32 if is_error else kind
        if oid == oids.discontinuity:
            value_kind = ValueKind.TIMETICKS
        got[oid] = VarBind(oid, value_kind, start + n)
    return got


def test_snmp_t06_packet_components_sum_exactly_and_are_not_kept() -> None:
    """SNMP-T06, SNMP-008: six components give exact aggregate totals.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    entry = InterfacePlan(4)
    values = counter_values(counter_oids(entry), _counters(entry, ValueKind.COUNTER64))
    # Order: in/out octets (100, 101), in ucast/mcast/bcast (102-104), out (105-107),
    # errors (108, 109), discontinuity (110).
    assert values == {
        "in_octets": 100, "out_octets": 101, "in_packets": 102 + 103 + 104,
        "out_packets": 105 + 106 + 107, "in_errors": 108, "out_errors": 109,
        "discontinuity_ticks": 110,
    }  # fmt: skip
    assert len(counter_oids(InterfacePlan(4, hc_packets=False)).all()) == 5


def test_counters_of_the_wrong_type_or_a_missing_component_are_missing() -> None:
    """Invariant 1: a 32-bit value under an HC OID, or a missing component, is not data.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    entry = InterfacePlan(4)
    oids = counter_oids(entry)
    wrong = counter_values(oids, _counters(entry, ValueKind.COUNTER32))
    assert wrong["in_octets"] is None
    assert wrong["in_packets"] is None
    assert wrong["in_errors"] == 108
    partial = _counters(entry, ValueKind.COUNTER64)
    partial[oids.in_packets[2]] = VarBind(oids.in_packets[2], ValueKind.NO_SUCH_INSTANCE)
    values = counter_values(oids, partial)
    assert values["in_packets"] is None
    assert values["out_packets"] == 105 + 106 + 107
    legacy = InterfacePlan(4, family=CounterFamily.LEGACY32, discontinuity=False)
    assert counter_values(counter_oids(legacy), _counters(legacy, ValueKind.COUNTER32))["in_octets"]


def test_batches_keep_interfaces_whole_and_learn_absent_counters() -> None:
    """SNMP-017, SNMP-010: batching never splits an interface; noSuch answers are learned.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    entries = [InterfacePlan(i, discontinuity=False) for i in range(1, 8)]
    assert [len(b) for b in pack(entries, 30)] == [3, 3, 1]
    assert len(first_batch(entries, 5)) == 1
    entry = InterfacePlan(12)
    oids = counter_oids(entry)
    absent = {oid: VarBind(oid, ValueKind.NO_SUCH_INSTANCE) for oid in oids.all()}
    changes = learn(entry, oids, absent)
    assert {(c.capability, c.supported) for c in changes} == {
        ("hc_octets", False),
        ("discontinuity", False),
    }
    packets_only = _counters(entry, ValueKind.COUNTER64) | {
        oid: VarBind(oid, ValueKind.NO_SUCH_OBJECT) for oid in (*oids.in_packets, *oids.out_packets)
    }
    assert [c.capability for c in learn(entry, oids, packets_only)] == ["hc_packets"]


def _poll(
    stats: SnmpStats, stage1: Stage1Result, quality: Quality = Quality.NONE
) -> SnmpPollResult:
    """Build a poll result around a Stage 1 result.

    Args:
        stats: Effort counts.
        stage1: The Stage 1 result.
        quality: The poll's quality.

    Returns:
        SnmpPollResult: The result.

    Raises:
        None
    """
    return SnmpPollResult(
        "sim", SnmpVersion.V2C, AT, 0.0, 0.1, stage1, Stage2Result(()), stats, quality
    )


@pytest.mark.parametrize(
    ("responses", "uptime", "error", "quality", "state"),
    [
        (0, None, SnmpError(SnmpErrorKind.TIMEOUT, "x"), Quality.NONE, SnmpState.TIMEOUT),
        (0, None, SnmpError(SnmpErrorKind.TRANSPORT, "x"), Quality.NONE, SnmpState.UNKNOWN),
        (
            1,
            None,
            SnmpError(SnmpErrorKind.AGENT_ERROR, "x", status=5),
            Quality.NONE,
            SnmpState.ERROR,
        ),
        (1, 5, None, Quality.PARTIAL_RESPONSE, SnmpState.PARTIAL),
        (1, 5, None, Quality.MISSING_FIELDS, SnmpState.RESPONDING),
    ],
)
def test_snmp_state_separates_device_and_collector_failures(
    responses: int, uptime: int | None, error: SnmpError | None, quality: Quality, state: SnmpState
) -> None:
    """Invariant 2, SNMP-013: timeouts, collector trouble, refusals, and partial answers differ.

    Args:
        responses: Responses received.
        uptime: ``sysUpTime`` read.
        error: Stage 1's error.
        quality: The poll's quality.
        state: The expected state.

    Returns:
        None

    Raises:
        None
    """
    stage1 = Stage1Result(AT, 0.0, uptime, (InterfaceStatus(1, None, None),), error=error)
    stats = SnmpStats(requests=2, responses=responses, timeouts=2 - responses)
    assert snmp_state(_poll(stats, stage1, quality)) is state


def test_up_up_needs_both_statuses_and_config_maps_snmp_keys() -> None:
    """SNMP-006 and the ``[snmp]`` keys: unknown status is never up/up; config maps cleanly.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert InterfaceStatus(1, IfAdminStatus.UP, IfOperStatus.UP).up_up
    assert not InterfaceStatus(1, IfAdminStatus.UP, None).up_up
    assert not InterfaceStatus(1, IfAdminStatus.UP, IfOperStatus.DORMANT).up_up
    cfg = snmp_config(
        EffectiveConfig(
            {
                "snmp": {
                    "timeout": "5s",
                    "retries": 0,
                    "port": 1161,
                    "capability_reprobe_interval": "1h",
                }
            }
        )
    )
    assert (cfg.settings.timeout_s, cfg.settings.retries, cfg.port) == (5.0, 0, 1161)
    assert cfg.capability_reprobe_interval == timedelta(hours=1)
    assert snmp_config(EffectiveConfig({})).settings.max_varbinds == 30
