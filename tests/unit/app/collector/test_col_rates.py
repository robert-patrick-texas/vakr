"""Rate derivation in the collector: baselines, checkpoint, restart (RATE-T01, -T02, RATE-001..004).

Dependencies:
    vakr.app.collector: Baselines, codec, checkpoint, derivation.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_rates.py
"""

import json
import stat
from pathlib import Path

import pytest

from tests.unit.app.collector.col_fakes import CID, T0, counter_sample, iface, make_device
from vakr.app.collector import baselines as baselines_module
from vakr.app.collector.baseline_codec import decode_store, encode_store
from vakr.app.collector.baselines import Baseline, BaselineStore
from vakr.app.collector.checkpoint import (
    boot_id,
    checkpoint_path,
    load_baselines,
    save_checkpoint,
)
from vakr.app.collector.derive import DeriveContext, RateSettings, derive_observation
from vakr.collectors.snmp import UptimeReading
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import BaselineState, CounterFamily, UptimeContinuity
from vakr.domain.ids import CollectorId, PollId, new_id
from vakr.domain.quality import Quality
from vakr.logic.capacity import NO_CAPACITY
from vakr.logic.counters import CounterSample

DEVICE = make_device(1)
BOOT = "4f0c0d0e-boot-0000-0000-000000000001"


def ctx(
    boot: str = BOOT, interval_s: float = 60.0, limits: RateSettings | None = None
) -> DeriveContext:
    """Return poll-wide evidence for a continuous agent.

    Args:
        boot: Kernel boot ID.
        interval_s: Effective interval.
        limits: Rate limits; the defaults when ``None``.

    Returns:
        DeriveContext: The context.

    Raises:
        None
    """
    return DeriveContext(new_id(PollId), CID, DEVICE.id, interval_s, UptimeContinuity.CONTINUOUS,
                         boot, limits or RateSettings())  # fmt: skip


def test_first_sample_keeps_raw_counters_and_has_no_rates() -> None:
    """RATE-T02, RATE-004: raw counters stored, every rate unavailable, baseline set.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = BaselineStore()
    obs = derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx())
    assert obs.baseline_state is BaselineState.NO_BASELINE
    assert obs.quality & Quality.BASELINE_FIRST_SAMPLE
    assert obs.in_octets == 1_000
    assert obs.in_bps is None
    assert obs.out_bps is None
    assert store.get(iface(1)) is not None


def test_second_sample_uses_the_actual_elapsed_time() -> None:
    """RATE-T01, RATE-001, RATE-006: 750,000 octets in 60 s is exactly 100 kb/s.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx())
    obs = derive_observation(counter_sample(3, 60, 751_000), store, NO_CAPACITY, ctx())
    assert obs.baseline_state is BaselineState.VALID
    assert obs.in_bps == pytest.approx(100_000.0)
    assert obs.sample_elapsed_us == 60_000_000
    assert obs.quality & Quality.CAPACITY_UNKNOWN  # RATE-010: no denominator known


def test_baseline_from_another_ifindex_is_not_used() -> None:
    """RATE-003, invariant 6: a renumbered row gives BASELINE_MISSING_PRIOR, not a delta.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx())
    obs = derive_observation(counter_sample(7, 60, 9_000_000), store, NO_CAPACITY, ctx())
    assert obs.baseline_state is BaselineState.NO_BASELINE
    assert obs.quality & Quality.BASELINE_MISSING_PRIOR
    assert obs.in_bps is None


def test_unanswered_sample_keeps_the_older_baseline() -> None:
    """A gap is not a new starting point: the next answer rates over two intervals.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    store = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx())
    gap = derive_observation(counter_sample(3, 60, None), store, NO_CAPACITY, ctx())
    assert gap.in_octets is None
    assert gap.in_bps is None
    obs = derive_observation(counter_sample(3, 120, 1_501_000), store, NO_CAPACITY, ctx())
    assert obs.baseline_state is BaselineState.VALID
    assert obs.in_bps == pytest.approx(100_000.0)


def test_gap_beyond_the_configured_limit_invalidates() -> None:
    """RATE-T06, RATE-003: ``rates.max_gap_intervals`` = 2 refuses a 3-interval gap.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    limits = RateSettings.from_config(EffectiveConfig({"rates": {"max_gap_intervals": 2.0}}))
    assert limits == RateSettings(2.0, 1.1)
    store = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx(limits=limits))
    obs = derive_observation(counter_sample(3, 180, 9_000), store, NO_CAPACITY, ctx(limits=limits))
    assert obs.baseline_state is BaselineState.INVALIDATED
    assert obs.quality & Quality.BASELINE_GAP


def test_elapsed_time_is_monotonic_within_a_boot_and_wall_across_boots() -> None:
    """RATE-001: a restarted process shares the boot's monotonic clock; a reboot does not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    base = Baseline(DEVICE.id, 3, CounterSample(CounterFamily.HC64, in_octets=1), T0, 100.0, BOOT)
    later = T0.replace(minute=1)
    assert base.elapsed_to(later, 159.5, BOOT) == pytest.approx(59.5)
    assert base.elapsed_to(later, 5.0, "another-boot") == pytest.approx(60.0)
    assert base.elapsed_to(later, 5.0, "") == pytest.approx(60.0)


def test_store_is_bounded_and_follows_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded memory: new interfaces past the cap are refused; pruning drops the rest.

    Args:
        monkeypatch: Lowers the cap.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(baselines_module, "MAX_BASELINES", 1)
    store = BaselineStore()
    derive_observation(counter_sample(1, 0, 10, interface=1), store, NO_CAPACITY, ctx())
    derive_observation(counter_sample(2, 0, 10, interface=2), store, NO_CAPACITY, ctx())
    assert len(store) == 1
    assert store.refused == 1
    store.set_uptime(DEVICE.id, UptimeReading(100, T0))
    assert store.prune([], []) == 1
    assert len(store) == 0
    assert store.uptime(DEVICE.id) is None


def test_checkpoint_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    """6.2: write, fsync, rename; mode 0640; the restored store is identical.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    store = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx())
    store.set_uptime(DEVICE.id, UptimeReading(4_200, T0))
    path = checkpoint_path(tmp_path, CID)
    assert save_checkpoint(encode_store(store, CID, T0), path) > 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(path.parent.glob("*.tmp"))
    restored = load_baselines(path, CID)
    assert restored.get(iface(1)) == store.get(iface(1))
    assert restored.uptime(DEVICE.id) == UptimeReading(4_200, T0)


def test_untrusted_checkpoints_start_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing, damaged, oversized, or another collector's file: no baselines, no wrong rate.

    Args:
        tmp_path: Test directory.
        monkeypatch: Lowers the size bound.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "rate-baselines.json"
    assert len(load_baselines(path, CID)) == 0
    path.write_text("{not json")
    assert len(load_baselines(path, CID)) == 0
    store = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), store, NO_CAPACITY, ctx())
    save_checkpoint(encode_store(store, CID, T0), path)
    assert len(load_baselines(path, new_id(CollectorId))) == 0  # MPOL-003
    data = json.loads(path.read_text())
    data["interfaces"][str(iface(1))]["c"][0] = "1"
    with pytest.raises(ValueError, match="integer-or-null"):
        decode_store(data, CID)
    monkeypatch.setattr("vakr.app.collector.checkpoint.MAX_CHECKPOINT_BYTES", 10)
    assert len(load_baselines(path, CID)) == 0


def test_restart_resumes_rates_from_the_checkpoint(tmp_path: Path) -> None:
    """RATE-004, 6.2: a restart costs no rate interval when the checkpoint is current.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    path = checkpoint_path(tmp_path, CID)
    first = BaselineStore()
    derive_observation(counter_sample(3, 0, 1_000), first, NO_CAPACITY, ctx())
    save_checkpoint(encode_store(first, CID, T0), path)
    second = load_baselines(path, CID)
    obs = derive_observation(counter_sample(3, 60, 751_000), second, NO_CAPACITY, ctx())
    assert obs.baseline_state is BaselineState.VALID
    assert obs.in_bps == pytest.approx(100_000.0)


def test_boot_id_is_read_or_empty(tmp_path: Path) -> None:
    """The kernel boot ID scopes monotonic time; an unreadable one falls back to wall time.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    good = tmp_path / "boot_id"
    good.write_text(BOOT + "\n")
    assert boot_id(good) == BOOT
    assert boot_id(tmp_path / "missing") == ""
