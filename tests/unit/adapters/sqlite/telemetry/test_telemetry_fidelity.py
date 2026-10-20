"""Stored values come back exactly: HC counters, probe sentinels, floats, and offsets.

Covers DB-004 (IDs, nulls, sentinels, and quality survive storage),
ICMP-003/004/005 (every probe kept; ``-1`` on disk for no reply, ``None``
in the domain), E.21's signed mapping of 64-bit counters at or above 2^63,
and invariant 1 (missing is ``NULL``, never 0), checked both through the
adapter and on disk behind its back.

Dependencies:
    pytest; vakr.adapters.sqlite.telemetry; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_fidelity.py
"""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import T0, open_store, raw, write
from vakr.adapters.sqlite.telemetry import SqliteTelemetry
from vakr.adapters.sqlite.telemetry.codec_probe import probe_columns, probes_from
from vakr.adapters.sqlite.telemetry.vocab import from_signed64, to_signed64
from vakr.domain.enums import (
    AddressFamily,
    CounterFamily,
    IcmpState,
    IfAdminStatus,
    IfOperStatus,
    ObservationType,
    PollExecution,
)
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import CollectorId, InterfaceId, WorkerId, new_id
from vakr.domain.observations import (
    IcmpObservation,
    Observation,
    observation_subject,
    observation_type,
)
from vakr.domain.paging import PageRequest
from vakr.domain.quality import Quality
from vakr.domain.series import TimeWindow

WINDOW = TimeWindow(T0 - timedelta(days=1), T0 + timedelta(days=1))
TOP = (1 << 64) - 1


def _round_trip(tmp_path: Path, rows: Sequence[Observation], *, moved: bool) -> list[Observation]:
    """Write observations, optionally move them into day partitions, and read them back.

    Args:
        tmp_path: Directory for the file.
        rows: Observations of any types.
        moved: Run ``maintain`` first, so reads come from day partitions.

    Returns:
        list[Observation]: What ``read_raw`` returns for each, in input order.

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    assert write(backend, rows).inserted == len(rows)
    if moved:
        assert backend.maintain(T0 + timedelta(hours=1)).moved == len(rows)
    out = []
    for o in rows:
        page = backend.query.read_raw(observation_type(o), observation_subject(o), WINDOW,
                                      PageRequest(), collector_id=o.collector_id)  # fmt: skip
        out.extend(p for p in page.items if p.observation_id == o.observation_id)
    store.close()
    return out


@pytest.mark.parametrize("moved", [False, True], ids=["staged", "partitioned"])
def test_hc_counters_at_and_above_2_63_round_trip(tmp_path: Path, moved: bool) -> None:
    """E.21, DB-004: 64-bit counters up to 2^64 - 1 survive; disk holds the signed mapping.

    Args:
        tmp_path: Directory for the file.
        moved: Read from staging or from a day partition.

    Returns:
        None

    Raises:
        None
    """
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    sample = s.sample(dev, iface, col, T0, in_octets=TOP, out_octets=1 << 63, in_packets=0,
                      out_packets=(1 << 63) - 1, in_errors=TOP, out_errors=None,
                      in_error_delta=(1 << 63) + 5, out_error_delta=0,
                      admin_status=IfAdminStatus.UP, oper_status=IfOperStatus.LOWER_LAYER_DOWN,
                      counter_discontinuity_ticks=(1 << 32) - 1)  # fmt: skip
    assert _round_trip(tmp_path, [sample], moved=moved) == [sample]
    table = "obs_iface_d20261001" if moved else "obs_iface_hot"
    sql = f"SELECT in_octets, out_octets, out_errors FROM {table}"  # noqa: S608  # fixed names
    stored = raw(tmp_path / "telemetry.db", sql)
    assert stored == [(-1, -(1 << 63), None)]
    assert (to_signed64(TOP), from_signed64(-(1 << 63)), to_signed64(None)) == (-1, 1 << 63, None)


@pytest.mark.parametrize("moved", [False, True], ids=["staged", "partitioned"])
def test_icmp004_no_reply_is_minus_one_on_disk_and_none_in_the_domain(
    tmp_path: Path, moved: bool
) -> None:
    """ICMP-003/004/005: every probe kept, -1 stored for no reply, None read back.

    Args:
        tmp_path: Directory for the file.
        moved: Read from staging or from a day partition.

    Returns:
        None

    Raises:
        None
    """
    dev, col = s.device().id, new_id(CollectorId)
    five = s.icmp(dev, col, T0, rtts_us=(0, None, 1_000_000, None, 7))
    lost = s.icmp(dev, col, T0 + s.MINUTE, rtts_us=(None,) * 5, state=IcmpState.UNREACHABLE,
                  min_rtt_us=None, avg_rtt_us=None, median_rtt_us=None, max_rtt_us=None,
                  jitter_us=None, quality=Quality.NONE)  # fmt: skip
    long_train = s.icmp(dev, col, T0 + 2 * s.MINUTE, rtts_us=(900, None, 1100, 1000, 1000,
                                                               None, 950, 2 ** 40))  # fmt: skip
    one = s.icmp(dev, col, T0 + 3 * s.MINUTE, rtts_us=(1000,), min_rtt_us=1000, avg_rtt_us=1000,
                 median_rtt_us=1000, max_rtt_us=1000, jitter_us=None,
                 address_family=AddressFamily.IPV6, target_address="2001:db8::1",
                 source_address="2001:db8::2")  # fmt: skip
    rows = [five, lost, long_train, one]
    assert _round_trip(tmp_path, rows, moved=moved) == rows
    table = "obs_icmp_d20261001" if moved else "obs_icmp_hot"
    columns = "rtt_1_us, rtt_2_us, rtt_3_us, rtt_4_us, rtt_5_us, rtt_more, sent_count, lost_count"
    sql = f"SELECT {columns} FROM {table} ORDER BY ts_us"  # noqa: S608  # fixed names
    stored = raw(tmp_path / "telemetry.db", sql)
    assert stored[0] == (0, -1, 1_000_000, -1, 7, None, 5, 2)
    assert stored[1][:5] == (-1,) * 5
    assert stored[2][5] == (-1).to_bytes(8, "big", signed=True) + (950).to_bytes(8, "big") + (
        2**40
    ).to_bytes(8, "big")
    assert stored[3][:6] == (1000, None, None, None, None, None)


@pytest.mark.parametrize(
    "rtts",
    [(None,), (1200,), (None, 900), (850, 900), (900, None, 1100, None, 1000)],
    ids=["1-lost", "1-reply", "2-one-lost", "2-replies", "5-two-lost"],
)
@pytest.mark.parametrize("moved", [False, True], ids=["staged", "partitioned"])
def test_icmp003_probe_trains_of_1_to_5_keep_their_exact_length(
    tmp_path: Path, rtts: tuple[int | None, ...], moved: bool
) -> None:
    """ICMP-003/004: 1-5 probes round-trip; unsent probes are NULL, never the -1 loss sentinel.

    ``icmp.probes`` is configurable from 2 to 5 and probes the collector
    never sent are left out of ``rtts_us``, so ``sent = len(rtts_us)`` must
    survive storage exactly: ``sent_count`` holds it and columns past it are
    ``NULL``. Only a sent probe without a reply is stored as ``-1``.

    Args:
        tmp_path: Directory for the file.
        rtts: The probe train.
        moved: Read from staging or from a day partition.

    Returns:
        None

    Raises:
        None
    """
    dev, col = s.device().id, new_id(CollectorId)
    replies = [r for r in rtts if r is not None]
    low, high = (min(replies), max(replies)) if replies else (None, None)
    cycle = s.icmp(dev, col, T0, rtts_us=rtts, jitter_us=None, min_rtt_us=low, avg_rtt_us=low,
                   median_rtt_us=low, max_rtt_us=high)  # fmt: skip
    (back,) = _round_trip(tmp_path, [cycle], moved=moved)
    assert isinstance(back, IcmpObservation)
    assert (back.rtts_us, back.sent, back.received) == (rtts, len(rtts), len(replies))
    table = "obs_icmp_d20261001" if moved else "obs_icmp_hot"
    sql = f"SELECT rtt_1_us, rtt_2_us, rtt_3_us, rtt_4_us, rtt_5_us, sent_count FROM {table}"  # noqa: S608  # fixed names
    (stored,) = raw(tmp_path / "telemetry.db", sql)
    expected = [-1 if r is None else r for r in rtts] + [None] * (5 - len(rtts))
    assert stored == (*expected, len(rtts))


def test_probe_codec_edges() -> None:
    """ICMP-004: probe columns pad with NULL, and a damaged extra-probe blob is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert probe_columns((None,)) == (-1, None, None, None, None, None)
    assert probes_from((5, None, 7, None, None), None) == (5,)  # NULL ends the train
    with pytest.raises(StorageError):
        probes_from((5, None, None, None, None), b"\x00\x01")


def test_db004_every_other_field_survives(tmp_path: Path) -> None:
    """DB-004, invariant 1: floats, enums, poll offsets, health counts, and NULLs round-trip.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    rows: list[Observation] = [
        s.sample(dev, iface, col, T0, in_bps=0.0, out_bps=1e300, in_pps=0.1, out_pps=123.0,
                 in_error_rate=1 / 3, counter_family=CounterFamily.LEGACY32,
                 in_octets=(1 << 32) - 1),
        s.poll(dev, col, T0, worker_id=new_id(WorkerId), started_at=T0 - timedelta(microseconds=3),
               completed_at=T0 + timedelta(seconds=90, microseconds=1),
               execution=PollExecution.PARTIAL),
        s.health(col, T0, spool_used_pct=99.999, clock_offset_us=-(10**12), spool_bytes=10**15,
                 worker_id=new_id(WorkerId), quality=Quality.CLOCK_UNSYNCED),
        s.snmp(dev, col, T0, sys_uptime_ticks=0, error_code="walk-truncated"),
    ]  # fmt: skip
    assert _round_trip(tmp_path, rows, moved=True) == rows


def test_values_sqlite_cannot_hold_refuse_the_whole_batch(tmp_path: Path) -> None:
    """STORE-010: a value beyond 64 bits is a ValidationError and nothing is written.

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
    good = s.icmp(dev, col, T0)
    huge = s.icmp(dev, col, T0 + s.MINUTE, rtts_us=(1, 2, 3, 4, 5, 1 << 70))
    with pytest.raises(ValidationError):
        write(backend, [good, huge])
    poll = s.poll(dev, col, T0, configured_interval_s=1 << 64)
    with pytest.raises(ValidationError):
        write(backend, [good, poll])
    assert backend.query.read_raw(ObservationType.ICMP, dev, WINDOW, PageRequest()).items == ()
    store.close()
