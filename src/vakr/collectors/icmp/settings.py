"""ICMP collection settings from the ``[icmp]`` configuration block (E.1, E.9, E.22).

The option catalog validates each key's type and range; this module turns
the effective values into ``IcmpSettings`` and adds the rule that spans
settings and strategy: under the default ``icmp_gated`` strategy SNMP runs
only after two replies (ICMP-006), so a cycle of fewer than two probes
would never collect SNMP (Appendix E.17).

Dependencies:
    vakr.config: Effective configuration and catalog defaults.
    vakr.lifecycle.configmerge: Duration parsing.
    vakr.logic.icmp, vakr.ports.icmp: Thresholds and the probe request.

Safety:
    Read-only. An ``icmp.impaired_rtt_ms`` of 0 means "off", because the
    catalog has no unset value; it never becomes a 0 ms threshold.

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> settings = settings_from_config(EffectiveConfig({"icmp": {"probes": 3}}))
    >>> settings.probes, settings.spacing_s, settings.policy.impaired_rtt_ms
    (3, 0.2, None)
"""

from dataclasses import dataclass
from typing import Any

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import ReachabilityStrategy
from vakr.lifecycle.configmerge import parse_duration
from vakr.logic.icmp import DEFAULT_POLICY, IcmpPolicy
from vakr.ports.icmp import ProbeRequest

MIN_GATED_PROBES = 2
"""Fewest probes per cycle under ``icmp_gated``: SNMP needs two replies (E.17)."""


@dataclass(frozen=True, slots=True)
class IcmpSettings:
    """Effective ICMP collection settings; defaults are Appendix E.1, E.9, E.22.

    Attributes:
        probes: Echo requests per cycle (``icmp.probes``).
        spacing_s: Seconds between probe starts (``icmp.spacing``).
        timeout_s: Seconds to wait for each reply (``icmp.timeout``).
        max_in_flight: Collector-wide cap on waiting probes (``icmp.max_in_flight``).
        policy: Impairment thresholds (``icmp.impaired_*``).
    """

    probes: int = 5
    spacing_s: float = 0.2
    timeout_s: float = 1.0
    max_in_flight: int = 1024
    policy: IcmpPolicy = DEFAULT_POLICY

    def request(self, target: str, source: str | None = None) -> ProbeRequest:
        """Build the probe request for one device.

        Args:
            target: Canonical IP literal of the device's poll address.
            source: Pinned source address, if the device has one.

        Returns:
            ProbeRequest: The cycle to run.

        Raises:
            ValidationError: If the target or source is not acceptable.
        """
        return ProbeRequest(target, self.probes, self.spacing_s, self.timeout_s, source)


def require_gate_probes(strategy: ReachabilityStrategy, probes: int) -> None:
    """Refuse a probe count that would stop SNMP forever under ``icmp_gated``.

    Args:
        strategy: The device's reachability strategy.
        probes: Probes per cycle.

    Returns:
        None

    Raises:
        ValueError: If ``strategy`` is ``icmp_gated`` and ``probes`` is
            below ``MIN_GATED_PROBES``.
    """
    if strategy is ReachabilityStrategy.ICMP_GATED and probes < MIN_GATED_PROBES:
        raise ValueError(
            f"icmp.probes = {probes} with icmp_gated would never run SNMP: "
            f"SNMP needs {MIN_GATED_PROBES} replies (ICMP-006, Appendix E.17)"
        )


def _value(config: EffectiveConfig, key: str) -> Any:
    """Read an option, falling back to its catalog default.

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        Any: The configured or default value.

    Raises:
        KeyError: If ``key`` is not in the catalog (a programming error).
    """
    option = project.catalog().option(key)
    if option is None:
        raise KeyError(key)
    return config.get(key, option.default)


def settings_from_config(config: EffectiveConfig) -> IcmpSettings:
    """Build ``IcmpSettings`` from the ``[icmp]`` block.

    Args:
        config: Effective configuration, already validated by the catalog.

    Returns:
        IcmpSettings: The settings, with ``impaired_rtt_ms = 0`` read as off.

    Raises:
        ValueError: If a value is outside what the probe request or the
            ICMP policy accept (only possible with an unvalidated config).
    """
    rtt_ms = float(_value(config, "icmp.impaired_rtt_ms"))
    policy = IcmpPolicy(float(_value(config, "icmp.impaired_loss_percent")), rtt_ms or None)
    return IcmpSettings(
        probes=int(_value(config, "icmp.probes")),
        spacing_s=parse_duration(_value(config, "icmp.spacing")),
        timeout_s=parse_duration(_value(config, "icmp.timeout")),
        max_in_flight=int(_value(config, "icmp.max_in_flight")),
        policy=policy,
    )
