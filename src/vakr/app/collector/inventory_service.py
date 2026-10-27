"""The collector's inventory: ``vakr.inventory.service.InventoryService`` behind the seam (6.2).

``vakr.app.collector.inventory`` defines what the collector needs from
inventory. This module supplies the real implementation: devices and poll
plans from the metadata database, classified by the collection rules in
``/etc/vakr/policies.toml`` (or no rules when the file is absent) and the
configured interface keywords (META-012). On SIGHUP the collector hands the
reloaded rules and keywords to the running service, which bumps its change
counter so the scheduler re-plans.

Dependencies:
    vakr.inventory.service: The inventory service.
    vakr.policy: Rules file and keyword loaders.

Safety:
    Reads configuration and metadata only; devices carry credential
    references, never secrets. An invalid ``policies.toml`` raises
    ``PolicyError`` at start, and on reload the caller keeps the old rules.

Example:
    >>> load_policy(Path("/nonexistent")).rules
    ()
"""

import logging
from pathlib import Path

from vakr.app.collector.inventory import InventoryContext, InventorySource
from vakr.config.loader import EffectiveConfig
from vakr.inventory.service import InventoryService
from vakr.policy.files import load_policy_file
from vakr.policy.keywords import keywords_from_config
from vakr.policy.rules import Policy
from vakr.policy.scope import unapplied_settings

_LOG = logging.getLogger(__name__)

POLICIES_FILE = "policies.toml"
"""The collection rules file in the configuration directory (11.1)."""


def load_policy(config_dir: Path) -> Policy:
    """Read the collection rules, or no rules when the file does not exist.

    Args:
        config_dir: The configuration directory.

    Returns:
        Policy: The validated rules.

    Raises:
        PolicyError: If ``policies.toml`` exists but is invalid.
    """
    path = config_dir / POLICIES_FILE
    return load_policy_file(path) if path.is_file() else Policy()


def open_service(context: InventoryContext) -> InventorySource:
    """Build the inventory service this collector polls from.

    Args:
        context: Configuration, metadata access, identity, and clock.

    Returns:
        InventorySource: An ``InventoryService`` for this collector.

    Raises:
        PolicyError: If ``policies.toml`` or the keyword settings are invalid.
    """
    policy = load_policy(context.config_dir)
    _note_unapplied(policy)
    return InventoryService(
        context.uow_factory,
        context.collector.id,
        policy,
        context.clock,
        keywords=keywords_from_config(context.config),
    )


def reload_service(inventory: InventorySource, config: EffectiveConfig, config_dir: Path) -> None:
    """Give a running service the reloaded rules and keywords.

    Args:
        inventory: The running source: an ``InventoryService``, or the
            ``PeerInventory`` wrapping one on a peer, whose ``policies.toml``
            is node-local too. Anything else (a test fake) is left as it is.
        config: The validated new configuration.
        config_dir: The configuration directory, which holds ``policies.toml``.

    Returns:
        None

    Raises:
        PolicyError: If the reloaded rules are invalid; the caller keeps the old ones.
    """
    from vakr.app.collector.replication_loop import PeerInventory

    if isinstance(inventory, (InventoryService, PeerInventory)):
        policy = load_policy(config_dir)
        _note_unapplied(policy)
        inventory.replace_policy(policy, keywords_from_config(config))


def _note_unapplied(policy: Policy) -> None:
    """Log each rule setting the MVP collector does not apply (E.25 #10).

    Args:
        policy: The rules just loaded.

    Returns:
        None

    Raises:
        None
    """
    for note in unapplied_settings(policy):
        _LOG.warning("%s", note, extra={"event_id": "collector.policy_setting_unused"})
