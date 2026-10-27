"""The acceptance reports' rollup payload summary agrees with the full decoder (E.35, ADR-021).

``payload_summary`` and the bucket reader's inline path skip a payload's
values for speed; whatever the payload's shape (shared or separate counts,
valid counts below the sample count, multi-byte varints), they must give
the largest sample count, the presence bits, and the quality union that
``decode_payload`` gives.

Dependencies:
    pytest; vakr.adapters.sqlite.telemetry.rollup_codec, rollup_summary, acceptance.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_rollup_summary.py
"""

from functools import reduce

import pytest

from vakr.adapters.sqlite.telemetry.rollup_codec import Aggregates, decode_payload, encode_payload
from vakr.adapters.sqlite.telemetry.rollup_summary import bucket_mark, payload_summary
from vakr.domain.errors import StorageError


def full(expected: int, sample: int, valid: int, quality: int) -> Aggregates:
    """Build one metric's aggregates.

    Args:
        expected: Expected count.
        sample: Sample count.
        valid: Valid count.
        quality: Flags.

    Returns:
        Aggregates: With values exactly when ``valid > 0``.

    Raises:
        None
    """
    values = (1.5, 2.0, 1e9, 3e12) if valid else (None, None, None, None)
    return Aggregates(expected, sample, valid, values[0], values[1], values[2], values[3], quality)


CASES = [
    {1: full(15, 15, 15, 0), 2: full(15, 15, 15, 0), 3: full(15, 15, 15, 0)},
    {1: full(15, 14, 14, 1 << 10), 3: full(15, 14, 0, 1 << 10), 6: full(15, 14, 14, 1 << 10)},
    {1: full(15, 0, 0, 4), 2: full(15, 0, 0, 4)},
    {1: full(300, 200, 200, 1 << 22), 2: full(300, 200, 200, 1 << 22)},
    {2: full(15, 12, 12, 0), 5: full(15, 13, 13, 1 << 16)},
]


@pytest.mark.parametrize("slots", CASES)
def test_e35_summary_matches_the_full_decoder(slots: dict[int, Aggregates]) -> None:
    """E.35: samples, presence, and flags equal the full decode for every payload shape.

    Args:
        slots: Slot to aggregates.

    Returns:
        None

    Raises:
        None
    """
    payload = encode_payload(slots, quantize=True)
    decoded = decode_payload(payload)
    expected = (
        max(a.sample_count for a in decoded.values()),
        sum(1 << (slot - 1) for slot, a in decoded.items() if a.valid_count > 0),
        reduce(lambda q, a: q | a.quality, decoded.values(), 0),
    )
    assert payload_summary(payload) == expected
    mark = bucket_mark(0, payload)
    assert (mark.samples, mark.available, int(mark.quality)) == expected


def test_e35_damaged_payload_is_refused() -> None:
    """ADR-021: a truncated payload raises ``StorageError``, never a guess.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(StorageError):
        payload_summary(b"\x01")
    with pytest.raises(StorageError):
        payload_summary(bytes((3, 0, 0, 15)))
