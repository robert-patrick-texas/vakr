"""Read the ``[snmp]`` configuration into session settings and intervals.

The option catalog (``vakr.defaults/options.toml``) declares and validates
every ``snmp.*`` key with its Appendix E default; this module turns the
validated values into the types the collector uses. A key missing from a
hand-built configuration falls back to the same default.

Dependencies:
    vakr.config.loader: ``EffectiveConfig``.
    vakr.lifecycle.configmerge: Duration parsing.
    vakr.ports.snmp: ``SnmpSettings``.

Safety:
    No key here holds a secret; communities live in the credentials file.

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> cfg = snmp_config(EffectiveConfig({"snmp": {"timeout": "3s"}}))
    >>> cfg.settings.timeout_s, cfg.port, cfg.rediscovery_interval.days
    (3.0, 161, 1)
"""

from dataclasses import dataclass
from datetime import timedelta

from vakr.config.loader import EffectiveConfig
from vakr.lifecycle.configmerge import parse_duration
from vakr.ports.snmp import SnmpSettings
from vakr.ports.snmp.settings import (
    DEFAULT_MAX_REPETITIONS,
    DEFAULT_MAX_VARBINDS,
    DEFAULT_MAX_WALK_ROWS,
    DEFAULT_RETRIES,
)
from vakr.ports.snmp.target import DEFAULT_PORT

DEFAULT_REDISCOVERY = "24h"  # E.9, DISC-002
DEFAULT_REPROBE = "7d"  # E.9, DISC-009


@dataclass(frozen=True, slots=True)
class SnmpConfig:
    """The effective ``[snmp]`` settings.

    Attributes:
        port: UDP port of SNMP agents.
        settings: The per-session request budget.
        rediscovery_interval: How often each device is rediscovered.
        capability_reprobe_interval: How long learned counter limitations
            are trusted before they are probed again.
    """

    port: int
    settings: SnmpSettings
    rediscovery_interval: timedelta
    capability_reprobe_interval: timedelta


def snmp_config(config: EffectiveConfig) -> SnmpConfig:
    """Build the SNMP settings from the effective configuration.

    Args:
        config: The validated effective configuration.

    Returns:
        SnmpConfig: Settings and intervals.

    Raises:
        ValueError: If a value is malformed, which validation normally
            prevents.
    """
    settings = SnmpSettings(
        timeout_s=parse_duration(config.get("snmp.timeout", "2s")),
        retries=int(config.get("snmp.retries", DEFAULT_RETRIES)),
        max_varbinds=int(config.get("snmp.max_varbinds", DEFAULT_MAX_VARBINDS)),
        max_repetitions=int(config.get("snmp.max_repetitions", DEFAULT_MAX_REPETITIONS)),
        max_walk_rows=int(config.get("snmp.max_walk_rows", DEFAULT_MAX_WALK_ROWS)),
    )
    rediscovery = parse_duration(config.get("snmp.rediscovery_interval", DEFAULT_REDISCOVERY))
    reprobe = parse_duration(config.get("snmp.capability_reprobe_interval", DEFAULT_REPROBE))
    return SnmpConfig(
        port=int(config.get("snmp.port", DEFAULT_PORT)),
        settings=settings,
        rediscovery_interval=timedelta(seconds=rediscovery),
        capability_reprobe_interval=timedelta(seconds=reprobe),
    )
