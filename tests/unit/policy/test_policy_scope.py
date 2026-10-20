"""Rule settings the MVP collector does not apply are named, never silently ignored (E.25 #10).

Dependencies:
    pytest; the policy parser.

Example:
    $ uv run pytest tests/unit/policy/test_policy_scope.py
"""

from vakr.policy.rules import Policy
from vakr.policy.rules_parse import parse_policy
from vakr.policy.scope import NOT_APPLIED, unapplied_settings

DOC = {
    "interface_policy": {
        "rules": [
            {"name": "quiet", "action": "exclude", "match": {"ifname": ["Null*"]}},
            {
                "name": "uplinks",
                "match": {"ifalias": ["re:.*UPLINK.*"]},
                "settings": {"polling_interval": "30s", "snmp_timeout": "3s"},
            },
        ]
    }
}


def test_option_a_every_rule_setting_is_named_with_its_device_level_alternative() -> None:
    """Option A (owner decision 2026-10-20): settings are explained but not applied.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    notes = unapplied_settings(parse_policy(DOC, "policies.toml"))
    assert notes == [
        f"interface_policy.rules[1] (uplinks): polling_interval is {NOT_APPLIED}; "
        "set `interval` on the device in inventory.toml",
        f"interface_policy.rules[1] (uplinks): snmp_timeout is {NOT_APPLIED}; the option applies",
    ]
    assert unapplied_settings(Policy()) == []
