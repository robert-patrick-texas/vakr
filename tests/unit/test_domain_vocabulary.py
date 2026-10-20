"""Contract tests for the shared domain vocabulary (ARCH-002).

Stored values and flag bit positions are part of the language-neutral
observation contract, so these tests pin them: changing one must be a
deliberate schema decision, not an accident.

Dependencies:
    vakr.domain: The enums and quality flags under test.

Example:
    $ uv run pytest tests/unit/test_domain_vocabulary.py
"""

from vakr.domain.enums import AdminState, IcmpState, IfOperStatus, InterestClass, SnmpState
from vakr.domain.quality import BASELINE_INVALIDATING, UNKNOWN_CONTEXT, Quality


def test_stored_values_are_stable_lowercase_strings() -> None:
    """Enum values are what storage and the API persist; they must not drift.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert [s.value for s in IcmpState] == ["reachable", "impaired", "unreachable", "unknown"]
    assert SnmpState.PARTIAL.value == "partial"
    assert AdminState.MAINTENANCE.value == "maintenance"
    assert InterestClass("primary") is InterestClass.PRIMARY
    assert IfOperStatus.LOWER_LAYER_DOWN == 7


def test_quality_bit_positions_are_pinned_and_unique() -> None:
    """Bit positions are append-only; a Go collector writes the same integers.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    pinned = {
        Quality.PARTIAL_LOSS: 0,
        Quality.SINGLE_REPLY: 1,
        Quality.NOT_ATTEMPTED: 2,
        Quality.BASELINE_FIRST_SAMPLE: 7,
        Quality.BASELINE_IDENTITY_UNRESOLVED: 14,
        Quality.RATE_EXCEEDS_CAPACITY: 16,
        Quality.RETRIED: 22,
    }
    for flag, bit in pinned.items():
        assert int(flag) == 1 << bit
    values = [int(q) for q in Quality if q is not Quality.NONE]
    assert len(values) == len(set(values))
    assert all(v & (v - 1) == 0 for v in values)


def test_flag_groups() -> None:
    """Baseline flags invalidate rates; context flags make state unknown.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert Quality.BASELINE_GAP in BASELINE_INVALIDATING
    assert Quality.COUNTER_WRAP_32 not in BASELINE_INVALIDATING
    assert Quality.NOT_ATTEMPTED in UNKNOWN_CONTEXT
    assert Quality.PARTIAL_LOSS not in UNKNOWN_CONTEXT
