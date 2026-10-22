"""Operation records and the request service: rules, transitions, races (API-011, E.32).

The record refuses fields that do not fit its state and moves one way
only; the request service refuses devices no collector would discover,
cleans requester names, coalesces a racing request onto the winner's
operation, and leaves an operation a collector claimed meanwhile alone
when it gives up stale ones. ``result_of`` turns a write's counts into
what the administrator sees (DISC-T03).

Dependencies:
    pytest; the memory metadata adapter; tests.contract samples.

Example:
    $ uv run pytest tests/unit/operations/test_operations_service.py
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.contract import contract_samples as s
from tests.contract.contract_metadata_operations import operation
from tests.contract.contract_samples import T0
from vakr.adapters.memory import memory_uow_factory
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import AdminState, ReachabilityStrategy
from vakr.domain.errors import ConflictError, DuplicateError, ValidationError
from vakr.domain.ids import CollectorId, new_id
from vakr.domain.operations import MAX_ERROR_LENGTH, Operation, OperationState, clip
from vakr.operations import (
    CollectorOperations,
    OperationService,
    clean_actor,
    limits,
    operation_service,
    refusal,
)
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import MetadataUnitOfWork


def test_e32_records_fit_their_state_and_move_one_way() -> None:
    """API-011: a queued record has no start; transitions refuse the wrong state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    device = s.device()
    queued = operation(device.id)
    with pytest.raises(ValidationError, match="started_at"):
        replace(queued, started_at=T0)
    with pytest.raises(ValidationError, match="error"):
        replace(queued, error="no")
    with pytest.raises(ValidationError, match="requested_by"):
        replace(queued, requested_by="x" * 200)
    with pytest.raises(ValidationError, match="not running"):
        queued.failed(T0, "no")
    running = queued.claimed(new_id(CollectorId), T0)
    with pytest.raises(ValidationError, match="not queued"):
        running.expired(T0, "late")
    done = running.failed(T0 + timedelta(seconds=1), "x" * 900)
    assert done.state is OperationState.FAILED
    assert done.error is not None
    assert len(done.error) == MAX_ERROR_LENGTH
    assert (done.version, done.active) == (3, False)
    assert clip("  two\nlines  ") == "two lines"
    assert clip("") == "failed"


def test_disc003_refusal_and_requester_names() -> None:
    """DISC-003, SEC-018: who may be rediscovered; names are stored on one bounded line.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    snmp = s.device(credential_ref="lab")
    gated = ReachabilityStrategy.ICMP_GATED
    assert refusal(snmp, gated) is None
    assert refusal(replace(snmp, admin_state=AdminState.MAINTENANCE), gated) is None
    assert "icmp_only" in (refusal(snmp, ReachabilityStrategy.ICMP_ONLY) or "")
    assert refusal(replace(snmp, reachability=ReachabilityStrategy.SNMP_FALLBACK),
                   ReachabilityStrategy.ICMP_ONLY) is None  # fmt: skip
    assert clean_actor(" local:root\t") == "local:root"
    assert clean_actor("\x00") == "unknown"
    assert len(clean_actor("a" * 400)) == 128
    config = EffectiveConfig({"polling": {"reachability_strategy": "icmp_only"}})
    uow = memory_uow_factory()
    quiet = operation_service(uow, ManualClock(T0), config)
    with uow() as tx:
        tx.devices.add(snmp)
        tx.commit()
    with pytest.raises(Exception, match="icmp_only"):
        quiet.request_rediscovery(snmp.id, actor="alice")
    odd = operation_service(uow, ManualClock(T0), EffectiveConfig({"polling": {
        "reachability_strategy": "sometimes"}}))  # fmt: skip
    assert odd.request_rediscovery(snmp.id, actor="alice").created


def test_a_racing_request_follows_the_winners_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """E.32: a request that loses the one-active race answers with the winner's operation.

    Args:
        monkeypatch: Makes the first write collide.

    Returns:
        None

    Raises:
        None
    """
    uow, clock = memory_uow_factory(), ManualClock(T0)
    device = s.device(credential_ref="lab")
    winner = operation(device.id, requested_at=T0)
    with uow() as tx:
        tx.devices.add(device)
        tx.commit()
    service = OperationService(uow, clock)

    def collide(*_args: object) -> object:
        """Store the winner, then fail as the losing write would.

        Args:
            *_args: Ignored.

        Returns:
            object: Never.

        Raises:
            DuplicateError: Always.
        """
        with uow() as tx:
            tx.operations.add(winner)
            tx.commit()
        raise DuplicateError("operation", "active device", device.id)

    monkeypatch.setattr(service, "_request", collide)
    asked = service.request_rediscovery(device.id, actor="alice")
    assert (asked.operation, asked.created) == (winner, False)


def test_expiry_leaves_a_claimed_operation_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """E.32: if a collector claims a stale operation as it expires, the claim wins.

    Args:
        monkeypatch: Makes the expiry's commit collide.

    Returns:
        None

    Raises:
        None
    """
    uow, clock = memory_uow_factory(), ManualClock(T0)
    device, east = s.device(credential_ref="lab"), s.collector()
    stale = operation(device.id, requested_at=T0)
    with uow() as tx:
        tx.devices.add(device)
        tx.collectors.add(east)
        tx.operations.add(stale)
        tx.commit()
    clock.advance(limits.PICKUP_LIMIT + timedelta(seconds=1))
    runs = CollectorOperations(uow, clock, east.id)
    service = OperationService(uow, clock)
    real = service._live

    def claimed_meanwhile(tx: MetadataUnitOfWork, op: Operation | None) -> Operation | None:
        """Let the collector claim first, then try to expire.

        Args:
            tx: The unit of work.
            op: The stale operation.

        Returns:
            Operation | None: As ``_live``.

        Raises:
            ConflictError: From the expiry, as the collector moved the version.
        """
        assert runs.claim(stale) is not None
        return real(tx, op)

    monkeypatch.setattr(service, "_live", claimed_meanwhile)
    assert service.expire_stale() == 0
    with uow() as tx:
        kept = tx.operations.get(stale.id)
    assert kept is not None
    assert kept.state is OperationState.RUNNING
    with uow() as tx, pytest.raises(ConflictError):
        tx.operations.update(stale.expired(T0, "late"))  # the version moved with the claim
