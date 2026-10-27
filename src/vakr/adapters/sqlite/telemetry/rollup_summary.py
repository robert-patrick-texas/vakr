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
from vakr.domain.quality import Quality
from vakr.ports.telemetry.acceptance import BucketMark

_US = 1_000_000


def _shared_summary(payload: bytes) -> tuple[int, int, int] | None:
    """Read the common payload in which every metric shares the first one's counts.

    A bucket's metrics summarise the same observations, so they usually share
    the expected count, sample count, and quality, and every sample is valid.
    Then the first three varints after the header answer for the whole row and
    no value needs stepping over, which keeps a 14-day scan of 1,600 interfaces
    within the field kit's two minutes.

    Args:
        payload: The stored blob.

    Returns:
        tuple[int, int, int] | None: As ``payload_summary``; ``None`` when not that shape.

    Raises:
        StorageError: If the payload ends early.
    """
    present, shared, valid_is_sample = payload[0], payload[1], payload[2]
    first = present & -present
    if not present or shared != present & ~first or valid_is_sample != present:
        return None
    if len(payload) > 5 and payload[3] < 0x80 and payload[4] < 0x80 and payload[5] < 0x80:
        sample = payload[4]  # one-byte varints: expected, sample, quality
        return sample, present if sample > 0 else 0, payload[5]
    reader = PayloadReader(payload, 3)
    reader.varint()
    sample, quality = reader.varint(), reader.varint()
    return sample, present if sample > 0 else 0, quality


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
    if (common := _shared_summary(payload)) is not None:
        return common
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


_QUALITY: dict[int, Quality] = {}


def quality_of(bits: int) -> Quality:
    """Return the ``Quality`` of stored bits, built once per distinct value.

    A report reads millions of rows that share a handful of flag values, and
    building an ``IntFlag`` costs more than the rest of a row.

    Args:
        bits: The stored integer.

    Returns:
        Quality: The flags.

    Raises:
        ValueError: If the bits are not a valid ``Quality``.
    """
    found = _QUALITY.get(bits)
    if found is None:
        found = _QUALITY.setdefault(bits, Quality(bits))
    return found


def bucket_mark(start: int, payload: bytes) -> BucketMark:
    """Build one bucket mark, reading the common one-byte payload inline.

    Args:
        start: Bucket start.
        payload: The stored blob.

    Returns:
        BucketMark: The mark.

    Raises:
        StorageError: If the payload is damaged.
    """
    if len(payload) > 5 and payload[2] == payload[0] and payload[3] < 0x80:
        present = payload[0]
        shared_rest = payload[1] == present & ~(present & -present)
        if present and shared_rest and payload[4] < 0x80 and payload[5] < 0x80:
            sample = payload[4]  # as rollup_summary's shared case, without the calls
            return BucketMark(start, sample, present if sample else 0, quality_of(payload[5]))
    samples, available, quality = payload_summary(payload)
    return BucketMark(start, samples, available, quality_of(quality))
