"""In-memory adapter behaviour the shared suites leave out on purpose.

The shared suites never overlap two writing units of work, because SQLite
serialises writers. The memory adapter allows it and must then detect the
conflict at commit time (DB-003), which these tests prove.

Dependencies:
    pytest; vakr.adapters.memory; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/contract/memory/test_memory_specific.py
"""

from dataclasses import replace

import pytest

from tests.contract import contract_samples as s
from vakr.adapters.memory import MemoryTelemetry, MemoryTelemetryStore, memory_uow_factory
from vakr.adapters.memory.paging import paginate
from vakr.domain.errors import ConflictError, DuplicateError, ValidationError
from vakr.domain.inventory import API_MANAGED
from vakr.domain.paging import PageRequest, encode_cursor


def test_db003_overlapping_writers_conflict_at_commit() -> None:
    """DB-003: the second of two overlapping edits fails at commit and changes nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    dev = s.device()
    with factory() as uow:
        uow.devices.add(dev)
        uow.commit()
    with factory() as alice, factory() as bob:
        alice.devices.update(replace(dev, comment="alice"), writer=API_MANAGED)
        bob.devices.update(replace(dev, comment="bob"), writer=API_MANAGED)
        bob.commit()
        with pytest.raises(ConflictError):
            alice.commit()
        alice.commit()  # the failed commit discarded alice's change; nothing left to apply
    with factory() as uow:
        stored = uow.devices.get(dev.id)
    assert stored is not None
    assert stored.comment == "bob"


def test_db001_overlapping_adds_detect_duplicates_at_commit() -> None:
    """DB-001: two units of work adding the same ID or name: the later commit fails.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    factory = memory_uow_factory()
    dev = s.device("same")
    with factory() as first, factory() as second, factory() as third:
        first.devices.add(dev)
        second.devices.add(s.device("same"))
        third.devices.add(replace(dev, name="other"))
        first.commit()
        with pytest.raises(DuplicateError):
            second.commit()
        with pytest.raises(DuplicateError):
            third.commit()


def test_forged_cursor_with_wrong_types_is_rejected() -> None:
    """API-005: a well-formed cursor whose parts have the wrong types is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    forged = PageRequest(cursor=encode_cursor((7,)))
    with pytest.raises(ValidationError):
        paginate(["a", "b"], lambda v: (v,), forged)
    assert paginate([], lambda v: (v,), forged).items == ()


def test_memory_backend_views_share_one_store() -> None:
    """Each view of a store sees the same data, as reopened SQL connections would.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = MemoryTelemetryStore(max_batch_rows=10)
    assert MemoryTelemetry(store).capabilities is MemoryTelemetry(store).capabilities
    assert MemoryTelemetry(store).capabilities.max_batch_rows == 10
