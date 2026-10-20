"""Scheduler settings, poll targets, the plan timeline, and documented examples (E.25, TIME-001).

Dependencies:
    pytest, doctest: Tables and the modules' ``>>>`` examples.
    vakr.scheduler: The code under test.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_settings.py
"""

import doctest
import importlib
import pkgutil
from datetime import timedelta
from pathlib import Path

import pytest

import vakr.scheduler
from tests.unit.scheduler import sched_fakes, sched_simloop
from tests.unit.scheduler.sched_fakes import RULE, device_id
from tests.unit.scheduler.sched_simloop import START, new_clock
from vakr.config import project
from vakr.config.loader import EffectiveConfig, load_effective_config
from vakr.domain.enums import AdminState, ManagedByKind, ReachabilityStrategy
from vakr.domain.inventory import Device
from vakr.domain.ownership import ManagedBy
from vakr.scheduler.phase import OrderMode, PhaseMode
from vakr.scheduler.settings import SchedulerSettings, scheduler_problems, settings_from_config
from vakr.scheduler.targets import PollDefaults, clamp_interval, target_for, targets_from_devices
from vakr.scheduler.timeline import Timeline

MODULES = sorted(m.name for m in pkgutil.iter_modules(vakr.scheduler.__path__))


def test_packaged_defaults_match_appendix_e25(tmp_path: Path) -> None:
    """E.25 / CFGM-001: the catalog defaults are the documented ones.

    Args:
        tmp_path: An empty configuration directory, so only packaged defaults apply.

    Returns:
        None

    Raises:
        None
    """
    s = settings_from_config(EffectiveConfig({}), hostname="Vakr-East.example.net")
    assert s.rule.collector_name == "vakr-east"
    assert (s.rule.phase, s.rule.order, s.rule.fixed_phase_us) == (
        PhaseMode.HOSTNAME, OrderMode.COMMON, 0,
    )  # fmt: skip
    assert s.defaults == PollDefaults(60, ReachabilityStrategy.ICMP_GATED)
    assert (s.workers, s.queue_capacity) == (32, 1000)
    assert (s.late_after_s, s.max_start_delay_s, s.poll_deadline_s, s.shutdown_grace_s) == (
        5, 30, 90, 15,
    )  # fmt: skip
    assert s == settings_from_config(load_effective_config(tmp_path), "vakr-east")


def test_operator_values_are_applied_and_cross_checked() -> None:
    """11.4: values flow through; ``late_after > max_start_delay`` is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    cfg = EffectiveConfig({
        "polling": {"default_interval": "2m", "reachability_strategy": "snmp_fallback"},
        "scheduler": {
            "collector_name": "core-poller", "phase": "fixed", "fixed_phase": "17s",
            "device_order": "hashed", "workers": 8, "max_start_delay": "45s",
        },
    })  # fmt: skip
    s = settings_from_config(cfg, hostname="ignored")
    assert s.rule.collector_name == "core-poller"
    assert s.rule.fixed_phase_us == 17_000_000
    assert (s.rule.phase, s.rule.order) == (PhaseMode.FIXED, OrderMode.HASHED)
    assert s.defaults == PollDefaults(120, ReachabilityStrategy.SNMP_FALLBACK)
    assert s.workers == 8
    assert s.max_start_delay_us == 45_000_000
    assert s.late_after_us == 5_000_000
    assert scheduler_problems(cfg) == []
    bad = EffectiveConfig({"scheduler": {"late_after": "40s", "max_start_delay": "20s"}})
    assert scheduler_problems(bad) == [
        "scheduler.late_after (40s) must not exceed scheduler.max_start_delay (20s)"
    ]
    with pytest.raises(ValueError, match="late_after"):
        settings_from_config(bad, hostname="h")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("workers", 0, "at least 1"),
        ("queue_capacity", 0, "at least 1"),
        ("poll_deadline_s", 0.0, "positive"),
        ("shutdown_grace_s", -1.0, "negative"),
    ],
)
def test_settings_refuse_unbounded_values(field: str, value: float, message: str) -> None:
    """Every bound is enforced even when settings are built in code.

    Args:
        field: Settings field.
        value: Invalid value.
        message: Expected error text.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=message):
        SchedulerSettings(RULE, **{field: value})  # ty: ignore[invalid-argument-type]


def test_scheduler_options_are_bounded_in_the_catalog() -> None:
    """Every [scheduler] numeric option has both a minimum and a maximum.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    numeric = [
        o for o in project.catalog().options
        if o.key.startswith(("scheduler.", "polling.")) and o.spec.kind in ("int", "duration")
    ]  # fmt: skip
    assert len(numeric) == 8
    assert all(o.spec.minimum is not None and o.spec.maximum is not None for o in numeric)


def _device(i: int, **kw: object) -> Device:
    """Build a device with overrides.

    Args:
        i: Device number.
        **kw: ``Device`` field overrides.

    Returns:
        Device: The device.

    Raises:
        None
    """
    return Device(
        device_id(i),
        f"d{i}",
        "192.0.2.1",
        ManagedBy(ManagedByKind.API),
        START,
        START,
        **kw,  # ty: ignore[invalid-argument-type]
    )


def test_targets_follow_overrides_admin_state_and_limits() -> None:
    """9.2, E.18, ADAPT-004: disabled devices are not polled; intervals clamp visibly.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    defaults = PollDefaults(60, ReachabilityStrategy.ICMP_GATED)
    devices = [
        _device(1),
        _device(2, admin_state=AdminState.DISABLED),
        _device(3, admin_state=AdminState.RETIRED),
        _device(4, admin_state=AdminState.MAINTENANCE, poll_interval_s=300),
        _device(5, poll_interval_s=5, reachability=ReachabilityStrategy.SNMP_ONLY),
    ]
    targets = targets_from_devices(devices, defaults)
    assert [t.device_id for t in targets] == [device_id(1), device_id(4), device_id(5)]
    assert targets[1].maintenance
    assert targets[1].effective_interval_s == 300
    assert (targets[2].configured_interval_s, targets[2].effective_interval_s) == (5, 30)
    assert targets[2].strategy is ReachabilityStrategy.SNMP_ONLY
    assert target_for(devices[1], defaults) is None
    assert clamp_interval(60) == 60


def test_timeline_relabels_only_past_the_step_threshold() -> None:
    """SCHED-018: a sub-second slew keeps labels; a real step moves them once.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    line = Timeline(clock)
    t = line.now_us()
    clock.set_utc(clock.now_utc() + timedelta(milliseconds=500))
    assert not line.check_step()
    assert line.label(t) == START
    clock.set_utc(clock.now_utc() - timedelta(seconds=30))
    assert line.check_step()
    assert line.clock_steps == 1
    assert line.label(t) == START - timedelta(seconds=29.5)
    assert line.wall_offset_us() == -29_500_000
    assert line.delay_s(t - 1) == 0.0


@pytest.mark.parametrize("name", [*MODULES, "sched_fakes", "sched_simloop"])
def test_documented_examples_run(name: str) -> None:
    """Every ``>>>`` example in the scheduler package and its test helpers holds.

    Args:
        name: Module name.

    Returns:
        None

    Raises:
        None
    """
    helpers = {"sched_fakes": sched_fakes, "sched_simloop": sched_simloop}
    module = helpers.get(name) or importlib.import_module(f"vakr.scheduler.{name}")
    result = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert result.failed == 0
