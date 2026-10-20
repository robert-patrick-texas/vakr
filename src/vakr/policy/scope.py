"""Which parts of a collection rule the MVP collector applies (owner decision 2026-10-20).

Rules decide whether an interface is monitored and which tags and
annotations it carries, and the collector applies all of that. A rule's
*settings* (polling and discovery intervals, reachability strategy, SNMP
timeout and retries, adaptive polling, stale-after) describe per-interface
behaviour the MVP does not have: it polls whole devices, each at its own
interval from ``inventory.toml`` or the packaged ``[polling]`` and
``[snmp]`` options (Option A, Appendix E.25 #10). So that a setting never
silently does nothing, ``vakr policy validate`` warns about each one, the
collector logs it, and ``vakr policy explain`` marks it.

Dependencies:
    vakr.policy.rules, settings: The parsed rules and the setting names.

Safety:
    Pure; messages name rules and settings only.

Example:
    >>> unapplied_settings(Policy())
    []
"""

from vakr.policy.rules import Policy

NOT_APPLIED = "not applied yet: the MVP polls whole devices"
"""Why a rule setting has no effect in this release."""

DEVICE_FIELDS = {
    "polling_interval": "interval",
    "discovery_interval": "discovery_interval",
    "reachability_strategy": "reachability",
}
"""Settings a device can carry in ``inventory.toml``, and the field to use there."""


def unapplied_settings(policy: Policy) -> list[str]:
    """Describe every rule setting the MVP collector does not apply.

    Args:
        policy: The parsed collection rules.

    Returns:
        list[str]: One ``location: explanation`` line per setting, with the
        device-level alternative when one exists.

    Raises:
        None
    """
    notes: list[str] = []
    for rule in policy.rules:
        for name, _value in rule.settings:
            field = DEVICE_FIELDS.get(name)
            fix = (
                f"set `{field}` on the device in inventory.toml" if field else "the option applies"
            )
            where = f"interface_policy.rules[{rule.position - 1}] ({rule.name})"
            notes.append(f"{where}: {name} is {NOT_APPLIED}; {fix}")
    return notes
