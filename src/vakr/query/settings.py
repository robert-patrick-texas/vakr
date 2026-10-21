"""Settings and bounds of the current-state query services (Appendix E.29, E.19).

The query services need a few collection defaults to judge evidence the
way the collector produced it: the default poll interval and reachability
strategy for devices without their own (E.25), and the ICMP thresholds that
name *why* a device is impaired (E.1). Everything else here is a bound, so
a large inventory can slow a refresh but never exhaust memory (rule:
every query is bounded).

Dependencies:
    vakr.config: The effective configuration and option catalog.
    vakr.logic.icmp, vakr.logic.status: ICMP policy and restart window.
    vakr.scheduler.hashing: The local collector's name, resolved as the collector does.

Safety:
    Reads configuration only; never secrets.

Example:
    >>> s = QuerySettings()
    >>> s.default_interval_s, s.strategy.value, s.limits.max_devices
    (60, 'icmp_gated', 5000)
"""

from dataclasses import dataclass, field
from typing import Any

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import ReachabilityStrategy
from vakr.domain.paging import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from vakr.lifecycle.configmerge import parse_duration
from vakr.logic.icmp import DEFAULT_POLICY, IcmpPolicy
from vakr.logic.status import RECENT_RESTART_WINDOW_S
from vakr.scheduler.hashing import resolve_collector_name

DEFAULT_REFRESH_S = 10.0
"""``tui.refresh_interval`` default (E.29)."""
MIN_REFRESH_S = 2.0
MAX_REFRESH_S = 600.0


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """How much one list or detail view reads (E.29, E.19).

    Attributes:
        page_default: Device-list page size when none is asked for.
        page_max: Largest device-list page.
        max_devices: Devices one list reads; more are reported as truncated.
        max_interfaces_counted: Interfaces scanned for the list's counts.
        max_device_interfaces: Interfaces one device view reads.
        restart_history: SNMP observations read to find a recent restart.
    """

    page_default: int = DEFAULT_PAGE_LIMIT
    page_max: int = MAX_PAGE_LIMIT
    max_devices: int = 5_000
    max_interfaces_counted: int = 20_000
    max_device_interfaces: int = 2_000
    restart_history: int = 500


@dataclass(frozen=True, slots=True)
class QuerySettings:
    """What the query services need from configuration.

    Attributes:
        default_interval_s: ``polling.default_interval`` in seconds.
        strategy: ``polling.reachability_strategy``.
        icmp_policy: ICMP thresholds, for the "Packet loss / Latency" label.
        restart_window_s: How long a detected restart keeps a device amber.
        timezone: ``time.timezone``; empty means the host timezone.
        limits: Read bounds.
        local_collector: This node's collector name (``scheduler.collector_name``,
            else the short host name); empty when unknown. Traffic graphs and
            rankings default to it (27.5 #6).
    """

    default_interval_s: int = 60
    strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED
    icmp_policy: IcmpPolicy = DEFAULT_POLICY
    restart_window_s: float = RECENT_RESTART_WINDOW_S
    timezone: str = ""
    limits: QueryLimits = field(default_factory=QueryLimits)
    local_collector: str = ""


def _get(config: EffectiveConfig, key: str) -> Any:
    """Read one option, falling back to its catalog default (CFGM-001).

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        Any: The configured value, or the packaged default.

    Raises:
        KeyError: If the catalog does not declare ``key``.
    """
    option = project.catalog().option(key)
    if option is None:
        raise KeyError(key)
    return config.get(key, option.default)


def settings_from_config(config: EffectiveConfig, hostname: str | None = None) -> QuerySettings:
    """Build query settings from the effective configuration.

    Args:
        config: Effective configuration, validated by the catalog.
        hostname: This host's name, to resolve an empty ``scheduler.collector_name``
            as the collector does (SCHED-001); ``None`` leaves the local
            collector unknown unless it is configured.

    Returns:
        QuerySettings: The settings; ``icmp.impaired_rtt_ms = 0`` means off.

    Raises:
        ValueError: If a value is outside what the ICMP policy accepts
            (only possible with an unvalidated configuration).
    """
    rtt_ms = float(_get(config, "icmp.impaired_rtt_ms"))
    policy = IcmpPolicy(float(_get(config, "icmp.impaired_loss_percent")), rtt_ms or None)
    configured = str(_get(config, "scheduler.collector_name")).strip()
    try:
        local = resolve_collector_name(configured, hostname) if hostname else configured
    except ValueError:
        local = configured
    return QuerySettings(
        default_interval_s=int(parse_duration(_get(config, "polling.default_interval"))),
        strategy=ReachabilityStrategy(_get(config, "polling.reachability_strategy")),
        icmp_policy=policy,
        timezone=str(_get(config, "time.timezone")),
        local_collector=local,
    )


def refresh_from_config(config: EffectiveConfig) -> float:
    """Read ``tui.refresh_interval`` in seconds.

    Args:
        config: Effective configuration.

    Returns:
        float: Seconds between TUI refreshes.

    Raises:
        ValueError: If the value is not a duration.
    """
    return parse_duration(_get(config, "tui.refresh_interval"))
