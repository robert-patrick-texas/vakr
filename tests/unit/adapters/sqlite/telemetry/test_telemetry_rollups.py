"""Rollup storage: replacement by identity, precision, widths, and the packed payload.

Covers RET-009 (rewriting a bucket replaces that metric and nothing else),
RET-004/006 (every aggregate and count survives), ROLL-T05 (a bucket with
no valid sample keeps its counts and no values), and the E.21 precision
rule: interface aggregates are kept to float32 precision when they need
more than four bytes exactly (ADR-021 amendment 3), ICMP aggregates exactly.

Dependencies:
    pytest, random; vakr.adapters.sqlite.telemetry; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_rollups.py
"""

import random
from pathlib import Path
from typing import cast

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import (
    BUCKET,
    T0,
    at,
    bucket,
    open_store,
    raw,
    write,
)
from vakr.adapters.sqlite.telemetry import SqliteTelemetry
from vakr.adapters.sqlite.telemetry.rollup_codec import Aggregates, decode_payload, encode_payload
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.paging import PageRequest
from vakr.domain.quality import Quality
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.state import RollupBucket

DAY = TimeWindow(T0, at(1))


def test_ret009_rewrite_replaces_one_metric_only(tmp_path: Path) -> None:
    """RET-009: rewriting a bucket's identity replaces it; the row's other metrics stay.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    iface, col = new_id(InterfaceId), new_id(CollectorId)
    first_in = bucket(iface, col, Metric.IN_BPS, T0, 1.0)
    out = bucket(iface, col, Metric.OUT_BPS, T0, 50.0, quality=Quality.BASELINE_GAP)
    assert backend.rollups.write([first_in, out]) == 2
    rerun = bucket(iface, col, Metric.IN_BPS, T0, 2.0, sample_count=14, valid_count=13)
    backend.rollups.write([rerun])
    assert backend.rollups.read(iface, col, Metric.IN_BPS, DAY, limit=5) == (rerun,)
    assert backend.rollups.read(iface, col, Metric.OUT_BPS, DAY, limit=5) == (out,)
    assert raw(store.path, "SELECT bucket_count FROM partition_catalog") == [(2,)]
    assert raw(store.path, "SELECT count(*) FROM roll_iface_m202610") == [(1,)]
    with pytest.raises(ValidationError):
        backend.rollups.write([rerun, cast(RollupBucket, object())])
    assert backend.rollups.read(iface, col, Metric.IN_BPS, DAY, limit=5) == (rerun,)
    store.close()


def test_rollups_keep_counts_without_values_and_other_widths(tmp_path: Path) -> None:
    """ROLL-T05, 10.5: an all-gap bucket keeps its counts; other widths are kept apart.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    dev, col = s.device().id, new_id(CollectorId)
    outage = RollupBucket(dev, col, Metric.RTT_MEDIAN_US, T0, 900, 15, 15, 0,
                          quality=Quality.NOT_ATTEMPTED)  # fmt: skip
    hourly = bucket(dev, col, Metric.RTT_MEDIAN_US, T0, 900.5, bucket_seconds=3600)
    later = bucket(dev, col, Metric.RTT_MEDIAN_US, T0 + BUCKET, 1000.0)
    backend.rollups.write([later, hourly, outage])
    stored = backend.rollups.read(dev, col, Metric.RTT_MEDIAN_US, DAY, limit=10)
    assert stored == (outage, hourly, later)  # ordered by (start, width)
    series = backend.query.icmp_series(DeviceId(dev), col, Metric.RTT_MEDIAN_US, DAY)
    assert series.points == ()  # no native horizon yet: the whole window is native
    assert backend.rollups.coverage_end() == T0 + 4 * BUCKET  # the hourly bucket's end
    store.close()


def test_e21_interface_values_keep_float32_precision_icmp_values_are_exact(
    tmp_path: Path,
) -> None:
    """E.21, ADR-021 amendment 3: interface aggregates within 2^-24; ICMP aggregates exact.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    iface, dev, col = new_id(InterfaceId), new_id(DeviceId), new_id(CollectorId)
    rng = random.Random(21)  # noqa: S311  # reproducible test values, not secrets
    written = []
    for k in range(40):
        base = rng.uniform(1e3, 1e10)
        written.append(bucket(iface, col, Metric.IN_BPS, T0 + k * BUCKET, base, mean=base * 1.1,
                              p95=base * 1.3, maximum=base * 1.4))  # fmt: skip
        written.append(bucket(dev, col, Metric.LOSS_PCT, T0 + k * BUCKET, 0.0, mean=100 / 3,
                              p95=20.0, maximum=40.0))  # fmt: skip
    backend.rollups.write(written)
    got = backend.rollups.read(iface, col, Metric.IN_BPS, DAY, limit=100)
    for want, have in zip(written[::2], got, strict=True):
        for name in ("minimum", "mean", "p95", "maximum"):
            w, h = getattr(want, name), getattr(have, name)
            assert abs(h - w) <= w * 2**-24
        low, mean, high = have.minimum, have.mean, have.maximum
        assert low is not None
        assert mean is not None
        assert high is not None
        assert low <= mean <= high
    assert backend.rollups.read(dev, col, Metric.LOSS_PCT, DAY, limit=100) == tuple(written[1::2])
    small = bucket(iface, col, Metric.OUT_ERRORS, T0, 0.0, mean=1 / 60, p95=0.0, maximum=1.0,
                   minimum=0.0)  # fmt: skip
    exact = bucket(iface, col, Metric.IN_PPS, T0, 3.0, mean=15.5, p95=16.0, maximum=17.25)
    backend.rollups.write([small, exact])
    assert backend.rollups.read(iface, col, Metric.IN_PPS, DAY, limit=1) == (exact,)
    store.close()


def test_rollup_only_interfaces_are_linked_to_their_device_later(tmp_path: Path) -> None:
    """ADR-021: an interface first seen in a rollup gets its device from its first sample.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    backend.rollups.write([bucket(iface, col, Metric.IN_BPS, T0, 1.0)])
    assert raw(store.path, "SELECT device_key FROM dict_interface") == [(None,)]
    write(backend, [s.sample(dev, iface, col, T0)])
    assert raw(store.path, "SELECT device_key FROM dict_interface") == [(1,)]
    assert backend.current.interface_states(dev, col, PageRequest(), now=T0).total == 1
    store.close()


def test_payload_codec_shares_counts_and_refuses_damage() -> None:
    """ADR-021 amendment 3: shared counts cost no bytes; damaged payloads are StorageError.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    a = Aggregates(15, 15, 15, 1.0, 2.0, 3.0, 4.0, 0)
    gap = Aggregates(15, 15, 0, None, None, None, None, 0)
    one, two = encode_payload({1: a}, quantize=True), encode_payload({1: a, 2: a}, quantize=True)
    assert len(two) - len(one) == 5  # the second metric adds only its mode byte and values
    assert decode_payload(encode_payload({3: gap, 6: a}, quantize=False)) == {3: gap, 6: a}
    assert decode_payload(two, 2) == {2: a}
    for damaged in (b"", b"\x01\x00\x00", two + b"\x00", "text"):
        with pytest.raises(StorageError):
            decode_payload(damaged)
