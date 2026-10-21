"""Decoding the rollup job's interface rows from the SQLite telemetry store (RET-004).

The rollup source reads interface rows as twelve columns, not whole
observations: keys, time, identity, device, the six metric values, and the
quality flags (``IFACE_COLUMNS``). These helpers turn such rows into the
port's ``InterfaceSample`` read models and group them per interface and
collector.

Dependencies:
    vakr.adapters.sqlite.encoding, telemetry.codec_rows: Stored-value decoding.
    vakr.ports.telemetry.rollup_source: The read models.

Safety:
    Pure decoding; a damaged value raises ``StorageError``, and ``NULL``
    stays ``None``, never zero (invariant 1).

Example:
    >>> stored_real(None), stored_real(3)
    (None, 3.0)
"""

from collections import defaultdict
from collections.abc import Callable
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time
from vakr.adapters.sqlite.telemetry.codec_rows import stored_int
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId
from vakr.domain.quality import Quality
from vakr.ports.telemetry.rollup_source import InterfaceSample, InterfaceSamples

IFACE_COLUMNS = ("series_key", "collector_key", "ts_us", "observation_uuid", "device_key",
                 "in_bps", "out_bps", "in_pps", "out_pps", "in_error_rate", "out_error_rate",
                 "quality_flags")  # fmt: skip
"""Interface columns a rollup reads; the metric values are in ``INTERFACE_METRICS`` order."""

type Key = tuple[int, int]
"""``(series key, collector key)``."""


def row_key(row: tuple[object, ...]) -> Key:
    """Return a row's ``(series key, collector key)``.

    Args:
        row: A row whose first two columns are the keys.

    Returns:
        Key: The keys.

    Raises:
        StorageError: If either is not an integer.
    """
    return stored_int(row[0]), stored_int(row[1])


def stored_real(value: object) -> float | None:
    """Return a stored ``REAL`` metric, keeping ``NULL`` as ``None`` (invariant 1).

    Args:
        value: Column value.

    Returns:
        float | None: The value.

    Raises:
        StorageError: If the value is not a number.
    """
    if value is None or type(value) is float:  # every STRICT REAL value; checked first for speed
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    raise StorageError("a stored interface rate is not a number", retryable=False)


def iface_sample(row: tuple[object, ...]) -> InterfaceSample:
    """Decode one ``_IFACE`` row into a rollup sample.

    Args:
        row: The row.

    Returns:
        InterfaceSample: The sample.

    Raises:
        StorageError: If the row is damaged.
    """
    values = tuple(map(stored_real, row[5:11]))
    return InterfaceSample(decode_time(row[2]), blob_to_uuid(row[3]), values,
                           Quality(stored_int(row[11])))  # fmt: skip


def group_interfaces(
    uuid_of: Callable[[str, int], UUID], rows: list[tuple[object, ...]]
) -> list[InterfaceSamples]:
    """Group interface rows into per-interface, per-collector samples, in UUID order.

    Args:
        uuid_of: Resolves ``(dictionary, key)`` to a UUID.
        rows: Rows with the ``_IFACE`` columns.

    Returns:
        list[InterfaceSamples]: The items.

    Raises:
        StorageError: If a row is damaged or a key is missing from its dictionary.
    """
    groups: dict[Key, list[InterfaceSample]] = defaultdict(list)
    devices: dict[Key, tuple[InterfaceSample, int]] = {}
    for row in rows:
        key, sample = row_key(row), iface_sample(row)
        groups[key].append(sample)
        newest = devices.get(key)
        if newest is None or (sample.timestamp, sample.observation_id) > (
            newest[0].timestamp,
            newest[0].observation_id,
        ):
            devices[key] = (sample, stored_int(row[4]))
    items = [
        InterfaceSamples(uuid_of("interface", key[0]), CollectorId(uuid_of("collector", key[1])),
                         uuid_of("device", devices[key][1]),
                         tuple(sorted(found, key=lambda x: (x.timestamp, x.observation_id))))
        for key, found in groups.items()
    ]  # fmt: skip
    return sorted(items, key=lambda i: (str(i.interface_id), str(i.collector_id)))
