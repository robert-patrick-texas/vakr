"""Rollup rows reduced to counts for the acceptance reports: sample count, presence, quality (E.35).

A rollup payload packs every metric of one subject, collector, and bucket
(``rollup_codec``). The acceptance reports need only how many samples a
bucket summarises, which metrics had a valid sample, and the union of the
quality flags, so ``payload_summary`` steps over the values instead of
decoding them, and ``bucket_rows`` reads one month partition's 15-minute
rows of one subject.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.payload_reader: Sequential decoding.

Safety:
    Read-only; table names are checked catalog names, values are bound.

Example:
    >>> payload_summary(bytes((1, 0, 1, 15, 15, 0, 0)))
    (15, 1, 0)
"""

# The query interpolates a checked partition name only; values are bound parameters.
# ruff: noqa: S608

import sqlite3

from vakr.adapters.sqlite.telemetry.payload_reader import PayloadReader
from vakr.domain.errors import StorageError

_US = 1_000_000


def payload_summary(payload: object) -> tuple[int, int, int]:
    """Return a rollup payload's largest sample count, presence bits, and quality union.

    Args:
        payload: The stored blob (see ``rollup_codec``).

    Returns:
        tuple[int, int, int]: Samples, bit ``slot - 1`` per metric with valid
        samples, and the union of the metrics' quality flags.

    Raises:
        StorageError: If the payload is truncated or malformed.
    """
    if not isinstance(payload, bytes) or len(payload) < 3:
        raise StorageError("a stored rollup payload is damaged", retryable=False)
    present, shared, valid_is_sample = payload[0], payload[1], payload[2]
    reader = PayloadReader(payload, 3)
    samples = available = quality = sample = 0
    for slot in range(8):
        bit = 1 << slot
        if not present & bit:
            continue
        if not shared & bit:
            reader.varint()
            sample = reader.varint()
            quality |= reader.varint()
        valid = sample if valid_is_sample & bit else reader.varint()
        if valid > 0:
            reader.skip_values()
            available |= bit
        samples = max(samples, sample)
    if not reader.done():
        raise StorageError("a stored rollup payload has trailing bytes", retryable=False)
    return samples, available, quality


def bucket_rows(
    conn: sqlite3.Connection, name: str, month_us: int, keys: tuple[int, int],
    bounds: tuple[int, int], descending: bool,
) -> list[tuple[int, bytes]]:  # fmt: skip
    """Fetch one month partition's 15-minute rows of a subject and collector in the window.

    Args:
        conn: Connection inside the read transaction.
        name: Checked partition name.
        month_us: The partition's start.
        keys: Series and collector keys.
        bounds: ``(start_us, end_us)`` on the bucket start.
        descending: Newest first.

    Returns:
        list[tuple[int, bytes]]: ``(bucket offset in seconds, payload)`` rows.

    Raises:
        sqlite3.Error: If the query fails.
    """
    order = "DESC" if descending else "ASC"
    return conn.execute(
        f"SELECT bucket_offset_s, payload FROM {name} WHERE series_key = :s "
        "AND collector_key = :c AND width_adj_s = 0 AND bucket_offset_s >= :lo "
        f"AND bucket_offset_s < :hi ORDER BY bucket_offset_s {order}",
        {"s": keys[0], "c": keys[1],
         "lo": -(-(bounds[0] - month_us) // _US), "hi": -(-(bounds[1] - month_us) // _US)},
    ).fetchall()  # fmt: skip
