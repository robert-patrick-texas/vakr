"""SQLite ranking projection internals: hour units, plans, invalidation, pruning, damage (E.28).

The shared ``RankingProjectionContract`` checks what every backend must do;
these tests look at what the SQLite adapter stores to do it: one bucket
unit per projected bucket and one hour unit once an hour's four buckets
are listed, unlisted together by a late sample and by retention, exact
under any mix of projected and native spans, and refusing a damaged
payload instead of ranking from it (DASH-T01, DASH-T02, MPOS-008).

Dependencies:
    pytest, random; tests.contract.contract_ranking_projection; the SQLite adapter.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_ranking_projection.py
"""

import math
import random
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.contract.contract_ranking_projection import BUCKET, HOURS, expected, fleet
from tests.contract.contract_ranking_projection import write as write_all
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import open_store, raw, write
from vakr.adapters.sqlite.telemetry import SqliteTelemetry, ranking_parts
from vakr.adapters.sqlite.telemetry.ranking_parts import Partial
from vakr.adapters.sqlite.telemetry.rankings import window_summaries
from vakr.domain.errors import StorageError
from vakr.domain.ids import InterfaceId, new_id
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us

UNITS = "SELECT width_s, start_us FROM rank_unit ORDER BY width_s, start_us"


def _us(k: int, width: timedelta = BUCKET) -> int:
    """Return the start of unit ``k`` after ``T0`` in microseconds.

    Args:
        k: Unit number.
        width: Unit width.

    Returns:
        int: Microseconds since the epoch.

    Raises:
        None
    """
    return to_epoch_us(s.T0 + k * width)


def test_e28_hours_are_compacted_and_unlisted_with_their_buckets(tmp_path: Path) -> None:
    """E.28: four listed buckets make an hour unit; a late sample unlists bucket and hour.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    rows, cols = fleet()
    write_all(backend, rows)
    proj = backend.ranking_projection
    for bucket in proj.unprojected(TimeWindow(s.T0, s.T0 + HOURS * 4 * BUCKET)):
        proj.project(bucket)
    hour = timedelta(hours=1)
    assert raw(store.path, UNITS) == [(900, _us(k)) for k in range(12)] + [
        (3600, _us(k, hour)) for k in range(3)
    ]
    assert raw(store.path, "SELECT count(*) FROM rank_iface WHERE width_s = 3600") == [(18,)]
    proj.project(s.T0 + 5 * BUCKET)  # re-projecting rebuilds the hour from its buckets
    assert (3600, _us(1, hour)) in raw(store.path, UNITS)
    write(backend, [s.sample(rows[0].device_id, rows[0].interface_id, cols[0],
                             s.T0 + 6 * BUCKET + s.MINUTE // 2)])  # fmt: skip
    units = raw(store.path, UNITS)
    assert {(900, _us(6)), (3600, _us(1, hour))}.isdisjoint(units)
    assert {(900, _us(5)), (3600, _us(2, hour))} <= set(units)
    store.close()


def test_e28_any_mix_of_projected_and_native_spans_is_exact(tmp_path: Path) -> None:
    """E.28, DASH-T01, DASH-T02: random projected subsets and windows match the reference.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    rows, cols = fleet(seed=11)
    write_all(backend, rows)
    proj, rng = backend.ranking_projection, random.Random(5)  # noqa: S311  # reproducible
    every = list(proj.unprojected(TimeWindow(s.T0, s.T0 + HOURS * 4 * BUCKET)))
    for _ in range(25):
        proj.forget()
        for bucket in rng.sample(every, rng.randint(0, len(every))):
            proj.project(bucket)
        start = rng.randrange(0, HOURS * 60 - 1)
        window = TimeWindow(s.T0 + start * s.MINUTE,
                            s.T0 + rng.randrange(start + 1, HOURS * 60 + 1) * s.MINUTE)  # fmt: skip
        for col in cols:
            assert window_summaries(store, col, window) == expected(rows, col, window)
    store.close()


def test_e28_retention_removes_old_units_and_their_rows(tmp_path: Path) -> None:
    """E.28: a retention pass deletes units and partials older than 25 hours.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    rows, _cols = fleet()
    write_all(backend, rows)
    proj = backend.ranking_projection
    for bucket in proj.unprojected(TimeWindow(s.T0, s.T0 + HOURS * 4 * BUCKET)):
        proj.project(bucket)
    backend.retention.apply(s.T0 + timedelta(hours=26, minutes=50))
    cut = _us(7)
    assert min(r[1] for r in raw(store.path, UNITS)) == cut
    assert raw(store.path, "SELECT count(*) FROM rank_iface WHERE start_us < ?", (cut,)) == [(0,)]
    assert raw(store.path, "SELECT DISTINCT width_s FROM rank_unit") == [(900,), (3600,)]
    store.close()


def test_e28_a_damaged_payload_is_refused(tmp_path: Path) -> None:
    """E.28: a payload of another version or length fails the ranking instead of skewing it.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    rows, cols = fleet()
    write_all(backend, rows)
    backend.ranking_projection.project(s.T0)
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("UPDATE rank_iface SET payload = substr(payload, 1, 20)")
    with pytest.raises(StorageError, match="damaged"):
        window_summaries(store, cols[0], TimeWindow(s.T0, s.T0 + BUCKET))
    for payload in (b"", b"\x02" + bytes(60), Partial().payload() + bytes(8)):
        with pytest.raises(StorageError, match="damaged"):
            Partial().add_payload(payload)
    store.close()


def test_e28_partials_merge_packet_sums_exactly() -> None:
    """E.28, DASH-004: parts kept from one bucket join the rest without losing low bits.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    bucket, rest = Partial(), Partial()
    bucket.add_row((2, "5.0,1.5", "2.0", 2, 3, 4, 2, "1e16,1.0"))
    rest.add_row((1, None, None, 1, 0, 1, 1, "1.0"))
    rest.add_payload(bucket.payload())
    assert (1e16 + 1.0) + 1.0 == 1e16
    assert math.fsum(rest.parts) == 1e16 + 2
    assert (list(rest.ins), list(rest.outs), rest.samples) == ([1.5, 5.0], [2.0], 3)
    summary = rest.summary(new_id(InterfaceId))
    assert (summary.error_samples, summary.in_errors, summary.out_errors) == (3, 3, 5)
    assert summary.packets == round((1e16 + 2) / 1_000_000)


def test_e28_payloads_round_trip_through_the_byte_order_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E.28: the swapping path a big-endian host takes packs and unpacks the same values.

    Args:
        monkeypatch: Pretends the host is big-endian.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(ranking_parts, "_SWAP", True)
    part = Partial()
    part.add_row((3, "2.5,1.25,7.0", "0.5", 3, 1, 2, 3, "3.0,4.0,5.5"))
    again = Partial()
    again.add_payload(part.payload())
    assert (list(again.ins), list(again.outs), math.fsum(again.parts)) == (
        [1.25, 2.5, 7.0],
        [0.5],
        12.5,
    )
