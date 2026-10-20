"""Observation invariants (4.3 invariants 1, 2, 5, 11; ICMP-003..005; RATE-004; 9.1; 10.1-10.4).

Dependencies:
    pytest; vakr.domain; tests.contract.contract_samples for builders.

Example:
    $ uv run pytest tests/unit/domain/test_domain_observations.py
"""

from datetime import datetime, timedelta
from typing import Any, cast

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import (
    AddressFamily,
    BaselineState,
    CounterFamily,
    ObservationType,
    PollExecution,
)
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.observations import observation_subject, observation_type
from vakr.domain.quality import Quality

DEV, IFACE, COL = new_id(DeviceId), new_id(InterfaceId), new_id(CollectorId)


def test_icmp_counts_are_derived_from_rtts() -> None:
    """ICMP-003, RET-008: sent, received, and loss come from the per-probe RTTs.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    obs = s.icmp(DEV, COL, s.T0)
    assert (obs.sent, obs.received, obs.loss_pct) == (5, 4, 20.0)
    assert observation_type(obs) is ObservationType.ICMP
    assert observation_subject(obs) == DEV


@pytest.mark.parametrize(
    "overrides",
    [
        {"rtts_us": ()},
        {"rtts_us": [900]},
        {"rtts_us": (900, -1, 1000, 1000, 1000)},
        {"rtts_us": (None,) * 5},
        {"avg_rtt_us": 2000},
        {"median_rtt_us": 10},
        {"target_address": "2001:db8::1"},
        {"target_address": "router1"},
        {"target_address": 7},
        {"source_address": "192.0.2.010"},
        {"timestamp": datetime(2026, 10, 1)},
        {"quality": 3},
        {"state": "reachable"},
        {"address_family": AddressFamily.IPV6},
    ],
)
def test_icmp_invariants(overrides: dict[str, Any]) -> None:
    """ICMP-004/005, invariant 11: impossible ICMP results cannot be built.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.icmp(DEV, COL, s.T0, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"response_count": 4},
        {"timeout_count": 4},
        {"request_count": -1},
        {"sys_uptime_ticks": 1 << 32},
        {"error_code": ""},
        {"snmp_version": "1"},
    ],
)
def test_snmp_invariants(overrides: dict[str, Any]) -> None:
    """10.2, SNMP-014: impossible SNMP exchanges cannot be built.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.snmp(DEV, COL, s.T0, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"baseline_state": BaselineState.NO_BASELINE},
        {"baseline_state": BaselineState.INVALIDATED, "in_bps": None, "out_bps": None,
         "in_error_delta": 0},
        {"quality": Quality.BASELINE_GAP},
        {"counter_family": CounterFamily.LEGACY32},
        {"in_errors": -1},
        {"in_bps": float("nan")},
        {"in_bps": 5},
        {"sample_elapsed_us": 0},
        {"counter_discontinuity_ticks": 1 << 32},
        {"if_index": 0},
        {"admin_status": 1},
    ],
)  # fmt: skip
def test_interface_sample_invariants(overrides: dict[str, Any]) -> None:
    """Invariants 1 and 5, RATE-004: no rate without a valid baseline; counters fit their width.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.sample(DEV, IFACE, COL, s.T0, **overrides)


def test_interface_sample_without_baseline_keeps_raw_counters() -> None:
    """Invariant 1: a first sample stores raw counters and no rates, flagged.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    first = s.sample(DEV, IFACE, COL, s.T0, baseline_state=BaselineState.NO_BASELINE,
                     quality=Quality.BASELINE_FIRST_SAMPLE, in_bps=None, out_bps=None)  # fmt: skip
    assert first.in_octets is not None
    assert first.in_bps is None
    assert observation_subject(first) == IFACE


@pytest.mark.parametrize(
    "overrides",
    [
        {"completed_at": None},
        {"execution": PollExecution.NOT_ATTEMPTED},
        {"execution": PollExecution.RUNNING},
        {"execution": PollExecution.SCHEDULED, "started_at": None},
        {"completed_at": s.T0 - s.MINUTE},
        {"effective_interval_s": 0},
        {"worker_id": "w1"},
    ],
)
def test_device_poll_invariants(overrides: dict[str, Any]) -> None:
    """9.1, SCHED-017: poll times must agree with how far the poll got.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.poll(DEV, COL, s.T0, **overrides)


def test_device_poll_states_and_timestamp() -> None:
    """9.1, invariant 10: a poll's timestamp is its scheduled time; running polls have no end.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    running = s.poll(DEV, COL, s.T0, execution=PollExecution.RUNNING, completed_at=None)
    missed = s.poll(DEV, COL, s.T0, execution=PollExecution.MISSED_OVERRUN, started_at=None,
                    completed_at=None, quality=Quality.NOT_ATTEMPTED)  # fmt: skip
    assert running.timestamp == s.T0
    assert missed.started_at is None
    assert observation_type(running) is ObservationType.DEVICE_POLL


@pytest.mark.parametrize(
    "overrides",
    [
        {"queue_depth": 101},
        {"spool_used_pct": 100.5},
        {"spool_used_pct": 50},
        {"event_loop_lag_us": -1},
        {"software_version": ""},
        {"state": "healthy"},
    ],
)
def test_collector_health_invariants(overrides: dict[str, Any]) -> None:
    """OBS-001, OBS-003: impossible health snapshots cannot be built.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.health(COL, s.T0, **overrides)


def test_observation_helpers_reject_other_types() -> None:
    """ARCH-002: only the five observation types have an envelope type.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    health = s.health(COL, s.T0 + timedelta(seconds=1), clock_offset_us=-5, queue_capacity=None)
    assert observation_subject(health) == COL
    assert health.queue_depth == 3
    with pytest.raises(ValidationError):
        observation_type(cast(Any, s.device()))
