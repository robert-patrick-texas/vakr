"""Configuration reload on SIGHUP: validate, apply live options, keep the rest (CONF-003).

``systemctl reload vakr-collector`` sends SIGHUP. The collector loads the
candidate configuration strictly; if it is invalid, the reload is refused,
the reason is logged, and the running configuration stays in force, so a
typo never stops collection (TEST-008). A valid candidate's ``live``
options take effect at once: ICMP probing and thresholds, SNMP timeouts and
sizes, rediscovery and re-probe intervals, the ``[rates]`` limits, and the
``[polling]`` defaults (followed by a re-plan). Options marked ``restart`` in
the catalog, such as ``scheduler.workers`` or the spool sizes, are listed in
the log as waiting for a restart.

Dependencies:
    vakr.config.project: The option catalog and each option's reload class.
    vakr.app.collector.preflight: Strict loading.

Safety:
    Messages name option keys, never their values.

Example:
    >>> restart_changes(EffectiveConfig({}), EffectiveConfig({}))
    []
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vakr.app.collector import preflight
from vakr.app.collector.derive import RateSettings
from vakr.app.collector.inventory import reload_inventory
from vakr.collectors.icmp.settings import IcmpSettings
from vakr.collectors.icmp.settings import settings_from_config as icmp_settings
from vakr.collectors.snmp.config import SnmpConfig, snmp_config
from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.domain.errors import ValidationError
from vakr.scheduler.settings import SchedulerSettings
from vakr.scheduler.settings import settings_from_config as scheduler_settings

if TYPE_CHECKING:
    from vakr.app.collector.service import CollectorService

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Reloaded:
    """A validated candidate configuration and the settings built from it.

    Attributes:
        config: The candidate.
        scheduler: Its scheduler settings (only ``defaults`` apply live).
        icmp: Its ICMP settings.
        snmp: Its SNMP settings.
        rates: Its rate limits.
        restart: Keys of changed options that need a restart.
    """

    config: EffectiveConfig
    scheduler: SchedulerSettings
    icmp: IcmpSettings
    snmp: SnmpConfig
    rates: RateSettings
    restart: list[str]


def restart_changes(old: EffectiveConfig, new: EffectiveConfig) -> list[str]:
    """List options whose value changed but apply only after a restart.

    Args:
        old: The configuration in force.
        new: The candidate.

    Returns:
        list[str]: Option keys, in catalog order.

    Raises:
        None
    """
    found = []
    for option in project.catalog().options:
        if option.reload == "restart" and old.get(option.key) != new.get(option.key):
            found.append(option.key)
    return found


def build_reload(old: EffectiveConfig, new: EffectiveConfig) -> Reloaded:
    """Build the settings a validated candidate configuration gives.

    Args:
        old: The configuration in force.
        new: The candidate, already loaded strictly.

    Returns:
        Reloaded: Settings and the restart-only changes.

    Raises:
        ValueError: If a combination of values is invalid (``ValidationError`` too).
    """
    try:
        sched, icmp, snmp = scheduler_settings(new), icmp_settings(new), snmp_config(new)
    except ValidationError as exc:
        raise ValueError(str(exc)) from None
    return Reloaded(
        new, sched, icmp, snmp, RateSettings.from_config(new), restart_changes(old, new)
    )


async def reload_service(service: CollectorService) -> bool:
    """Apply a valid candidate configuration, or keep the running one (CONF-003).

    Args:
        service: The running service.

    Returns:
        bool: True when the candidate was applied.

    Raises:
        None
    """
    try:
        new = await asyncio.to_thread(preflight.check_config, service.parts.paths.config_dir)
        loaded = build_reload(service.parts.config, new)
    except (preflight.PreflightError, ValueError) as exc:
        _LOG.error(
            "configuration reload refused, the running configuration stays: %s",
            exc,
            extra={"event_id": "collector.reload_refused"},
        )
        return False
    service.icmp_step.collector.settings = loaded.icmp
    service.snmp_step.config = service.discovery.config = loaded.snmp
    service.snmp_step.rates = loaded.rates
    service.replanner.defaults = loaded.scheduler.defaults
    service.parts.config = new
    _LOG.info(
        "configuration reloaded%s",
        f"; needs a restart: {', '.join(loaded.restart)}" if loaded.restart else "",
        extra={"event_id": "collector.reloaded"},
    )
    try:
        await asyncio.to_thread(
            reload_inventory, service.parts.inventory, new, service.parts.paths.config_dir
        )
    except Exception as exc:  # noqa: BLE001 - the running policy stays; collection goes on
        _LOG.error(
            "inventory refused the reloaded policy (%s); it keeps the previous one",
            type(exc).__name__,
            extra={"event_id": "collector.reload_inventory_failed"},
        )
    service.replanner.request()
    return True
